"""s12 retract_memory 工具入口：经 harness 撤回，错误不抛穿 agent loop。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from test_remote_memory import s12  # noqa: F401  复用 s12 模块加载 fixture


@pytest.fixture
def store(s12, tmp_path, monkeypatch):
    st = s12.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")
    at = s12._iso(datetime(2026, 8, 1, 12, tzinfo=timezone.utc))
    src = s12.MemorySource(source_id="t1", source_type="transcript", title="t", captured_at=at)
    st.append(kind=s12.MemoryKind.CONVERSATION, content="python deploy notes",
              summary="python deploy notes", source=src, memory_id="conv-1")
    monkeypatch.setattr(s12, "DEFAULT_STORE", st)
    monkeypatch.setattr(s12, "DEFAULT_RECALL", None)
    return st


def _call(s12, args):
    return json.loads(s12.dispatch_tool("retract_memory", args))


def test_retract_hides_from_recall_keeps_audit_and_survives_restart(s12, store):
    out = _call(s12, {"memory_id": "conv-1", "reason": "用户要求忘掉"})
    assert out["ok"] is True and out["retracts"] == "conv-1"
    assert out["memory_id"] == s12._retraction_id("conv-1")
    for st in (store, s12.RemoteMemoryStore(store.path, user_id="u")):
        assert s12.RecallEngine(st).recall("python deploy").hits == ()
        records = {r.memory_id: r for r in st.read_all()}
        assert "conv-1" in records
        event = records[out["memory_id"]]
        assert event.kind is s12.MemoryKind.RETRACTION
        assert event.source.source_type == "model_tool"


def test_repeat_returns_same_event_and_original_reason(s12, store):
    first = _call(s12, {"memory_id": "conv-1", "reason": "第一次的理由"})
    size = store.path.stat().st_size
    again = _call(s12, {"memory_id": "conv-1", "reason": "另一个理由"})
    assert again == first and again["reason"] == "第一次的理由"
    assert store.path.stat().st_size == size


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
def test_bad_input_is_ok_false_and_file_unchanged(s12, store, args):
    before = store.path.read_bytes()
    out = _call(s12, args)
    assert out["ok"] is False and out["error"]
    assert store.path.read_bytes() == before


def test_retracting_a_retraction_is_ok_false(s12, store):
    rid = _call(s12, {"memory_id": "conv-1", "reason": "r"})["memory_id"]
    before = store.path.read_bytes()
    assert _call(s12, {"memory_id": rid, "reason": "r"})["ok"] is False
    assert store.path.read_bytes() == before


def test_handler_never_raises_type_error_on_bad_args(s12, store):
    for name in s12.TOOL_HANDLERS:
        for args in ({}, {"unexpected": 1}):
            assert json.loads(s12.dispatch_tool(name, args))["ok"] is False
    out = json.loads(s12.dispatch_tool("recall_history", {"query": "q", "limit": True}))
    assert out["ok"] is False


def test_tools_registry_and_prompt_convention(s12, store):
    names = [t["name"] for t in s12.TOOLS]
    assert "retract_memory" in names and set(names) == set(s12.TOOL_HANDLERS)
    schema = next(t for t in s12.TOOLS if t["name"] == "retract_memory")["input_schema"]
    assert set(schema["properties"]) == set(schema["required"]) == {"memory_id", "reason"}
    prompt = s12.build_system_prompt(store)
    assert "only when the user explicitly asks" in prompt
    assert "never guess" in prompt
