"""S14 done 项归档：离开 durable state 时必须交还可追溯的审计记录。"""

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


@pytest.fixture(scope="module")
def s14():
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


def _item(s14, item_id, at, status="done", pointer="transcript:demo:7"):
    reason = "等待评审" if status == "blocked" else None
    return s14.PendingItem(item_id, "任务", pointer, at, reason=reason, status=status)


def _state(s14):
    return s14.DurableContextState(pending_items=(
        _item(s14, "old-done", "2026-10-01T10:00:00+00:00"),
        _item(s14, "new-done", "2026-10-08T10:00:00+00:00"),
        _item(s14, "old-open", "2020-01-01T00:00:00+00:00", status="open"),
        _item(s14, "old-blocked", "2020-01-01T00:00:00+00:00", status="blocked"),
        _item(s14, "edge-done", "2026-10-02T10:00:00Z"),
    ))


def test_expired_done_archived_with_audit_others_kept(s14) -> None:
    state = _state(s14)
    after, records = s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)
    assert [r.item for r in records] == [state.pending_items[0], state.pending_items[4]]
    assert all(r.archived_at == NOW.isoformat() for r in records)
    assert [i.item_id for i in after.pending_items] == ["new-done", "old-open", "old-blocked"]
    assert len(state.pending_items) == 5 and after is not state


def test_audit_pointer_resolves_with_existing_resolver(s14, tmp_path) -> None:
    (tmp_path / "demo.jsonl").write_text(
        "".join(f'{{"content": "turn {n}"}}\n' for n in range(1, 9)), encoding="utf-8")
    resolver = s14.SourcePointerResolver(transcript_root=tmp_path, artifact_root=tmp_path)
    _, records = s14.archive_done_pending_items(_state(s14), now=NOW, older_than=WEEK)
    assert records[0].item.source_pointer == "transcript:demo:7"
    audit_state = s14.DurableContextState(pending_items=tuple(r.item for r in records))
    (res,) = s14.resolve_durable_sources(audit_state, resolver)
    assert (res.status, res.excerpt) == (s14.SourceResolutionStatus.AVAILABLE, "turn 7")


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
def test_bad_clock_arguments_raise_and_leave_state(s14, kwargs) -> None:
    state = _state(s14)
    before = state.pending_items
    with pytest.raises(ValueError):
        s14.archive_done_pending_items(state, **({"now": NOW, "older_than": WEEK} | kwargs))
    assert state.pending_items == before


def test_rejects_non_state_and_datetime_subclass(s14) -> None:
    class _Now(datetime):
        pass

    with pytest.raises(ValueError):
        s14.archive_done_pending_items(_state(s14).pending_items, now=NOW, older_than=WEEK)
    with pytest.raises(ValueError):
        s14.archive_done_pending_items(
            _state(s14), now=_Now(2026, 10, 9, 10, tzinfo=timezone.utc), older_than=WEEK)


def test_plus_eight_items_compare_as_absolute_instants(s14) -> None:
    # +08:00 的 10-02T17:59 = UTC 10-02T09:59 → 已满 7 天；18:01 = 10:01 UTC → 未满
    state = s14.DurableContextState(pending_items=(
        _item(s14, "due", "2026-10-02T17:59:00+08:00"),
        _item(s14, "not-due", "2026-10-02T18:01:00+08:00"),
    ))
    after, records = s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)
    assert [r.item.item_id for r in records] == ["due"]
    assert [i.item_id for i in after.pending_items] == ["not-due"]


def test_future_items_kept_and_nothing_due_returns_same_state(s14) -> None:
    state = s14.DurableContextState(pending_items=(
        _item(s14, "future", "2026-12-01T00:00:00+00:00"),
        _item(s14, "open", "2020-01-01T00:00:00+00:00", status="open"),
    ))
    after, records = s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)
    assert after is state and records == ()
    empty = s14.DurableContextState()
    assert s14.archive_done_pending_items(empty, now=NOW, older_than=WEEK) == (empty, ())


def test_audit_record_is_frozen_and_validated(s14) -> None:
    _, (record, *_) = s14.archive_done_pending_items(_state(s14), now=NOW, older_than=WEEK)
    with pytest.raises(AttributeError):
        record.archived_at = "x"  # type: ignore[misc]
    for item, at in ((_item(s14, "o", "2026-10-01T10:00:00+00:00", status="open"), NOW.isoformat()),
                     (record.item, "2026-10-09T10:00:00"),
                     (record.item, _SneakyStr(NOW.isoformat()))):
        with pytest.raises(ValueError):
            s14.ArchivedPendingItem(item, at)


def test_archive_is_not_registered_as_model_tool(s14) -> None:
    names = {tool["name"] for tool in getattr(s14, "TOOLS", [])}
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
def test_invalid_entry_at_head_or_tail_fails_whole_batch(s14, bad_at, bad) -> None:
    due = [_item(s14, "a", "2026-09-01T00:00:00Z"), _item(s14, "b", "2026-09-02T00:00:00Z")]
    entry = _item(s14, "x", "2026-09-03T00:00:00Z")
    if bad == "not-an-item":
        entry = SimpleNamespace(**vars(entry))
    elif bad == "bad-status":
        object.__setattr__(entry, "status", "archived")
    else:
        object.__setattr__(entry, "last_confirmed_at", "2026-09-03T00:00:00")
    items = list(due)
    items.insert(len(items) if bad_at == -1 else 0, entry)
    state = s14.DurableContextState(pending_items=tuple(items))
    original = state.pending_items
    with pytest.raises(ValueError):
        s14.archive_done_pending_items(
            state, now=datetime(2026, 10, 9, tzinfo=timezone.utc), older_than=timedelta(days=7))
    assert state.pending_items is original


def test_audit_construction_failure_keeps_original_state(s14, monkeypatch) -> None:
    items = (_item(s14, "a", "2026-09-01T00:00:00Z"), _item(s14, "b", "2026-09-02T00:00:00Z"))
    state = s14.DurableContextState(pending_items=items)

    def boom(item, archived_at):
        raise RuntimeError("audit boom")

    monkeypatch.setattr(s14, "ArchivedPendingItem", boom)
    with pytest.raises(RuntimeError, match="audit boom"):
        s14.archive_done_pending_items(
            state, now=datetime(2026, 10, 9, tzinfo=timezone.utc), older_than=timedelta(days=7))
    assert state.pending_items == items
    assert all(a is b for a, b in zip(state.pending_items, items))
