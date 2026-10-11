"""s12 只追加撤回：审计流与有效视图分离。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from test_remote_memory import s12  # noqa: F401  复用 s12 模块加载 fixture


def _src(s12, day: int = 1):
    at = s12._iso(datetime(2026, 8, day, 12, tzinfo=timezone.utc))
    return s12.MemorySource(source_id=f"s{day}", source_type="transcript", title="t", captured_at=at)


def _add(s12, store, memory_id, *, kind=None, day=1, text="python deploy notes"):
    kind = kind or s12.MemoryKind.CONVERSATION
    return store.append(kind=kind, content=text, summary=text, source=_src(s12, day), memory_id=memory_id)


class Eq(str):
    __eq__, __hash__ = (lambda self, other: True), str.__hash__


class Hash(str):
    __hash__ = lambda self: 1  # noqa: E731


class Bool(str):
    __bool__ = lambda self: False  # noqa: E731


BAD_IDS = [7, None, Eq("conv"), Hash("conv"), Bool("conv"), "", " conv", "conv ",
           "\u2028conv", "conv\u2028", "x" * 201]


@pytest.fixture
def store(s12, tmp_path):
    return s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")


def test_retract_conversation_leaves_view_but_keeps_audit(s12, store) -> None:
    _add(s12, store, "conv")
    retraction = store.retract("conv", reason="user asked", source=_src(s12))
    assert s12.RecallEngine(store).recall("python deploy").hits == ()
    assert [r.memory_id for r in store.read_all()] == ["conv", retraction.memory_id]
    assert store.active_records() == [] and retraction.retracts == "conv"


def test_latest_profile_falls_back_then_none(s12, store) -> None:
    profile = s12.MemoryKind.PROFILE
    _add(s12, store, "p1", kind=profile, day=1)
    _add(s12, store, "p2", kind=profile, day=2)
    store.retract("p2", reason="wrong", source=_src(s12))
    assert store.latest_profile().memory_id == "p1"
    store.retract("p1", reason="wrong", source=_src(s12))
    assert store.latest_profile() is None


def test_unknown_or_retraction_target_rejected_file_unchanged(s12, store) -> None:
    _add(s12, store, "conv")
    first = store.retract("conv", reason="r", source=_src(s12))
    before = store.path.read_bytes()
    for target in ("missing", first.memory_id):
        with pytest.raises(s12.RemoteMemoryValidationError):
            store.retract(target, reason="r", source=_src(s12))
    assert store.path.read_bytes() == before


def test_repeat_retract_is_idempotent(s12, store) -> None:
    _add(s12, store, "conv")
    a = store.retract("conv", reason="r", source=_src(s12))
    b = store.retract("conv", reason="again", source=_src(s12, 2))
    assert a == b
    assert sum(r.kind is s12.MemoryKind.RETRACTION for r in store.read_all()) == 1
    _add(s12, store, "x" * 200)  # 恰好 200 字符可撤回
    assert store.retract("x" * 200, reason="r", source=_src(s12)).retracts == "x" * 200


@pytest.mark.parametrize("bad", BAD_IDS)
def test_bad_target_ids_rejected(s12, store, bad) -> None:
    _add(s12, store, "conv")
    before = store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryValidationError):
        s12._require_record_id(bad, field_name="memory_id")
    with pytest.raises(s12.RemoteMemoryValidationError):
        store.retract(bad, reason="r", source=_src(s12))
    assert store.path.read_bytes() == before


def test_public_append_rejects_retraction(s12, store) -> None:
    _add(s12, store, "conv")
    before = store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryValidationError):
        _add(s12, store, "r1", kind=s12.MemoryKind.RETRACTION)
    assert store.path.read_bytes() == before


def test_derived_id_taken_by_other_record_is_duplicate(s12, store) -> None:
    _add(s12, store, "conv")
    _add(s12, store, s12._retraction_id("conv"))
    before = store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryDuplicateError):
        store.retract("conv", reason="r", source=_src(s12))
    assert store.path.read_bytes() == before


def _tamper(s12, store, **changes):
    _add(s12, store, "conv")
    good = store.retract("conv", reason="r", source=_src(s12)).to_dict()
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
def test_tampered_retraction_is_corruption(s12, store, changes) -> None:
    if changes.get("memory_id") == "DERIVED":  # 悬空撤回：id 正确但目标不存在
        changes = {**changes, "memory_id": s12._retraction_id("missing")}
    _tamper(s12, store, **changes)
    with pytest.raises(s12.RemoteMemoryCorruptionError):
        store.active_records()


@pytest.mark.parametrize("memory_id", ["mem\x85id", "mem\u2028id", "mem\u2029id"])
def test_line_separator_ids_round_trip(s12, store, memory_id) -> None:
    _add(s12, store, memory_id)
    store.retract(memory_id, reason="r", source=_src(s12))
    reopened = s12.RemoteMemoryStore(store.path, user_id="u")
    first, retraction = reopened.read_all()
    assert first.memory_id == memory_id and retraction.retracts == memory_id
    assert reopened.active_records() == []


def test_legacy_lines_and_unchanged_format(s12, store) -> None:
    record = _add(s12, store, "conv")
    line = json.loads(store.path.read_text(encoding="utf-8"))
    assert "retracts" not in line and line == record.to_dict()
    assert s12.StoredMemory.from_dict(line).retracts is None
    assert [r.memory_id for r in store.active_records()] == ["conv"]
