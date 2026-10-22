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
