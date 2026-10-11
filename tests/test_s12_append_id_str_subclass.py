"""s12 append()：str 子类不能借 uuid 回退绕过 `_require_record_id`。"""

from __future__ import annotations

import pytest
from test_remote_memory import s12  # noqa: F401  复用 s12 模块加载 fixture
from test_s12_memory_id_type_duplicate import _add


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
        _add(s12, store, memory_id)
    assert not store.path.exists() or store.path.read_bytes() == b""


def test_builtin_empty_str_still_autogenerates_uuid(s12, tmp_path) -> None:
    store = s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")
    record = _add(s12, store, "")
    assert type(record.memory_id) is str and len(record.memory_id) == 32
