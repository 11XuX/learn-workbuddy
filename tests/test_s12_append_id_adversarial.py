"""Adversarial checks for the s12 append ID validation boundary."""

from __future__ import annotations

import pytest
from test_remote_memory import s12  # noqa: F401
from test_s12_memory_id_type_duplicate import _add, _src


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
    _add(s12, store, "123")
    before = store.path.read_bytes()

    with pytest.raises(s12.RemoteMemoryValidationError):
        _add(s12, store, memory_id)

    assert store.path.read_bytes() == before
    reopened = s12.RemoteMemoryStore(store.path, user_id="u")
    assert [r.memory_id for r in reopened.read_all()] == ["123"]


@pytest.mark.parametrize("memory_id", ["x", "x" * 200, "a\u2028b"])
def test_valid_id_roundtrip_duplicate_and_retract(s12, tmp_path, memory_id):
    store = s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")
    record = _add(s12, store, memory_id)
    assert type(record.memory_id) is str
    assert record.memory_id == memory_id

    reopened = s12.RemoteMemoryStore(store.path, user_id="u")
    assert reopened.read_all()[0].memory_id == memory_id
    before = store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryDuplicateError):
        _add(s12, reopened, memory_id)
    assert store.path.read_bytes() == before

    reopened.retract(record.memory_id, reason="withdraw", source=_src(s12))
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
                source=_src(s12),
            )
        else:
            record = _add(s12, store, None if mode == "none" else "")
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
        reopened.retract(record.memory_id, reason="withdraw", source=_src(s12))
    assert reopened.active_records() == []
