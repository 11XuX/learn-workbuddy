"""s12 append() 承诺原子拒绝重复 id：int memory_id 不能绕过去重。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from test_remote_memory import s12  # noqa: F401  复用 s12 模块加载 fixture


def _src(s12):
    at = s12._iso(datetime(2026, 8, 1, 12, tzinfo=timezone.utc))
    return s12.MemorySource(source_id="s1", source_type="transcript", title="t", captured_at=at)


def _add(s12, store, memory_id):
    return store.append(kind=s12.MemoryKind.CONVERSATION, content="python notes",
                        summary="python notes", source=_src(s12), memory_id=memory_id)


@pytest.fixture
def store(s12, tmp_path):
    return s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")


def test_control_str_duplicate_rejected_file_unchanged(s12, store) -> None:
    _add(s12, store, "123")
    before = store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryDuplicateError):
        _add(s12, store, "123")
    assert store.path.read_bytes() == before
    assert [r.memory_id for r in store.read_all()] == ["123"]


def test_control_str_duplicate_rejected_across_reload(s12, store) -> None:
    _add(s12, store, "123")
    with pytest.raises(s12.RemoteMemoryDuplicateError):
        _add(s12, s12.RemoteMemoryStore(store.path, user_id="u"), "123")


@pytest.mark.parametrize("pair", [(123, 123), (123, "123"), ("123", 123)])
def test_int_id_never_slips_past_duplicate_check(s12, store, pair) -> None:
    accepted = 0
    for memory_id in pair:
        try:
            _add(s12, store, memory_id)
            accepted += 1
        except (s12.RemoteMemoryDuplicateError, s12.RemoteMemoryValidationError):
            pass
    assert accepted <= 1, f"{pair!r}: both appends accepted"
    reloaded = s12.RemoteMemoryStore(store.path, user_id="u")
    assert len(reloaded.read_all()) == accepted  # 落盘仍可读，没有被双写毁掉


def test_returned_id_round_trips_through_retract(s12, store) -> None:
    try:
        record = _add(s12, store, 123)
    except s12.RemoteMemoryValidationError:
        return  # 入口就拒绝非 str id，也满足约定
    # append() 返回的 id 必须能直接用于 retract()，且与回读类型一致
    assert record.memory_id == store.read_all()[0].memory_id
    store.retract(record.memory_id, reason="r", source=_src(s12))
    assert store.active_records() == []
