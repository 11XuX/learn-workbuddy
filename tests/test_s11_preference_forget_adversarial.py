"""s11 遗忘偏好的对抗测试：时区边界、多墓碑与多轮重建。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s11():
    """沿用已有测试的离线加载方式，退出时恢复导入与环境状态。"""
    stub_dir = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    os.environ["MODEL_ID"] = "offline-test-model"
    module_name = "s11_preference_forget_adversarial_test_module"
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, ROOT / "s11_user_memory" / "code.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(stub_dir))
        sys.modules.pop(module_name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        if old_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = old_model


@pytest.fixture
def memory(s11, tmp_path):
    """建立两个已遗忘的 key，检测目标操作是否误删其他墓碑。"""
    store = s11.UserMemory(tmp_path, user_id="alice")
    for key, value, deleted_at in (
        (
            "response.language",
            "旧语言偏好",
            "2026-10-02T00:00:00.000001Z",
        ),
        (
            "editor.indent",
            "旧缩进偏好",
            "2026-10-02T00:00:00.000002Z",
        ),
    ):
        store.set_preference(
            key, value, updated_at="2026-10-01T00:00:00Z"
        )
        store.delete_preference(key, deleted_at=deleted_at)
    return store


@pytest.mark.parametrize(
    "evidence_at",
    [
        "2026-10-02T08:00:00.000001+08:00",
        "2026-10-01T19:00:00.000000-05:00",
    ],
)
def test_equal_or_older_offset_evidence_preserves_tombstone(
    s11, memory, evidence_at
):
    """攻击字符串时间比较与提前移除墓碑：等时刻或更早证据不得写盘。"""
    before_json = memory.preferences_path.read_bytes()
    before_markdown = memory.memory_path.read_bytes()

    with pytest.raises(s11.StalePreferenceUpdateError):
        memory.set_preference(
            " RESPONSE.LANGUAGE ",
            "旧语言偏好",
            updated_at=evidence_at,
            source_event_id="session:e1",
        )

    assert memory.preferences_path.read_bytes() == before_json
    assert memory.memory_path.read_bytes() == before_markdown
    assert memory.list_preferences() == []
    assert [
        item.key for item in memory.list_deleted_preferences()
    ] == ["editor.indent", "response.language"]


def test_recreation_preserves_other_tombstone_across_cycles(
    s11, memory, tmp_path
):
    """攻击缓存、微秒截断及整组清墓碑：多轮重建须接续 revision 并保留其他 key。"""
    restarted = s11.UserMemory(tmp_path, user_id="alice")
    result = restarted.set_preference(
        "response.language",
        "新语言偏好",
        updated_at="2026-10-02T08:00:00.000002+08:00",
    )
    assert result.status is s11.WriteStatus.CREATED
    assert result.revision == 2
    assert [
        item.key for item in restarted.list_deleted_preferences()
    ] == ["editor.indent"]

    restarted.delete_preference(
        "response.language",
        deleted_at="2026-10-02T00:00:00.000003Z",
    )
    restarted = s11.UserMemory(tmp_path, user_id="alice")
    before = restarted.preferences_path.read_bytes()

    with pytest.raises(s11.StalePreferenceUpdateError):
        restarted.set_preference(
            "response.language",
            "新语言偏好",
            updated_at="2026-10-02T00:00:00.000002Z",
        )
    assert restarted.preferences_path.read_bytes() == before

    result = restarted.set_preference(
        "response.language",
        "最新语言偏好",
        updated_at="2026-10-02T00:00:00.000004Z",
    )
    assert result.revision == 3
    payload = json.loads(
        restarted.preferences_path.read_text(encoding="utf-8")
    )
    assert payload["deleted"] == [
        {
            "key": "editor.indent",
            "deleted_at": "2026-10-02T00:00:00.000002Z",
            "revision": 1,
            "source": "explicit",
        }
    ]
    assert [
        (item.key, item.value, item.revision)
        for item in restarted.list_preferences()
    ] == [("response.language", "最新语言偏好", 3)]
    for text in (
        restarted.preferences_path.read_text(encoding="utf-8"),
        restarted.memory_path.read_text(encoding="utf-8"),
    ):
        assert "旧语言偏好" not in text
        assert "旧缩进偏好" not in text
