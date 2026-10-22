"""s12 recall_history 工具入口：参数校验错误应返回 ok=false，不抛穿 agent loop。

README「失败与边界行为」写明 limit 不在 1 到 10、只有标点的 query 要拒绝调用；
README 架构图 `dispatch_tool 参数检查 -->|不合法| ok=false`；同章 retract_memory
已把校验错误转成 ok=false。recall_history 却让 RemoteMemoryValidationError 从
dispatch_tool 抛出，agent_loop / main() 没有兜底，REPL 带 traceback 退出。
存储损坏（RemoteMemoryCorruptionError）不是参数错误，必须照常抛出，不能被吞成 ok=false。
本文件自行加载模块，不依赖其他测试文件的 fixture。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def s12(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    spec = importlib.util.spec_from_file_location("s12_recall_errors", ROOT / "s12_cloud_memory" / "code.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "s12_recall_errors", module)
    spec.loader.exec_module(module)
    st = module.RemoteMemoryStore(tmp_path / "m.jsonl", user_id="u")
    at = module._iso(datetime(2026, 8, 1, 12, tzinfo=timezone.utc))
    src = module.MemorySource(source_id="t1", source_type="transcript", title="t", captured_at=at)
    st.append(kind=module.MemoryKind.CONVERSATION, content="python deploy notes",
              summary="python deploy notes", source=src, memory_id="conv-1")
    module.DEFAULT_STORE, module.DEFAULT_RECALL = st, None
    return module


@pytest.mark.parametrize(
    "args",
    [{"query": "python deploy", "limit": 0}, {"query": "python deploy", "limit": 11}, {"query": "？？？"}],
    ids=["limit-below-minimum", "limit-above-maximum", "punctuation-only-query"],
)
def test_invalid_recall_returns_ok_false_instead_of_raising(s12, args):
    before = s12.DEFAULT_STORE.path.read_bytes()
    out = json.loads(s12.dispatch_tool("recall_history", args))
    assert out["ok"] is False and out["error"]
    assert s12.DEFAULT_STORE.path.read_bytes() == before


def test_valid_recall_keeps_success_shape(s12):
    out = json.loads(s12.dispatch_tool("recall_history", {"query": "python deploy", "limit": 10}))
    assert "ok" not in out
    assert [hit["memory_id"] for hit in out["hits"]] == ["conv-1"]


def test_store_corruption_still_raises(s12, monkeypatch):
    # 跳过 prompt 组装里的读盘，让损坏只在 recall 读 store 时暴露。
    monkeypatch.setattr(s12, "build_system_prompt", lambda store: "system")
    with s12.DEFAULT_STORE.path.open("ab") as fh:
        fh.write(b"{not json\n")
    with pytest.raises(s12.RemoteMemoryCorruptionError):
        s12.dispatch_tool("recall_history", {"query": "python deploy", "limit": 5})


def test_recall_limit_matrix_and_success_contract(s12):
    before = s12.DEFAULT_STORE.path.read_bytes()
    invalid_limits = (
        -100, -1, 0, 11, 100, 10**100,
        True, False, 1.0, 5.9, 10.9,
        "5", "11", None, [], {},
    )
    for limit in invalid_limits:
        out = json.loads(s12.dispatch_tool(
            "recall_history", {"query": "python deploy", "limit": limit}
        ))
        assert out.get("ok") is False, repr(limit)
        assert out.get("error"), repr(limit)
        assert s12.DEFAULT_STORE.path.read_bytes() == before

    for query in ("？？？", "!!!...", "", " \t\n"):
        out = json.loads(s12.dispatch_tool(
            "recall_history", {"query": query}
        ))
        assert out.get("ok") is False, repr(query)
        assert out.get("error"), repr(query)
        assert s12.DEFAULT_STORE.path.read_bytes() == before

    for limit in range(1, 11):
        out = json.loads(s12.dispatch_tool(
            "recall_history", {"query": "python deploy", "limit": limit}
        ))
        assert "ok" not in out
        assert out["query"]["limit"] == limit
        assert [hit["memory_id"] for hit in out["hits"]] == ["conv-1"]
        assert s12.DEFAULT_STORE.path.read_bytes() == before


def test_agent_loop_pairs_mixed_recall_calls(s12, monkeypatch):
    from types import SimpleNamespace

    arguments = [
        {"query": "python deploy", "limit": 0},
        {"query": "python deploy", "limit": 11},
        {"query": "？？？"},
        {"query": "python deploy", "limit": True},
        {"query": "python deploy", "limit": 1.0},
        {"query": "python deploy", "limit": "5"},
        {"query": "python deploy", "limit": 1},
    ]
    blocks = [
        SimpleNamespace(
            type="tool_use", id=f"recall-{index}",
            name="recall_history", input=args,
        )
        for index, args in enumerate(arguments)
    ]
    responses = iter([
        SimpleNamespace(content=blocks, stop_reason="tool_use"),
        SimpleNamespace(
            content=[SimpleNamespace(type="text", text="done")],
            stop_reason="end_turn",
        ),
    ])

    def create(**kwargs):
        return next(responses)

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(
        s12, "runtime_client", lambda: (client, "offline-test-model")
    )
    before = s12.DEFAULT_STORE.path.read_bytes()
    messages = [{"role": "user", "content": "search history"}]
    s12.agent_loop(messages)

    assert [message["role"] for message in messages] == [
        "user", "assistant", "user", "assistant"
    ]
    results = messages[2]["content"]
    assert [result["tool_use_id"] for result in results] == [
        block.id for block in blocks
    ]
    assert all(result["type"] == "tool_result" for result in results)
    for result in results[:-1]:
        out = json.loads(result["content"])
        assert out.get("ok") is False
        assert out.get("error")
    success = json.loads(results[-1]["content"])
    assert "ok" not in success
    assert [hit["memory_id"] for hit in success["hits"]] == ["conv-1"]
    assert s12.DEFAULT_STORE.path.read_bytes() == before
