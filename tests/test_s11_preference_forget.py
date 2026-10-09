"""s11 遗忘偏好：删除墓碑、防复活与模型遗忘工具的离线测试。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OLD = "2026-10-01T00:00:00+00:00"
VALUE = "Reply in Klingon"


@pytest.fixture(scope="module")
def s11():
    stub_dir = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    os.environ["MODEL_ID"] = "offline-test-model"
    module_name = "s11_preference_forget_test_module"
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
    store = s11.UserMemory(tmp_path, user_id="alice")
    store.save_identity(
        soul="# Soul\n\nBe useful.",
        assistant_identity="# Identity\n\nName: WorkBuddy",
        profile={"name": "Alice"},
    )
    return store


def _replay(memory):
    # 同一条证据：相同 source_event_id 和 updated_at
    return memory.set_preference(
        "response.language", VALUE, updated_at=OLD, source_event_id="session-1:e1"
    )


def _payload(memory) -> dict:
    return json.loads(memory.preferences_path.read_text(encoding="utf-8"))


def test_forget_tool_schema_only_exposes_required_key(s11) -> None:
    tool = next(
        item
        for item in s11.IdentityAwareAgent._build_tools()
        if item["name"] == "forget_user_preference"
    )
    assert set(tool["input_schema"]["properties"]) == {"key"}
    assert tool["input_schema"]["required"] == ["key"]


def test_dispatch_forget_removes_value_everywhere(s11, memory, tmp_path) -> None:
    _replay(memory)
    agent = s11.IdentityAwareAgent(memory, tmp_path)

    result = json.loads(
        agent._dispatch_tool("forget_user_preference", {"key": "response.language"})
    )

    assert result["status"] == "deleted"
    context = memory.get_context_for_agent()
    assert "response.language" not in context
    assert "response.language" not in memory.memory_path.read_text(encoding="utf-8")
    for text in (context, memory.read_memory(), memory.preferences_path.read_text()):
        assert VALUE not in text
    [tombstone] = memory.list_deleted_preferences()
    assert (tombstone.key, tombstone.source) == ("response.language", "model_tool")
    assert not hasattr(tombstone, "value")


def test_replaying_same_evidence_after_delete_is_stale(s11, memory) -> None:
    assert _replay(memory).status is s11.WriteStatus.CREATED
    assert memory.delete_preference("response.language").status is s11.WriteStatus.DELETED

    with pytest.raises(s11.StalePreferenceUpdateError):
        _replay(memory)
    assert memory.list_active_preferences() == []


def test_tombstone_survives_other_writes(s11, memory) -> None:
    _replay(memory)
    memory.delete_preference("response.language")
    memory.set_preference("editor.indent", "tabs")
    assert [item["key"] for item in _payload(memory)["deleted"]] == ["response.language"]
    memory.append_memory("Prefer short answers")
    assert [item["key"] for item in _payload(memory)["deleted"]] == ["response.language"]

    with pytest.raises(s11.StalePreferenceUpdateError):
        _replay(memory)
    assert "response.language" not in {item.key for item in memory.list_preferences()}


def test_newer_evidence_recreates_and_clears_tombstone(s11, memory) -> None:
    _replay(memory)
    memory.set_preference("response.language", "English", updated_at="2026-10-02T00:00:00Z")
    memory.delete_preference("response.language", deleted_at="2026-10-03T00:00:00Z")

    result = memory.set_preference(
        "response.language", "Chinese", updated_at="2026-10-04T00:00:00Z"
    )

    assert result.status is s11.WriteStatus.CREATED
    assert result.revision == 3
    assert memory.list_deleted_preferences() == []
    assert "deleted" not in _payload(memory)


def test_repeated_delete_is_unchanged_and_keeps_deleted_at(s11, memory) -> None:
    _replay(memory)
    memory.delete_preference("response.language", deleted_at="2026-10-02T00:00:00Z")

    again = memory.delete_preference("response.language")

    assert again.status is s11.WriteStatus.UNCHANGED
    assert again.revision == 1
    [tombstone] = memory.list_deleted_preferences()
    assert tombstone.deleted_at == "2026-10-02T00:00:00Z"
    missing = memory.delete_preference("never.saved")
    assert (missing.status, missing.revision) == (s11.WriteStatus.UNCHANGED, 0)


@pytest.mark.parametrize("deleted_at", [OLD, "2026-09-30T00:00:00Z"])
def test_stale_explicit_delete_writes_nothing(s11, memory, deleted_at) -> None:
    _replay(memory)
    before = memory.preferences_path.read_bytes()

    with pytest.raises(s11.StalePreferenceUpdateError):
        memory.delete_preference("response.language", deleted_at=deleted_at)
    assert memory.preferences_path.read_bytes() == before


def _write_state(memory, preferences: list, deleted: list | None) -> None:
    payload = {"schema_version": 2, "user_scope": memory.scope_id, "preferences": preferences}
    if deleted is not None:
        payload["deleted"] = deleted
    memory.preferences_path.write_text(json.dumps(payload), encoding="utf-8")


def test_legacy_file_without_deleted_and_invalid_tombstones(s11, memory) -> None:
    record = {"key": "editor.indent", "value": "tabs", "source": "explicit", "updated_at": OLD}
    tombstone = {"key": "editor.indent", "deleted_at": OLD, "revision": 1, "source": "explicit"}
    _write_state(memory, [record], None)
    assert [item.key for item in memory.list_preferences()] == ["editor.indent"]
    assert memory.list_deleted_preferences() == []

    for preferences, deleted in (([], [tombstone, tombstone]), ([record], [tombstone])):
        _write_state(memory, preferences, deleted)
        with pytest.raises(s11.UserMemoryValidationError):
            memory.list_preferences()


def test_tombstone_is_scoped_to_one_user(s11, memory, tmp_path) -> None:
    _replay(memory)
    memory.delete_preference("response.language")
    bob = s11.UserMemory(tmp_path, user_id="bob")

    assert _replay(bob).status is s11.WriteStatus.CREATED
    assert [item.key for item in bob.list_active_preferences()] == ["response.language"]
    assert bob.list_deleted_preferences() == []
