"""S14 PendingItem 生命周期与 done 项归档测试（原 6 个文件按段合并，段间 fixture 互相隔离）。"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s14_context_compact" / "code.py"
NOW = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
WEEK = timedelta(days=7)
T0 = "2026-10-01T09:00:00+00:00"


# ==== test_pending_lifecycle.py（PR 39）====


T1 = "2026-10-01T10:00:00+00:00"


@pytest.fixture(scope="module")
def _life_s14():
    sys.path.insert(0, str(ROOT / "tests" / "stubs"))
    saved = sys.modules.pop("anthropic", None)
    spec = importlib.util.spec_from_file_location("s14_pending_lifecycle_mod", CODE)
    sys.modules[spec.name] = module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.path.remove(str(ROOT / "tests" / "stubs"))
    sys.modules.pop("anthropic", None), sys.modules.pop(spec.name, None)
    if saved is not None:
        sys.modules["anthropic"] = saved


def _life_state(_life_s14, *items):
    return _life_s14.DurableContextState(pending_items=items)


def _open(_life_s14, item_id="ship-docs"):
    return _life_s14.PendingItem(item_id, "更新部署文档", "transcript:demo:3", T0)


def test_status_defaults_open_and_rejects_unknown(_life_s14) -> None:
    assert (_open(_life_s14).status, _open(_life_s14).reason) == ("open", None)
    with pytest.raises(ValueError):
        _life_s14.PendingItem("x", "d", "transcript:demo:1", T0, status="closed")


def test_open_to_done_returns_new_state(_life_s14) -> None:
    state = _life_state(_life_s14, _open(_life_s14))
    after = _life_s14.transition_pending_item(
        state, "ship-docs", "done", last_confirmed_at=T1, source_pointer="transcript:demo:7"
    )
    item = after.pending_items[0]
    assert (item.status, item.source_pointer, item.last_confirmed_at) == (
        "done", "transcript:demo:7", T1)
    assert state.pending_items[0].status == "open"
    assert "status=done" in _life_s14.render_durable_context(after)


def test_illegal_transitions_and_missing_fields_rejected(_life_s14) -> None:
    state = _life_state(_life_s14, _open(_life_s14))
    done = _life_s14.transition_pending_item(
        state, "ship-docs", "done", last_confirmed_at=T1, source_pointer="transcript:demo:7"
    )
    with pytest.raises(ValueError):
        _life_s14.transition_pending_item(done, "ship-docs", "open", last_confirmed_at=T1)
    with pytest.raises(KeyError):
        _life_s14.transition_pending_item(state, "nope", "done", last_confirmed_at=T1,
                                    source_pointer="transcript:demo:7")
    for bad in ("", "2026-10-01T10:00:00"):
        with pytest.raises(ValueError):
            _life_s14.transition_pending_item(state, "ship-docs", "done", last_confirmed_at=bad,
                                        source_pointer="transcript:demo:7")
    for status, extra in (("done", {}), ("blocked", {"source_pointer": "transcript:demo:5"})):
        with pytest.raises(ValueError):
            _life_s14.transition_pending_item(state, "ship-docs", status, last_confirmed_at=T1, **extra)


def test_blocked_renders_escaped_reason_and_clears_on_reopen(_life_s14) -> None:
    state = _life_state(_life_s14, _open(_life_s14))
    blocked = _life_s14.transition_pending_item(state, "ship-docs", "blocked", last_confirmed_at=T1,
                                          source_pointer="transcript:demo:5",
                                          reason="等待审批\n- injected: done")
    rendered = _life_s14.render_durable_context(blocked)
    assert "status=blocked; reason=等待审批" in rendered
    assert "\n- injected" not in rendered
    reopened = _life_s14.transition_pending_item(
        blocked, "ship-docs", "open", last_confirmed_at="2026-10-01T11:00:00+00:00")
    assert reopened.pending_items[0].reason is None
    assert reopened.pending_items[0].source_pointer == "transcript:demo:5"


def test_summary_saying_done_does_not_change_status(_life_s14, monkeypatch) -> None:
    monkeypatch.setattr(_life_s14, "TOKEN_THRESHOLD", 1)
    monkeypatch.setattr(_life_s14, "KEEP_RECENT_TURNS", 20)
    state = _life_state(_life_s14, _open(_life_s14))
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " * 50}
                for i in range(10)]
    result = _life_s14.compact_context(
        messages, state, summarizer=lambda _: "migration is done", verbose=False)
    assert "migration is done" in str(result.messages) and result.durable_state.pending_items[0].status == "open"


def test_done_rendering_is_bounded(_life_s14) -> None:
    def build(n_done):
        items = [_life_s14.PendingItem(f"open-{i}", "todo", f"transcript:s:{i}", T0) for i in range(2)]
        items += [
            _life_s14.PendingItem(f"done-{i}", "fin", f"transcript:s:{100 + i}",
                            f"2026-10-01T{i // 60:02d}:{i % 60:02d}:00+08:00", status="done")
            for i in range(n_done)
        ]
        return _life_state(_life_s14, *items)

    big = _life_s14.render_durable_context(build(200))
    assert "done_omitted=197" in big
    assert all(f"done-{i}:" in big for i in (197, 198, 199))
    assert "done-196:" not in big and len(build(200).pending_items) == 202
    assert "open-0:" in big and "open-1:" in big and abs(len(big) - len(_life_s14.render_durable_context(build(51)))) <= 10
    messages = [{"role": "user", "content": "hello " * 100}]
    results = [_life_s14.compact_context(messages, build(n), verbose=False) for n in (0, 200)]
    assert results[0].tokens_after == results[1].tokens_after


def test_repl_pending_demo_prints_four_locked_lines() -> None:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"} | {"MODEL_ID": "offline"}
    proc = subprocess.run([sys.executable, str(CODE)], input="/pending-demo\nq\n",
                          capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
    assert proc.returncode == 0, proc.stderr
    lines = [line.split("\x1b[0m")[-1] for line in proc.stdout.splitlines()]
    expected = [
        "[before] - ship-docs: 更新部署文档 (status=open; source=transcript:demo:3; "
        "confirmed=2026-10-01T09:00:00+00:00)",
        "[harness] ship-docs: open -> done",
        "[after]  - ship-docs: 更新部署文档 (status=done; source=transcript:demo:7; "
        "confirmed=2026-10-01T10:00:00+00:00)",
        "[harness] ship-docs: done -> open rejected",
    ]
    start = next(i for i, line in enumerate(lines) if line.startswith("[before]"))
    assert lines[start:start + 4] == expected


class _EvilId(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__

    def __bool__(self):
        return False


def test_transition_rejects_non_plain_inputs(_life_s14) -> None:
    state = _life_state(_life_s14, _open(_life_s14))
    ok = {"last_confirmed_at": T1, "source_pointer": "transcript:demo:5"}
    # str 子类 id 在入口被拒（ValueError），绝不会匹配任何条目
    for bad_id in (_EvilId("nope"), _EvilId("ship-docs"), 1, None):
        with pytest.raises(ValueError):
            _life_s14.transition_pending_item(state, bad_id, "done", **ok)
    for bad_status in (1, _EvilId("done"), None):
        with pytest.raises(ValueError):
            _life_s14.transition_pending_item(state, "ship-docs", bad_status, **ok)
    with pytest.raises(ValueError):
        _life_s14.transition_pending_item(state, "ship-docs", "blocked", reason=123, **ok)
    assert state.pending_items[0].status == "open"


def test_plain_normalized_id_and_enum_status_still_match(_life_s14) -> None:
    state = _life_state(_life_s14, _open(_life_s14))
    after = _life_s14.transition_pending_item(state, "  ship-docs ", _life_s14.PendingStatus.BLOCKED,
                                        last_confirmed_at=T1, source_pointer="transcript:demo:5",
                                        reason="等待")
    assert (after.pending_items[0].status, after.pending_items[0].reason) == ("blocked", "等待")


# ==== test_pending_lifecycle_adversarial.py（PR 39）====


@pytest.fixture(scope="module")
def _life_adv_s14():
    stub_path = str(ROOT / "tests" / "stubs")
    sys.path.insert(0, stub_path)
    saved = sys.modules.pop("anthropic", None)
    spec = importlib.util.spec_from_file_location(
        "s14_pending_lifecycle_adversarial_mod", CODE
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(stub_path)
        sys.modules.pop("anthropic", None)
        sys.modules.pop(spec.name, None)
        if saved is not None:
            sys.modules["anthropic"] = saved


@pytest.mark.parametrize(
    "confirmed",
    [
        T0,
        "2026-10-01T08:59:59+00:00",
        "2026-10-01T17:00:00+08:00",  # 与 T0 是同一时刻
    ],
    ids=["replayed", "older", "same-instant-other-zone"],
)
def test_transition_requires_fresh_confirmation(_life_adv_s14, confirmed):
    item = _life_adv_s14.PendingItem("task", "发布", "transcript:demo:3", T0)
    state = _life_adv_s14.DurableContextState(pending_items=(item,))
    with pytest.raises(ValueError):
        _life_adv_s14.transition_pending_item(
            state,
            "task",
            "done",
            last_confirmed_at=confirmed,
            source_pointer="transcript:demo:7",
        )
    assert state.pending_items == (item,)
    assert (item.status, item.last_confirmed_at) == ("open", T0)


class _SpoofedStatus(str):
    __hash__ = None

    def __eq__(self, other):
        return True


def test_constructor_rejects_unknown_status_with_spoofed_equality(_life_adv_s14):
    # 攻击构造器，而非已有测试覆盖的 transition_pending_item 入口。
    status = _SpoofedStatus("closed")
    assert str(status) == "closed"
    with pytest.raises(ValueError):
        _life_adv_s14.PendingItem("task", "发布", "transcript:demo:3", T0, status=status)


def test_done_selection_uses_instants_and_keeps_active_items(_life_adv_s14):
    # old-local 的日期字符串最大，但真实时刻最早，必须被折叠。
    done_times = (
        ("old-local", "2026-10-02T00:00:00+14:00"),  # UTC 10:00
        ("new-a", "2026-10-01T11:00:00Z"),
        ("new-b", "2026-10-01T07:00:00-05:00"),     # UTC 12:00
        ("new-c", "2026-10-01T22:00:00+09:00"),     # UTC 13:00
    )
    items = (
        _life_adv_s14.PendingItem("active", "继续", "transcript:demo:1", T0),
        _life_adv_s14.PendingItem(
            "blocked", "等待", "transcript:demo:2", T0,
            reason="等待审批", status="blocked",
        ),
    ) + tuple(
        _life_adv_s14.PendingItem(
            item_id, "已完成", f"transcript:demo:{index}", confirmed,
            status="done",
        )
        for index, (item_id, confirmed) in enumerate(done_times, start=3)
    )
    state = _life_adv_s14.DurableContextState(pending_items=items)
    rendered = _life_adv_s14.render_durable_context(state)
    pending_lines = [
        line for line in rendered.splitlines() if line.startswith("- ")
    ]
    shown_ids = {
        line[2:].split(":", 1)[0]
        for line in pending_lines
        if ": " in line
    }
    assert shown_ids == {"active", "blocked", "new-a", "new-b", "new-c"}
    assert pending_lines.count("- done_omitted=1") == 1
    assert sum("(status=done;" in line for line in pending_lines) == 3
    assert "status=blocked; reason=等待审批" in rendered
    assert state.pending_items == items
    assert len(state.pending_items) == 6


# ==== test_s14_pending_lifecycle_adversarial_r2.py（PR 39）====


LIFE_ADV_R2_T0 = "2026-10-01T09:00:00Z"


@pytest.fixture(scope="module")
def _life_adv_r2_s14():
    """用独立模块名和离线替身导入章节，最终恢复环境与模块。"""
    stub_dir = str(ROOT / "tests" / "stubs")
    saved_environ = os.environ.copy()
    saved_path = sys.path[:]
    saved_anthropic = sys.modules.pop("anthropic", None)
    name = "s14_pending_lifecycle_adversarial_r2_module"
    sys.path.insert(0, stub_dir)
    os.environ["MODEL_ID"] = "offline-test-model"
    try:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "s14_context_compact" / "code.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = saved_path
        sys.modules.pop(name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        os.environ.clear()
        os.environ.update(saved_environ)


def test_lifecycle_chain_uses_latest_confirmation_and_preserves_other_items(_life_adv_r2_s14):
    """攻击只比较初始时间、遗漏原因清空或误改其他条目的迁移实现。"""
    target = _life_adv_r2_s14.PendingItem("task", "发布", "transcript:demo:1", LIFE_ADV_R2_T0)
    other = _life_adv_r2_s14.PendingItem("other", "保留", "transcript:demo:2", LIFE_ADV_R2_T0)
    state = _life_adv_r2_s14.DurableContextState(pending_items=(target, other))
    blocked = _life_adv_r2_s14.transition_pending_item(
        state,
        "task",
        "blocked",
        last_confirmed_at="2026-10-01T18:00:00+08:00",
        source_pointer="transcript:demo:3",
        reason="等待审批",
    )

    # 此时刻晚于初始值，却早于最近一次确认，仍然必须拒绝。
    with pytest.raises(ValueError):
        _life_adv_r2_s14.transition_pending_item(
            blocked,
            "task",
            "open",
            last_confirmed_at="2026-10-01T09:30:00Z",
        )

    reopened = _life_adv_r2_s14.transition_pending_item(
        blocked,
        "task",
        "open",
        last_confirmed_at="2026-10-01T11:00:00Z",
    )
    done = _life_adv_r2_s14.transition_pending_item(
        reopened,
        "task",
        "done",
        last_confirmed_at="2026-10-01T12:00:00Z",
        source_pointer="transcript:demo:4",
    )

    assert tuple(
        snapshot.pending_items[0].status
        for snapshot in (state, blocked, reopened, done)
    ) == ("open", "blocked", "open", "done")
    assert blocked.pending_items[0].reason == "等待审批"
    assert reopened.pending_items[0].reason is None
    assert reopened.pending_items[0].source_pointer == "transcript:demo:3"
    assert (
        done.pending_items[0].reason,
        done.pending_items[0].source_pointer,
        done.pending_items[0].last_confirmed_at,
    ) == (None, "transcript:demo:4", "2026-10-01T12:00:00Z")
    assert state.pending_items == (target, other)
    assert all(
        snapshot.pending_items[1] is other
        for snapshot in (blocked, reopened, done)
    )


def test_done_growth_does_not_change_hard_limit_rejection(_life_adv_r2_s14, monkeypatch):
    """攻击把 durable 项计入消息上限，或因 done 折叠而漏掉上限拒绝的实现。"""
    monkeypatch.setattr(_life_adv_r2_s14, "TOKEN_THRESHOLD", 1)
    monkeypatch.setattr(_life_adv_r2_s14, "HARD_LIMIT", 10)
    messages = [{"role": "user", "content": "x" * 200}]

    for count in (0, 200):
        items = tuple(
            _life_adv_r2_s14.PendingItem(
                f"done-{index}",
                "完成",
                f"transcript:demo:{index + 1}",
                LIFE_ADV_R2_T0,
                status="done",
            )
            for index in range(count)
        )
        state = _life_adv_r2_s14.DurableContextState(pending_items=items)
        with pytest.raises(_life_adv_r2_s14.MessageViewLimitExceeded) as caught:
            _life_adv_r2_s14.compact_context(
                messages,
                state,
                summarizer=lambda _: "摘要",
                verbose=False,
            )

        # 单条文本为 200 / 4，加消息角色开销 4；单条消息不能被摘要缩减。
        assert (
            caught.value.tokens_before,
            caught.value.tokens_after,
            caught.value.hard_limit,
        ) == (54, 54, 10)
        assert state.pending_items == items
        assert len(state.pending_items) == count

    assert messages == [{"role": "user", "content": "x" * 200}]


# ==== test_pending_archive.py（PR 43）====


@pytest.fixture(scope="module")
def _archive_s14():
    sys.path.insert(0, str(ROOT / "tests" / "stubs"))
    saved = sys.modules.pop("anthropic", None)
    spec = importlib.util.spec_from_file_location("s14_pending_archive_mod", CODE)
    sys.modules[spec.name] = module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.path.remove(str(ROOT / "tests" / "stubs"))
    sys.modules.pop("anthropic", None), sys.modules.pop(spec.name, None)
    if saved is not None:
        sys.modules["anthropic"] = saved


def _item(_archive_s14, item_id, at, status="done", pointer="transcript:demo:7"):
    reason = "等待评审" if status == "blocked" else None
    return _archive_s14.PendingItem(item_id, "任务", pointer, at, reason=reason, status=status)


def _archive_state(_archive_s14):
    return _archive_s14.DurableContextState(pending_items=(
        _item(_archive_s14, "old-done", "2026-10-01T10:00:00+00:00"),
        _item(_archive_s14, "new-done", "2026-10-08T10:00:00+00:00"),
        _item(_archive_s14, "old-open", "2020-01-01T00:00:00+00:00", status="open"),
        _item(_archive_s14, "old-blocked", "2020-01-01T00:00:00+00:00", status="blocked"),
        _item(_archive_s14, "edge-done", "2026-10-02T10:00:00Z"),
    ))


def test_expired_done_archived_with_audit_others_kept(_archive_s14) -> None:
    state = _archive_state(_archive_s14)
    after, records = _archive_s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)
    assert [r.item for r in records] == [state.pending_items[0], state.pending_items[4]]
    assert all(r.archived_at == NOW.isoformat() for r in records)
    assert [i.item_id for i in after.pending_items] == ["new-done", "old-open", "old-blocked"]
    assert len(state.pending_items) == 5 and after is not state


def test_audit_pointer_resolves_with_existing_resolver(_archive_s14, tmp_path) -> None:
    (tmp_path / "demo.jsonl").write_text(
        "".join(f'{{"content": "turn {n}"}}\n' for n in range(1, 9)), encoding="utf-8")
    resolver = _archive_s14.SourcePointerResolver(transcript_root=tmp_path, artifact_root=tmp_path)
    _, records = _archive_s14.archive_done_pending_items(_archive_state(_archive_s14), now=NOW, older_than=WEEK)
    assert records[0].item.source_pointer == "transcript:demo:7"
    audit_state = _archive_s14.DurableContextState(pending_items=tuple(r.item for r in records))
    (res,) = _archive_s14.resolve_durable_sources(audit_state, resolver)
    assert (res.status, res.excerpt) == (_archive_s14.SourceResolutionStatus.AVAILABLE, "turn 7")


class _SneakyStr(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


@pytest.mark.parametrize("kwargs", [
    {"now": datetime(2026, 10, 9, 10)},
    {"now": datetime(2026, 10, 9, 18, tzinfo=timezone(timedelta(hours=8)))},
    {"now": "2026-10-09T10:00:00+00:00"},
    {"now": _SneakyStr("2026-10-09T10:00:00+00:00")},
    {"now": 1_791_540_000},
    {"now": None},
    {"older_than": timedelta(0)},
    {"older_than": timedelta(seconds=-1)},
    {"older_than": 7},
    {"older_than": None},
])
def test_bad_clock_arguments_raise_and_leave_state(_archive_s14, kwargs) -> None:
    state = _archive_state(_archive_s14)
    before = state.pending_items
    with pytest.raises(ValueError):
        _archive_s14.archive_done_pending_items(state, **({"now": NOW, "older_than": WEEK} | kwargs))
    assert state.pending_items == before


def test_rejects_non_state_and_datetime_subclass(_archive_s14) -> None:
    class _Now(datetime):
        pass

    with pytest.raises(ValueError):
        _archive_s14.archive_done_pending_items(_archive_state(_archive_s14).pending_items, now=NOW, older_than=WEEK)
    with pytest.raises(ValueError):
        _archive_s14.archive_done_pending_items(
            _archive_state(_archive_s14), now=_Now(2026, 10, 9, 10, tzinfo=timezone.utc), older_than=WEEK)


def test_plus_eight_items_compare_as_absolute_instants(_archive_s14) -> None:
    # +08:00 的 10-02T17:59 = UTC 10-02T09:59 → 已满 7 天；18:01 = 10:01 UTC → 未满
    state = _archive_s14.DurableContextState(pending_items=(
        _item(_archive_s14, "due", "2026-10-02T17:59:00+08:00"),
        _item(_archive_s14, "not-due", "2026-10-02T18:01:00+08:00"),
    ))
    after, records = _archive_s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)
    assert [r.item.item_id for r in records] == ["due"]
    assert [i.item_id for i in after.pending_items] == ["not-due"]


def test_future_items_kept_and_nothing_due_returns_same_state(_archive_s14) -> None:
    state = _archive_s14.DurableContextState(pending_items=(
        _item(_archive_s14, "future", "2026-12-01T00:00:00+00:00"),
        _item(_archive_s14, "open", "2020-01-01T00:00:00+00:00", status="open"),
    ))
    after, records = _archive_s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)
    assert after is state and records == ()
    empty = _archive_s14.DurableContextState()
    assert _archive_s14.archive_done_pending_items(empty, now=NOW, older_than=WEEK) == (empty, ())


def test_audit_record_is_frozen_and_validated(_archive_s14) -> None:
    _, (record, *_) = _archive_s14.archive_done_pending_items(_archive_state(_archive_s14), now=NOW, older_than=WEEK)
    with pytest.raises(AttributeError):
        record.archived_at = "x"  # type: ignore[misc]
    for item, at in ((_item(_archive_s14, "o", "2026-10-01T10:00:00+00:00", status="open"), NOW.isoformat()),
                     (record.item, "2026-10-09T10:00:00"),
                     (record.item, _SneakyStr(NOW.isoformat()))):
        with pytest.raises(ValueError):
            _archive_s14.ArchivedPendingItem(item, at)


def test_archive_is_not_registered_as_model_tool(_archive_s14) -> None:
    names = {tool["name"] for tool in getattr(_archive_s14, "TOOLS", [])}
    assert not any("archive" in name or "pending" in name for name in names)


def test_repl_pending_demo_appends_archive_lines() -> None:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"} | {"MODEL_ID": "offline"}
    proc = subprocess.run([sys.executable, str(CODE)], input="/pending-demo\nq\n",
                          capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
    assert proc.returncode == 0, proc.stderr
    lines = [line.split("\x1b[0m")[-1] for line in proc.stdout.splitlines()]
    start = next(i for i, line in enumerate(lines) if line.startswith("[harness] ship-docs: done"))
    assert lines[start + 1:start + 3] == [
        "[archive] ship-docs archived_at=2026-10-09T10:00:00+00:00 source=transcript:demo:7",
        "[after-archive] (no pending work)",
    ]


@pytest.mark.parametrize("bad_at", [0, -1])
@pytest.mark.parametrize("bad", ["not-an-item", "bad-status", "naive-time"])
def test_invalid_entry_at_head_or_tail_fails_whole_batch(_archive_s14, bad_at, bad) -> None:
    due = [_item(_archive_s14, "a", "2026-09-01T00:00:00Z"), _item(_archive_s14, "b", "2026-09-02T00:00:00Z")]
    entry = _item(_archive_s14, "x", "2026-09-03T00:00:00Z")
    if bad == "not-an-item":
        entry = SimpleNamespace(**vars(entry))
    elif bad == "bad-status":
        object.__setattr__(entry, "status", "archived")
    else:
        object.__setattr__(entry, "last_confirmed_at", "2026-09-03T00:00:00")
    items = list(due)
    items.insert(len(items) if bad_at == -1 else 0, entry)
    state = _archive_s14.DurableContextState(pending_items=tuple(items))
    original = state.pending_items
    with pytest.raises(ValueError):
        _archive_s14.archive_done_pending_items(
            state, now=datetime(2026, 10, 9, tzinfo=timezone.utc), older_than=timedelta(days=7))
    assert state.pending_items is original


def test_audit_construction_failure_keeps_original_state(_archive_s14, monkeypatch) -> None:
    items = (_item(_archive_s14, "a", "2026-09-01T00:00:00Z"), _item(_archive_s14, "b", "2026-09-02T00:00:00Z"))
    state = _archive_s14.DurableContextState(pending_items=items)

    def boom(item, archived_at):
        raise RuntimeError("audit boom")

    monkeypatch.setattr(_archive_s14, "ArchivedPendingItem", boom)
    with pytest.raises(RuntimeError, match="audit boom"):
        _archive_s14.archive_done_pending_items(
            state, now=datetime(2026, 10, 9, tzinfo=timezone.utc), older_than=timedelta(days=7))
    assert state.pending_items == items
    assert all(a is b for a, b in zip(state.pending_items, items))


# ==== test_s14_adversarial.py（PR 43）====


@pytest.fixture(scope="module")
def _adv_s14():
    saved_path = sys.path[:]
    saved_environ = os.environ.copy()
    saved_anthropic = sys.modules.pop("anthropic", None)
    name = "s14_adversarial_module"
    sys.path.insert(0, str(ROOT / "tests" / "stubs"))
    os.environ["MODEL_ID"] = "offline-test-model"
    try:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "s14_context_compact" / "code.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = saved_path
        sys.modules.pop(name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        os.environ.clear()
        os.environ.update(saved_environ)


def item(_adv_s14, name, at):
    return _adv_s14.PendingItem(
        name, "confirmed work", f"transcript:demo:{name}",
        at, status="done",
    )


@pytest.mark.parametrize("status", ["invalid", "open"])
@pytest.mark.parametrize("bad_first", [False, True])
def test_malformed_batch_must_fail_before_any_archive(_adv_s14, status, bad_first):
    due = item(_adv_s14, "1", "2026-10-01T10:00:00Z")
    # Public state construction accepts this object; no frozen-state bypass.
    bad = SimpleNamespace(
        item_id="bad",
        description="malformed work",
        source_pointer="transcript:demo:2",
        last_confirmed_at="not-a-timestamp",
        status=status,
        reason=None,
    )
    items = (bad, due) if bad_first else (due, bad)
    state = _adv_s14.DurableContextState(pending_items=items)
    original = state.pending_items

    with pytest.raises(ValueError):
        _adv_s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)

    assert state.pending_items is original
    assert all(a is b for a, b in zip(state.pending_items, items))


def test_microsecond_boundary_order_identity_and_repeat(_adv_s14):
    due = item(_adv_s14, "1", "2026-10-02T18:00:00+08:00")
    young = item(_adv_s14, "2", "2026-10-02T18:00:00.000001+08:00")
    older = item(_adv_s14, "3", "2026-10-02T09:59:59.999999Z")
    fact = _adv_s14.DurableFact(
        "fact", "keep evidence", "transcript:demo:4",
        "2026-10-01T00:00:00Z",
    )
    state = _adv_s14.DurableContextState(
        facts=(fact,), pending_items=(due, young, older)
    )

    after, records = _adv_s14.archive_done_pending_items(
        state, now=NOW, older_than=WEEK
    )

    assert tuple(r.item for r in records) == (due, older)
    assert records[0].item is due and records[1].item is older
    assert all(r.archived_at == NOW.isoformat() for r in records)
    assert after.pending_items == (young,)
    assert after.pending_items[0] is young
    assert after.facts[0] is fact
    assert state.pending_items == (due, young, older)

    repeated, again = _adv_s14.archive_done_pending_items(
        after, now=NOW, older_than=WEEK
    )
    assert repeated is after
    assert again == ()


def test_second_audit_failure_leaves_entire_input_intact(_adv_s14, monkeypatch):
    first = item(_adv_s14, "1", "2026-10-01T10:00:00Z")
    second = item(_adv_s14, "2", "2026-10-01T11:00:00Z")
    state = _adv_s14.DurableContextState(pending_items=(first, second))
    original = state.pending_items
    constructor = _adv_s14.ArchivedPendingItem
    attempted = []

    def fail_second(pending, archived_at):
        attempted.append(pending)
        if pending is second:
            raise RuntimeError("audit construction failed")
        return constructor(pending, archived_at)

    monkeypatch.setattr(_adv_s14, "ArchivedPendingItem", fail_second)
    with pytest.raises(RuntimeError, match="audit construction failed"):
        _adv_s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)

    assert attempted == [first, second]
    assert state.pending_items is original
    assert state.pending_items[0] is first
    assert state.pending_items[1] is second

    monkeypatch.setattr(_adv_s14, "ArchivedPendingItem", constructor)
    after, records = _adv_s14.archive_done_pending_items(
        state, now=NOW, older_than=WEEK
    )
    assert after.pending_items == ()
    assert tuple(r.item for r in records) == (first, second)


# ==== test_s14_adversarial_r2.py（PR 43）====


@pytest.fixture(scope="module")
def _adv_r2_s14():
    saved_path = sys.path[:]
    saved_env = os.environ.copy()
    saved_anthropic = sys.modules.pop("anthropic", None)
    name = "s14_adversarial_r2_module"
    sys.path.insert(0, str(ROOT / "tests" / "stubs"))
    os.environ["MODEL_ID"] = "offline-test-model"
    try:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "s14_context_compact" / "code.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = saved_path
        sys.modules.pop(name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        os.environ.clear()
        os.environ.update(saved_env)


@pytest.mark.parametrize("status", ["open", "blocked", "done"])
@pytest.mark.parametrize("field,value", [
    ("status", "invalid"),
    ("last_confirmed_at", "bad"),
    ("last_confirmed_at", "2026-10-01T00:00:00"),
])
def test_no_due_batch_still_validates_every_entry(
    _adv_r2_s14, monkeypatch, status, field, value
):
    bad = _adv_r2_s14.PendingItem(
        "bad", "work", "transcript:demo:1", NOW.isoformat(),
        status=status, reason="waiting" if status == "blocked" else None,
    )
    # Exercise defensive validation of an exact PendingItem, not a duck type.
    object.__setattr__(bad, field, value)
    state = _adv_r2_s14.DurableContextState(pending_items=(bad,))
    original = state.pending_items
    attempts = []

    def unexpected_archive(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("validation must precede audit construction")

    monkeypatch.setattr(_adv_r2_s14, "ArchivedPendingItem", unexpected_archive)
    with pytest.raises(ValueError):
        _adv_r2_s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)

    assert attempts == []
    assert state.pending_items is original
    assert state.pending_items[0] is bad
    assert getattr(bad, field) == value


def test_state_construction_failure_preserves_input_and_allows_retry(
    _adv_r2_s14, monkeypatch
):
    due = _adv_r2_s14.PendingItem(
        "due", "work", "transcript:demo:1",
        "2026-10-01T00:00:00Z", status="done",
    )
    state = _adv_r2_s14.DurableContextState(pending_items=(due,))
    original = state.pending_items
    audited = []
    replacements = []
    constructor = _adv_r2_s14.ArchivedPendingItem

    def record_audit(item, archived_at):
        record = constructor(item, archived_at)
        audited.append(record)
        return record

    def fail_replace(obj, **changes):
        replacements.append((obj, changes))
        raise RuntimeError("state construction failed")

    with monkeypatch.context() as patch:
        patch.setattr(_adv_r2_s14, "ArchivedPendingItem", record_audit)
        patch.setattr(_adv_r2_s14, "replace", fail_replace)
        with pytest.raises(RuntimeError, match="state construction failed"):
            _adv_r2_s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)

    assert len(audited) == 1
    assert audited[0].item is due
    assert audited[0].archived_at == NOW.isoformat()
    assert len(replacements) == 1
    assert replacements[0][0] is state
    assert replacements[0][1] == {"pending_items": ()}
    assert state.pending_items is original
    assert state.pending_items[0] is due

    after, records = _adv_r2_s14.archive_done_pending_items(
        state, now=NOW, older_than=WEEK
    )
    assert after.pending_items == ()
    assert len(records) == 1
    assert records[0].item is due
    assert state.pending_items is original


# 合并对抗：守住六段 fixture 的独立命名和 module scope。
def test_adversarial_fixture_registration_stays_isolated():
    import ast
    from collections import Counter
    from pathlib import Path

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    expected = {
        "_life_s14",
        "_life_adv_s14",
        "_life_adv_r2_s14",
        "_archive_s14",
        "_adv_s14",
        "_adv_r2_s14",
    }
    definitions = Counter(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    )
    assert "s14" not in definitions

    expected_decorator = ast.parse(
        '@pytest.fixture(scope="module")\ndef fixture(): pass'
    ).body[0].decorator_list[0]

    for name in sorted(expected):
        assert definitions[name] == 1, name
        fixture = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        assert len(fixture.decorator_list) == 1, name
        assert ast.dump(fixture.decorator_list[0]) == ast.dump(
            expected_decorator
        ), name
