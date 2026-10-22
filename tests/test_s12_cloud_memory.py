"""s12_cloud_memory 合并测试：8 个原文件按段原样搬入。

每段以 `# ==== 原文件名（来源 PR）====` 开头；新用例只能追加在对应段末。
同名 fixture / helper 已加段前缀隔离（`_adv_store` / `_retr_store` / `_id_store` /
`_tool_store` / `_tool_adv_store` / `s12_retract_demo` 等），不得用 `name=` 别名改回。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_remote_memory import s12  # noqa: F401  复用 s12 模块加载 fixture


# ==== test_s12_adversarial.py（来源 PR 42）====


def _adv_source(s12):
    return s12.MemorySource(
        source_id="adversarial",
        source_type="transcript",
        title="Adversarial evidence",
        captured_at="2026-08-01T12:00:00+00:00",
    )


def _adv_add(s12, store, memory_id):
    return store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id=memory_id,
        content="python deployment evidence",
        summary="python deployment evidence",
        source=_adv_source(s12),
        stored_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
    )


def _rid(target):
    return "retraction-" + hashlib.sha256(target.encode("utf-8")).hexdigest()[:24]


@pytest.fixture
def _adv_store(s12, tmp_path):
    return s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="u")


def test_concurrent_retractions_return_one_durable_event(s12, _adv_store):
    _adv_add(s12, _adv_store, "target")
    barrier = threading.Barrier(4)

    def retract(index):
        writer = s12.RemoteMemoryStore(_adv_store.path, user_id="u")
        barrier.wait(timeout=10)
        return writer.retract(
            "target", reason=f"request {index}", source=_adv_source(s12)
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(retract, range(4)))

    assert all(record == results[0] for record in results)
    assert results[0].memory_id == _rid("target")
    assert len(_adv_store.read_all()) == 2
    assert _adv_store.active_records() == []
    before = _adv_store.path.read_bytes()
    assert s12.RecallEngine(_adv_store).recall("python deployment").hits == ()
    assert _adv_store.path.read_bytes() == before


def test_legacy_integer_id_retraction_does_not_corrupt_store(s12, _adv_store):
    # Allow a future fix to reject integer ids; do not lock in acceptance.
    try:
        _adv_add(s12, _adv_store, 123)
    except s12.RemoteMemoryValidationError:
        assert not _adv_store.path.exists() or _adv_store.path.read_bytes() == b""
        return

    assert [r.memory_id for r in _adv_store.active_records()] == ["123"]
    before = _adv_store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryValidationError):
        _adv_store.retract(123, reason="forget", source=_adv_source(s12))
    assert _adv_store.path.read_bytes() == before

    event = _adv_store.retract("123", reason="forget", source=_adv_source(s12))
    assert event.memory_id == _rid("123")
    reopened = s12.RemoteMemoryStore(_adv_store.path, user_id="u")
    assert [r.memory_id for r in reopened.read_all()] == ["123", event.memory_id]
    assert reopened.active_records() == []
    before = _adv_store.path.read_bytes()
    assert reopened.retract(
        "123", reason="retry", source=_adv_source(s12)
    ) == event
    assert _adv_store.path.read_bytes() == before
    _adv_add(s12, reopened, "survivor")
    assert [r.memory_id for r in reopened.active_records()] == ["survivor"]


@pytest.mark.parametrize("separator", ["\x85", "\u2028", "\u2029"])
def test_separator_ids_stay_distinct_and_round_trip(s12, _adv_store, separator):
    target = f"mem{separator}id"
    _adv_add(s12, _adv_store, target)
    _adv_add(s12, _adv_store, "memid")
    event = _adv_store.retract(target, reason="forget", source=_adv_source(s12))
    assert event.memory_id == _rid(target)

    reopened = s12.RemoteMemoryStore(_adv_store.path, user_id="u")
    audit = reopened.read_all()
    assert [r.memory_id for r in audit] == [target, "memid", _rid(target)]
    assert audit[-1].retracts == target
    assert [r.memory_id for r in reopened.active_records()] == ["memid"]
    assert [
        hit.memory_id
        for hit in s12.RecallEngine(reopened).recall("python deployment").hits
    ] == ["memid"]

    before = _adv_store.path.read_bytes()
    for bad in (separator + "memid", "memid" + separator):
        with pytest.raises(s12.RemoteMemoryValidationError):
            reopened.retract(bad, reason="forget", source=_adv_source(s12))
        assert _adv_store.path.read_bytes() == before


@pytest.mark.parametrize("field", ["memory_id", "retracts"])
@pytest.mark.parametrize(
    "bad",
    [None, True, 123, [], {}, "", " target", "target ",
     "\x85target", "target\u2029", "x" * 201],
)
def test_raw_retraction_types_and_boundaries_are_corruption(
    s12, _adv_store, field, bad
):
    _adv_add(s12, _adv_store, "target")
    _adv_store.retract("target", reason="forget", source=_adv_source(s12))
    rows = [json.loads(line) for line in _adv_store.path.read_bytes().split(b"\n") if line]
    rows[-1][field] = bad
    _adv_store.path.write_bytes(
        b"".join(
            json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n"
            for row in rows
        )
    )
    before = _adv_store.path.read_bytes()
    for read in (
        _adv_store.active_records,
        _adv_store.latest_profile,
        lambda: s12.RecallEngine(_adv_store).recall("python"),
    ):
        with pytest.raises(s12.RemoteMemoryCorruptionError):
            read()
        assert _adv_store.path.read_bytes() == before


def test_duplicate_retraction_event_is_corruption(s12, _adv_store):
    _adv_add(s12, _adv_store, "target")
    _adv_store.retract("target", reason="forget", source=_adv_source(s12))
    event_line = _adv_store.path.read_bytes().split(b"\n")[-2]
    with _adv_store.path.open("ab") as stream:
        stream.write(event_line + b"\n")
    before = _adv_store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryCorruptionError):
        _adv_store.active_records()
    assert _adv_store.path.read_bytes() == before


def test_correctly_derived_event_cannot_target_another_retraction(s12, _adv_store):
    _adv_add(s12, _adv_store, "target")
    first = _adv_store.retract("target", reason="forget", source=_adv_source(s12))
    forged = first.to_dict()
    forged.update(memory_id=_rid(first.memory_id), retracts=first.memory_id)
    with _adv_store.path.open("ab") as stream:
        stream.write(json.dumps(forged).encode("utf-8") + b"\n")
    before = _adv_store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryCorruptionError):
        _adv_store.active_records()
    assert _adv_store.path.read_bytes() == before


def test_plain_record_keeps_legacy_serialization_bytes(s12, _adv_store):
    record = _adv_add(s12, _adv_store, "legacy")
    legacy = {
        "memory_id": record.memory_id,
        "user_scope": record.user_scope,
        "kind": record.kind.value,
        "content": record.content,
        "summary": record.summary,
        "source": {
            "source_id": record.source.source_id,
            "source_type": record.source.source_type,
            "title": record.source.title,
            "captured_at": record.source.captured_at,
        },
        "stored_at": record.stored_at,
        "schema_version": record.schema_version,
    }
    expected = (
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    assert _adv_store.path.read_bytes() == expected
    reopened = s12.RemoteMemoryStore(_adv_store.path, user_id="u")
    assert reopened.read_all()[0].retracts is None
    assert reopened.active_records() == reopened.read_all()



# ==== test_s12_memory_retraction.py（来源 PR 42）====


def _retr_src(s12, day: int = 1):
    at = s12._iso(datetime(2026, 8, day, 12, tzinfo=timezone.utc))
    return s12.MemorySource(source_id=f"s{day}", source_type="transcript", title="t", captured_at=at)


def _retr_add(s12, store, memory_id, *, kind=None, day=1, text="python deploy notes"):
    kind = kind or s12.MemoryKind.CONVERSATION
    return store.append(kind=kind, content=text, summary=text, source=_retr_src(s12, day), memory_id=memory_id)


class Eq(str):
    __eq__, __hash__ = (lambda self, other: True), str.__hash__


class Hash(str):
    __hash__ = lambda self: 1  # noqa: E731


class Bool(str):
    __bool__ = lambda self: False  # noqa: E731


BAD_IDS = [7, None, Eq("conv"), Hash("conv"), Bool("conv"), "", " conv", "conv ",
           "\u2028conv", "conv\u2028", "x" * 201]


@pytest.fixture
def _retr_store(s12, tmp_path):
    return s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")


def test_retract_conversation_leaves_view_but_keeps_audit(s12, _retr_store) -> None:
    _retr_add(s12, _retr_store, "conv")
    retraction = _retr_store.retract("conv", reason="user asked", source=_retr_src(s12))
    assert s12.RecallEngine(_retr_store).recall("python deploy").hits == ()
    assert [r.memory_id for r in _retr_store.read_all()] == ["conv", retraction.memory_id]
    assert _retr_store.active_records() == [] and retraction.retracts == "conv"


def test_latest_profile_falls_back_then_none(s12, _retr_store) -> None:
    profile = s12.MemoryKind.PROFILE
    _retr_add(s12, _retr_store, "p1", kind=profile, day=1)
    _retr_add(s12, _retr_store, "p2", kind=profile, day=2)
    _retr_store.retract("p2", reason="wrong", source=_retr_src(s12))
    assert _retr_store.latest_profile().memory_id == "p1"
    _retr_store.retract("p1", reason="wrong", source=_retr_src(s12))
    assert _retr_store.latest_profile() is None


def test_unknown_or_retraction_target_rejected_file_unchanged(s12, _retr_store) -> None:
    _retr_add(s12, _retr_store, "conv")
    first = _retr_store.retract("conv", reason="r", source=_retr_src(s12))
    before = _retr_store.path.read_bytes()
    for target in ("missing", first.memory_id):
        with pytest.raises(s12.RemoteMemoryValidationError):
            _retr_store.retract(target, reason="r", source=_retr_src(s12))
    assert _retr_store.path.read_bytes() == before


def test_repeat_retract_is_idempotent(s12, _retr_store) -> None:
    _retr_add(s12, _retr_store, "conv")
    a = _retr_store.retract("conv", reason="r", source=_retr_src(s12))
    b = _retr_store.retract("conv", reason="again", source=_retr_src(s12, 2))
    assert a == b
    assert sum(r.kind is s12.MemoryKind.RETRACTION for r in _retr_store.read_all()) == 1
    _retr_add(s12, _retr_store, "x" * 200)  # 恰好 200 字符可撤回
    assert _retr_store.retract("x" * 200, reason="r", source=_retr_src(s12)).retracts == "x" * 200


@pytest.mark.parametrize("bad", BAD_IDS)
def test_bad_target_ids_rejected(s12, _retr_store, bad) -> None:
    _retr_add(s12, _retr_store, "conv")
    before = _retr_store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryValidationError):
        s12._require_record_id(bad, field_name="memory_id")
    with pytest.raises(s12.RemoteMemoryValidationError):
        _retr_store.retract(bad, reason="r", source=_retr_src(s12))
    assert _retr_store.path.read_bytes() == before


def test_public_append_rejects_retraction(s12, _retr_store) -> None:
    _retr_add(s12, _retr_store, "conv")
    before = _retr_store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryValidationError):
        _retr_add(s12, _retr_store, "r1", kind=s12.MemoryKind.RETRACTION)
    assert _retr_store.path.read_bytes() == before


def test_derived_id_taken_by_other_record_is_duplicate(s12, _retr_store) -> None:
    _retr_add(s12, _retr_store, "conv")
    _retr_add(s12, _retr_store, s12._retraction_id("conv"))
    before = _retr_store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryDuplicateError):
        _retr_store.retract("conv", reason="r", source=_retr_src(s12))
    assert _retr_store.path.read_bytes() == before


def _tamper(s12, store, **changes):
    _retr_add(s12, store, "conv")
    good = store.retract("conv", reason="r", source=_retr_src(s12)).to_dict()
    first = store.path.read_bytes().split(b"\n")[0]
    store.path.write_bytes(first + b"\n" + json.dumps({**good, **changes}).encode() + b"\n")


@pytest.mark.parametrize(
    "changes",
    [
        {"memory_id": "retraction-wrong"},
        {"retracts": "missing", "memory_id": "retraction-x"},
        {"retracts": "missing", "memory_id": "DERIVED"},
        {"retracts": " conv"},
        {"retracts": 5},
        {"memory_id": 12345},
    ],
)
def test_tampered_retraction_is_corruption(s12, _retr_store, changes) -> None:
    if changes.get("memory_id") == "DERIVED":  # 悬空撤回：id 正确但目标不存在
        changes = {**changes, "memory_id": s12._retraction_id("missing")}
    _tamper(s12, _retr_store, **changes)
    with pytest.raises(s12.RemoteMemoryCorruptionError):
        _retr_store.active_records()


@pytest.mark.parametrize("memory_id", ["mem\x85id", "mem\u2028id", "mem\u2029id"])
def test_line_separator_ids_round_trip(s12, _retr_store, memory_id) -> None:
    _retr_add(s12, _retr_store, memory_id)
    _retr_store.retract(memory_id, reason="r", source=_retr_src(s12))
    reopened = s12.RemoteMemoryStore(_retr_store.path, user_id="u")
    first, retraction = reopened.read_all()
    assert first.memory_id == memory_id and retraction.retracts == memory_id
    assert reopened.active_records() == []


def test_legacy_lines_and_unchanged_format(s12, _retr_store) -> None:
    record = _retr_add(s12, _retr_store, "conv")
    line = json.loads(_retr_store.path.read_text(encoding="utf-8"))
    assert "retracts" not in line and line == record.to_dict()
    assert s12.StoredMemory.from_dict(line).retracts is None
    assert [r.memory_id for r in _retr_store.active_records()] == ["conv"]



# ==== test_s12_retract_tool.py（来源 PR 47）====


@pytest.fixture
def _tool_store(s12, tmp_path, monkeypatch):
    st = s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")
    at = s12._iso(datetime(2026, 8, 1, 12, tzinfo=timezone.utc))
    src = s12.MemorySource(source_id="t1", source_type="transcript", title="t", captured_at=at)
    st.append(kind=s12.MemoryKind.CONVERSATION, content="python deploy notes",
              summary="python deploy notes", source=src, memory_id="conv-1")
    monkeypatch.setattr(s12, "DEFAULT_STORE", st)
    monkeypatch.setattr(s12, "DEFAULT_RECALL", None)
    return st


def _tool_call(s12, args):
    return json.loads(s12.dispatch_tool("retract_memory", args))


def test_retract_hides_from_recall_keeps_audit_and_survives_restart(s12, _tool_store):
    out = _tool_call(s12, {"memory_id": "conv-1", "reason": "用户要求忘掉"})
    assert out["ok"] is True and out["retracts"] == "conv-1"
    assert out["memory_id"] == s12._retraction_id("conv-1")
    for st in (_tool_store, s12.RemoteMemoryStore(_tool_store.path, user_id="u")):
        assert s12.RecallEngine(st).recall("python deploy").hits == ()
        records = {r.memory_id: r for r in st.read_all()}
        assert "conv-1" in records
        event = records[out["memory_id"]]
        assert event.kind is s12.MemoryKind.RETRACTION
        assert event.source.source_type == "model_tool"


def test_repeat_returns_same_event_and_original_reason(s12, _tool_store):
    first = _tool_call(s12, {"memory_id": "conv-1", "reason": "第一次的理由"})
    size = _tool_store.path.stat().st_size
    again = _tool_call(s12, {"memory_id": "conv-1", "reason": "另一个理由"})
    assert again == first and again["reason"] == "第一次的理由"
    assert _tool_store.path.stat().st_size == size


BAD_ARGS = [
    {"memory_id": "nope", "reason": "r"},                       # 未知 id
    {"memory_id": "conv-1", "reason": "   "},                   # 空 reason
    {"memory_id": 7, "reason": "r"},                            # 非 str id
    {"memory_id": "conv-1", "reason": 7},                       # 非 str reason
    {"memory_id": "conv-1"},                                    # 缺参
    {"reason": "r"},                                            # 缺参
    {},                                                         # 全缺
    {"memory_id": "conv-1", "reason": "r", "source": "x"},     # 多参
    ["conv-1", "r"],                                            # 非对象
]


@pytest.mark.parametrize("args", BAD_ARGS)
def test_bad_input_is_ok_false_and_file_unchanged(s12, _tool_store, args):
    before = _tool_store.path.read_bytes()
    out = _tool_call(s12, args)
    assert out["ok"] is False and out["error"]
    assert _tool_store.path.read_bytes() == before


def test_retracting_a_retraction_is_ok_false(s12, _tool_store):
    rid = _tool_call(s12, {"memory_id": "conv-1", "reason": "r"})["memory_id"]
    before = _tool_store.path.read_bytes()
    assert _tool_call(s12, {"memory_id": rid, "reason": "r"})["ok"] is False
    assert _tool_store.path.read_bytes() == before


def test_handler_never_raises_type_error_on_bad_args(s12, _tool_store):
    for name in s12.TOOL_HANDLERS:
        for args in ({}, {"unexpected": 1}):
            assert json.loads(s12.dispatch_tool(name, args))["ok"] is False
    out = json.loads(s12.dispatch_tool("recall_history", {"query": "q", "limit": True}))
    assert out["ok"] is False


def test_tools_registry_and_prompt_convention(s12, _tool_store):
    names = [t["name"] for t in s12.TOOLS]
    assert "retract_memory" in names and set(names) == set(s12.TOOL_HANDLERS)
    schema = next(t for t in s12.TOOLS if t["name"] == "retract_memory")["input_schema"]
    assert set(schema["properties"]) == set(schema["required"]) == {"memory_id", "reason"}
    prompt = s12.build_system_prompt(_tool_store)
    assert "only when the user explicitly asks" in prompt
    assert "never guess" in prompt



# ==== test_s12_retract_tool_adversarial.py（来源 PR 47）====


@pytest.fixture
def _tool_adv_store(s12, tmp_path, monkeypatch):
    st = s12.RemoteMemoryStore(tmp_path / "memory.jsonl", user_id="u")
    source = s12.MemorySource(
        source_id="adversarial-seed",
        source_type="transcript",
        title="seed",
        captured_at=s12._iso(),
    )
    for memory_id in ("target", "keep"):
        st.append(
            kind=s12.MemoryKind.CONVERSATION,
            memory_id=memory_id,
            content="quartz deployment notes",
            summary="quartz deployment notes",
            source=source,
        )
    monkeypatch.setattr(s12, "DEFAULT_STORE", st)
    monkeypatch.setattr(s12, "DEFAULT_RECALL", None)
    monkeypatch.setattr(s12, "SYSTEM", None)
    return st


def _tool_adv_call(s12, args):
    return json.loads(s12.dispatch_tool("retract_memory", args))


class StrSubclass(str):
    pass


@pytest.mark.parametrize(
    "args",
    [
        None,
        [],
        "target",
        {},
        {"memory_id": "target"},
        {"reason": "forget"},
        {"memory_id": "target", "reason": "forget", "source": {}},
        {"memory_id": "target", "reason": "forget", "source_type": "user"},
        {"memory_id": True, "reason": "forget"},
        {"memory_id": ["target"], "reason": "forget"},
        {"memory_id": StrSubclass("target"), "reason": "forget"},
        {"memory_id": "target", "reason": None},
        {"memory_id": "target", "reason": StrSubclass("forget")},
        {"memory_id": "target", "reason": ""},
        {"memory_id": "target", "reason": "\t\n "},
        {"memory_id": " target", "reason": "forget"},
        {"memory_id": "TARGET", "reason": "forget"},
        {"memory_id": "x" * 201, "reason": "forget"},
    ],
)
def test_rejects_bad_arguments_without_writing(s12, _tool_adv_store, args):
    before = _tool_adv_store.path.read_bytes()
    result = _tool_adv_call(s12, args)
    assert result["ok"] is False
    assert result["error"]
    assert _tool_adv_store.path.read_bytes() == before


def test_tool_recall_transition_and_restart_idempotency(s12, _tool_adv_store, monkeypatch):
    def recalled_ids():
        result = json.loads(
            s12.dispatch_tool("recall_history", {"query": "quartz deployment"})
        )
        return {hit["memory_id"] for hit in result["hits"]}

    assert recalled_ids() == {"target", "keep"}
    original = _tool_adv_store.read_all()[0]
    first = _tool_adv_call(s12, {"memory_id": "target", "reason": "original reason"})
    assert first["ok"] is True
    assert first["retracts"] == "target"
    assert first["reason"] == "original reason"
    assert recalled_ids() == {"keep"}
    records = _tool_adv_store.read_all()
    assert len(records) == 3
    assert records[0] == original
    event = records[-1]
    assert event.memory_id == first["memory_id"]
    assert event.kind is s12.MemoryKind.RETRACTION
    assert event.retracts == "target"
    assert event.source.source_type == "model_tool"

    before = _tool_adv_store.path.read_bytes()
    reopened = s12.RemoteMemoryStore(_tool_adv_store.path, user_id="u")
    monkeypatch.setattr(s12, "DEFAULT_STORE", reopened)
    assert recalled_ids() == {"keep"}
    assert _tool_adv_call(
        s12, {"memory_id": "target", "reason": "replacement reason"}
    ) == first
    assert _tool_adv_call(
        s12, {"memory_id": first["memory_id"], "reason": "forget event"}
    )["ok"] is False
    assert _tool_adv_store.path.read_bytes() == before


def test_duplicate_id_collision_is_tool_error(s12, _tool_adv_store):
    source = _tool_adv_store.read_all()[0].source
    _tool_adv_store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id=s12._retraction_id("target"),
        content="existing unrelated record",
        summary="existing unrelated record",
        source=source,
    )
    before = _tool_adv_store.path.read_bytes()
    result = _tool_adv_call(s12, {"memory_id": "target", "reason": "forget"})
    assert result["ok"] is False
    assert result["error"]
    assert _tool_adv_store.path.read_bytes() == before
    assert "target" in {r.memory_id for r in _tool_adv_store.active_records()}


def test_agent_loop_delivers_errors_and_success(s12, _tool_adv_store, monkeypatch):
    inputs = [
        {"memory_id": "target"},
        {"memory_id": "target", "reason": "r", "source": "forged"},
        {"memory_id": "target", "reason": False},
        {"memory_id": "target", "reason": "original"},
        {"memory_id": "target", "reason": "replacement"},
    ]
    blocks = [
        SimpleNamespace(
            type="tool_use",
            id=f"tool-{index}",
            name="retract_memory",
            input=args,
        )
        for index, args in enumerate(inputs)
    ]
    responses = iter(
        [
            SimpleNamespace(stop_reason="tool_use", content=blocks),
            SimpleNamespace(stop_reason="end_turn", content=[]),
        ]
    )
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return next(responses)

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(s12, "runtime_client", lambda: (client, "offline"))
    messages = [{"role": "user", "content": "Forget target."}]
    s12.agent_loop(messages)

    assert len(requests) == 2
    results = messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == [
        block.id for block in blocks
    ]
    outputs = [json.loads(r["content"]) for r in results]
    assert [out["ok"] for out in outputs] == [
        False, False, False, True, True
    ]
    assert outputs[3] == outputs[4]
    assert outputs[4]["reason"] == "original"
    assert len(_tool_adv_store.read_all()) == 3



# ==== test_s12_retract_demo.py（来源 PR 54）====


ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s12_cloud_memory" / "code.py"
MODULE = "s12_retract_demo_test_module"


@pytest.fixture
def s12_retract_demo(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    spec = importlib.util.spec_from_file_location(MODULE, CODE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[MODULE] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(MODULE, None)
        sys.modules.pop("anthropic", None)


def _ids(line: str) -> list[str]:
    tail = line.split(": ", 1)[1]
    return [] if tail == "(none)" else tail.split(", ")


def _counts(line: str) -> tuple[int, int]:
    m = re.search(r"read_all=(\d+) active=(\d+)", line)
    assert m, line
    return int(m.group(1)), int(m.group(2))


def _check_demo(lines: list[str]) -> None:
    by_tag: dict[str, list[str]] = {}
    for line in lines:
        by_tag.setdefault(line.split("]", 1)[0] + "]", []).append(line)
    before = _ids(by_tag["[before]"][0])
    assert len(before) >= 2
    target = before[0]
    assert by_tag["[harness]"][0].startswith(f"[harness] retract {target} -> ")
    after = _ids(by_tag["[after]"][0])
    assert after == [i for i in before if i != target]
    assert _ids(by_tag["[restart]"][0]) == after
    read_before, active_before = _counts(by_tag["[before]"][1])
    read_after, active_after = _counts(by_tag["[after]"][1])
    assert (read_after, active_after) == (read_before + 1, active_before - 1)
    assert by_tag["[after]"][1].endswith(f"{target} still in read_all: True")
    rejects = by_tag["[reject]"]
    assert len(rejects) == 2
    assert all(line.endswith(": rejected") for line in rejects)


def _tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): (p.read_bytes() if p.is_file() else b"<dir>")
        for p in sorted(root.rglob("*"))
    }


def _run_repl(home: Path, stdin: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env |= {"MODEL_ID": "offline", "WORKBUDDY_HOME": str(home)}
    proc = subprocess.run(
        [sys.executable, str(CODE)], input=stdin,
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def test_repl_retract_demo_offline_keeps_workbuddy_home_unchanged(tmp_path):
    home = tmp_path / "home"
    _run_repl(home, "q\n")  # first launch initializes the default store
    snapshot = _tree(home)
    assert snapshot
    proc = _run_repl(home, "/retract-demo\nq\n")
    assert "retract-demo failed" not in proc.stdout
    lines = [line.split("s12 >> ")[-1] for line in proc.stdout.splitlines()]
    _check_demo([line for line in lines if line.startswith("[")])
    assert _tree(home) == snapshot


def test_demo_never_touches_default_runtime(s12_retract_demo, monkeypatch):
    def boom():
        raise AssertionError("default_runtime must not be used")

    monkeypatch.setattr(s12_retract_demo, "default_runtime", boom)
    monkeypatch.setattr(s12_retract_demo, "create_default_store", boom)
    _check_demo(s12_retract_demo.run_retract_demo())


def test_demo_output_is_deterministic(s12_retract_demo):
    assert s12_retract_demo.run_retract_demo() == s12_retract_demo.run_retract_demo()


def test_demo_error_propagates_and_temp_dir_is_cleaned(s12_retract_demo, monkeypatch):
    seen: list[Path] = []

    def failing_retract(self, target_id, **kwargs):
        seen.append(self.path.parent)
        raise RuntimeError("injected retract failure")

    monkeypatch.setattr(s12_retract_demo.RemoteMemoryStore, "retract", failing_retract)
    with pytest.raises(RuntimeError, match="injected retract failure"):
        s12_retract_demo.run_retract_demo()
    assert len(seen) == 1
    assert not seen[0].exists()


def test_adversarial_banner_keeps_profile_snapshot_and_no_temp_paths(tmp_path):
    # 6b19e24 dropped the upstream "profile snapshot" banner line on first launch.
    proc = _run_repl(tmp_path / "home", "/retract-demo\nq\n")
    assert "profile snapshot: " in proc.stdout
    assert "/retract-demo" in proc.stdout.split("s12 >> ")[0]
    demo = proc.stdout.split("s12 >> ", 1)[1]
    assert "records.jsonl" not in demo and str(tmp_path) not in demo


def test_adversarial_repl_survives_demo_failure(s12_retract_demo, monkeypatch, capsys):
    def boom():
        raise RuntimeError("injected demo failure")

    feed = iter(["/retract-demo", "/retract-demo", "q"])
    monkeypatch.setattr(s12_retract_demo, "run_retract_demo", boom)
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    s12_retract_demo.main()
    out = capsys.readouterr().out
    assert out.count("retract-demo failed: injected demo failure") == 2



# ==== test_s12_memory_id_type_duplicate.py（来源 PR 44）====


def _id_src(s12):
    at = s12._iso(datetime(2026, 8, 1, 12, tzinfo=timezone.utc))
    return s12.MemorySource(source_id="s1", source_type="transcript", title="t", captured_at=at)


def _id_add(s12, store, memory_id):
    return store.append(kind=s12.MemoryKind.CONVERSATION, content="python notes",
                        summary="python notes", source=_id_src(s12), memory_id=memory_id)


@pytest.fixture
def _id_store(s12, tmp_path):
    return s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")


def test_control_str_duplicate_rejected_file_unchanged(s12, _id_store) -> None:
    _id_add(s12, _id_store, "123")
    before = _id_store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryDuplicateError):
        _id_add(s12, _id_store, "123")
    assert _id_store.path.read_bytes() == before
    assert [r.memory_id for r in _id_store.read_all()] == ["123"]


def test_control_str_duplicate_rejected_across_reload(s12, _id_store) -> None:
    _id_add(s12, _id_store, "123")
    with pytest.raises(s12.RemoteMemoryDuplicateError):
        _id_add(s12, s12.RemoteMemoryStore(_id_store.path, user_id="u"), "123")


@pytest.mark.parametrize("pair", [(123, 123), (123, "123"), ("123", 123)])
def test_int_id_never_slips_past_duplicate_check(s12, _id_store, pair) -> None:
    accepted = 0
    for memory_id in pair:
        try:
            _id_add(s12, _id_store, memory_id)
            accepted += 1
        except (s12.RemoteMemoryDuplicateError, s12.RemoteMemoryValidationError):
            pass
    assert accepted <= 1, f"{pair!r}: both appends accepted"
    reloaded = s12.RemoteMemoryStore(_id_store.path, user_id="u")
    assert len(reloaded.read_all()) == accepted  # 落盘仍可读，没有被双写毁掉


def test_returned_id_round_trips_through_retract(s12, _id_store) -> None:
    try:
        record = _id_add(s12, _id_store, 123)
    except s12.RemoteMemoryValidationError:
        return  # 入口就拒绝非 str id，也满足约定
    # append() 返回的 id 必须能直接用于 retract()，且与回读类型一致
    assert record.memory_id == _id_store.read_all()[0].memory_id
    _id_store.retract(record.memory_id, reason="r", source=_id_src(s12))
    assert _id_store.active_records() == []


# 控制用例：校验只针对显式传入的非空 id；省略 / None / "" 仍走 uuid 自动生成。
@pytest.mark.parametrize("memory_id", ["<omitted>", None, ""])
def test_control_missing_or_empty_id_autogenerates_uuid(s12, _id_store, memory_id) -> None:
    if memory_id == "<omitted>":
        record = _id_store.append(kind=s12.MemoryKind.CONVERSATION, content="python notes",
                              summary="python notes", source=_id_src(s12))
    else:
        record = _id_add(s12, _id_store, memory_id)
    assert type(record.memory_id) is str
    assert len(record.memory_id) == 32 and int(record.memory_id, 16) >= 0  # uuid4().hex
    second = _id_add(s12, _id_store, memory_id if memory_id != "<omitted>" else None)
    assert second.memory_id != record.memory_id
    reloaded = s12.RemoteMemoryStore(_id_store.path, user_id="u")
    assert [r.memory_id for r in reloaded.read_all()] == [record.memory_id, second.memory_id]



# ==== test_s12_append_id_adversarial.py（来源：卡面未列 PR）====


class TrapStr(str):
    def __eq__(self, other):
        raise AssertionError("ID equality hook must not run")

    def __bool__(self):
        raise AssertionError("ID truthiness hook must not run")


class EmptyEqual:
    def __eq__(self, other):
        return True


@pytest.mark.parametrize(
    "memory_id",
    [
        123, 0, False, True, 1.5, b"123", [], {},
        EmptyEqual(), TrapStr(""), TrapStr("123"),
        " ", " padded", "padded ", "x" * 201,
        "\u2028id", "id\u2028", "\u0085id", "id\u2029",
    ],
    ids=[
        "int", "zero", "false", "true", "float", "bytes",
        "list", "dict", "empty-equal", "trap-empty", "trap-nonempty",
        "whitespace", "leading-space", "trailing-space", "too-long",
        "leading-ls", "trailing-ls", "leading-nel", "trailing-ps",
    ],
)
def test_invalid_id_preserves_existing_store(s12, tmp_path, memory_id):
    store = s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")
    _id_add(s12, store, "123")
    before = store.path.read_bytes()

    with pytest.raises(s12.RemoteMemoryValidationError):
        _id_add(s12, store, memory_id)

    assert store.path.read_bytes() == before
    reopened = s12.RemoteMemoryStore(store.path, user_id="u")
    assert [r.memory_id for r in reopened.read_all()] == ["123"]


@pytest.mark.parametrize("memory_id", ["x", "x" * 200, "a\u2028b"])
def test_valid_id_roundtrip_duplicate_and_retract(s12, tmp_path, memory_id):
    store = s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")
    record = _id_add(s12, store, memory_id)
    assert type(record.memory_id) is str
    assert record.memory_id == memory_id

    reopened = s12.RemoteMemoryStore(store.path, user_id="u")
    assert reopened.read_all()[0].memory_id == memory_id
    before = store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryDuplicateError):
        _id_add(s12, reopened, memory_id)
    assert store.path.read_bytes() == before

    reopened.retract(record.memory_id, reason="withdraw", source=_id_src(s12))
    assert reopened.active_records() == []
    assert len(reopened.read_all()) == 2


@pytest.mark.parametrize("mode", ["omitted", "none", "empty"])
def test_uuid_fallback_returns_retractable_ids(s12, tmp_path, mode):
    store = s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")
    records = []
    for _ in range(2):
        if mode == "omitted":
            record = store.append(
                kind=s12.MemoryKind.CONVERSATION,
                content="python notes",
                summary="python notes",
                source=_id_src(s12),
            )
        else:
            record = _id_add(s12, store, None if mode == "none" else "")
        assert type(record.memory_id) is str
        assert len(record.memory_id) == 32
        assert all(c in "0123456789abcdef" for c in record.memory_id)
        records.append(record)

    assert records[0].memory_id != records[1].memory_id
    reopened = s12.RemoteMemoryStore(store.path, user_id="u")
    assert [r.memory_id for r in reopened.read_all()] == [
        r.memory_id for r in records
    ]
    for record in records:
        reopened.retract(record.memory_id, reason="withdraw", source=_id_src(s12))
    assert reopened.active_records() == []



# ==== test_s12_append_id_str_subclass.py（来源：卡面未列 PR）====


class Evil(str):
    def __eq__(self, other):
        return True

    def __hash__(self):
        return 0

    def __bool__(self):
        return False


class PlainSub(str):
    pass


@pytest.mark.parametrize(
    "memory_id",
    [Evil("x"), Evil(""), PlainSub(""), PlainSub("ok")],
    ids=["evil", "evil-empty", "plain-empty", "plain"],
)
def test_str_subclass_memory_id_rejected_and_nothing_written(s12, tmp_path, memory_id) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")
    with pytest.raises(s12.RemoteMemoryValidationError):
        _id_add(s12, store, memory_id)
    assert not store.path.exists() or store.path.read_bytes() == b""


def test_builtin_empty_str_still_autogenerates_uuid(s12, tmp_path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")
    record = _id_add(s12, store, "")
    assert type(record.memory_id) is str and len(record.memory_id) == 32

# ==== Verifier 对抗测试（#64）====


def test_s12_cloud_memory_adversarial_fixture_section_isolation():
    import ast
    from pathlib import Path

    marker = "# ==== Verifier 对抗测试（#64）===="
    source = Path(__file__).read_text(encoding="utf-8")
    lines = source.splitlines()
    boundary = next(i for i, line in enumerate(lines) if line == marker)
    original = lines[:boundary]
    tree = ast.parse("\n".join(original))

    allowed = {
        "test_s12_adversarial.py": "_adv_store",
        "test_s12_memory_retraction.py": "_retr_store",
        "test_s12_retract_tool.py": "_tool_store",
        "test_s12_retract_tool_adversarial.py": "_tool_adv_store",
        "test_s12_retract_demo.py": "s12_retract_demo",
        "test_s12_memory_id_type_duplicate.py": "_id_store",
        "test_s12_append_id_adversarial.py": None,
        "test_s12_append_id_str_subclass.py": None,
    }
    sections = {}
    current = None
    seen_sections = []
    for lineno, line in enumerate(original, 1):
        if line.startswith("# ==== "):
            current = line.removeprefix("# ==== ").split("（", 1)[0]
            assert current in allowed, f"未声明的原段：{line}"
            seen_sections.append(current)
        sections[lineno] = current
    assert seen_sections == list(allowed)

    fixture_names = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "pytest"
                and target.attr == "fixture"
            ):
                if isinstance(decorator, ast.Call):
                    assert not any(
                        keyword.arg in ("name", None)
                        for keyword in decorator.keywords
                    ), f"{node.name} 不得通过 name= 或 **kwargs 隐藏注册名"
                fixture_names.append(node.name)

    expected = {name for name in allowed.values() if name is not None}
    assert len(fixture_names) == len(set(fixture_names)), fixture_names
    assert "store" not in fixture_names
    assert "s12" not in fixture_names  # 仅复用顶部导入的 s12
    assert set(fixture_names) == expected

    guarded = expected | {"store", "s12"}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        section = sections[node.lineno]
        assert section in allowed, node.name
        permitted = {"s12"}
        if allowed[section] is not None:
            permitted.add(allowed[section])
        arguments = (
            node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        )
        for argument in arguments:
            name = argument.arg
            if name in guarded or name.endswith("_store"):
                assert name in permitted, (
                    f"{section} 中 {node.name} 误用了 fixture {name}"
                )


import pytest


@pytest.mark.parametrize(
    "fixture_name, filename, expected_ids",
    [
        ("_adv_store", "records.jsonl", ()),
        ("_retr_store", "m.jsonl", ()),
        ("_id_store", "m.jsonl", ()),
        ("_tool_store", "m.jsonl", ("conv-1",)),
        ("_tool_adv_store", "memory.jsonl", ("target", "keep")),
    ],
)
def test_s12_cloud_memory_adversarial_fixture_semantic_binding(
    request, monkeypatch, fixture_name, filename, expected_ids
):
    module = request.getfixturevalue("s12")
    sentinels = {name: object() for name in (
        "DEFAULT_STORE", "DEFAULT_RECALL", "SYSTEM"
    )}
    for name, sentinel in sentinels.items():
        monkeypatch.setattr(module, name, sentinel)

    store = request.getfixturevalue(fixture_name)
    assert isinstance(store, module.RemoteMemoryStore)
    assert store.path.name == filename
    assert store.user_id == "u"

    records = store.read_all()
    assert tuple(record.memory_id for record in records) == expected_ids
    assert store.active_records() == records
    assert all(
        record.kind is module.MemoryKind.CONVERSATION for record in records
    )

    if fixture_name in {"_tool_store", "_tool_adv_store"}:
        assert module.DEFAULT_STORE is store
        assert module.DEFAULT_RECALL is None
        if fixture_name == "_tool_adv_store":
            assert module.SYSTEM is None
            assert all(
                record.source.source_id == "adversarial-seed"
                for record in records
            )
        else:
            assert module.SYSTEM is sentinels["SYSTEM"]
            assert records[0].source.source_id == "t1"
    else:
        for name, sentinel in sentinels.items():
            assert getattr(module, name) is sentinel
