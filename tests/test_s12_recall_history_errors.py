"""s12 recall_history 工具入口：越过 schema 边界的参数应返回 ok=false，不抛穿 agent loop。

`dispatch_tool()` 声称先按 tool schema 检查参数，`recall_history` 的 schema 写明
`limit` 的 minimum=1 / maximum=MAX_RECALL_LIMIT；README「失败与边界行为」写明
limit 越界、只有标点的 query 要拒绝调用。retract_memory 已把同类校验错误转成
ok=false（tests/test_s12_retract_tool.py：「错误不抛穿 agent loop」），
recall_history 却让 RemoteMemoryValidationError 直接从 dispatch_tool 抛出。
"""

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


@pytest.mark.parametrize(
    "args",
    [
        {"query": "python deploy", "limit": 0},
        {"query": "python deploy", "limit": 11},
        {"query": "？？？"},
    ],
    ids=["limit-below-minimum", "limit-above-maximum", "punctuation-only-query"],
)
def test_invalid_recall_returns_ok_false_instead_of_raising(s12, store, args):
    before = store.path.read_bytes()
    out = json.loads(s12.dispatch_tool("recall_history", args))
    assert out["ok"] is False and out["error"]
    assert store.path.read_bytes() == before


def test_valid_recall_still_returns_hits(s12, store):
    out = json.loads(s12.dispatch_tool("recall_history", {"query": "python deploy", "limit": 10}))
    assert [hit["memory_id"] for hit in out["hits"]] == ["conv-1"]
