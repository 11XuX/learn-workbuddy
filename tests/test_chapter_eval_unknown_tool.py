"""Repro: the shared ``--eval`` loop crashes when the model names an unknown tool.

``run_chapter_eval`` wraps ``ToolRegistry.run`` in ``try/except`` and turns a
failure (including its own ``KeyError("unknown tool")``) into a ``tool_error``
trace event plus a ``Tool failed: ...`` result for the model.  But
``_tool_argument`` runs *before* that ``try`` and raises ``KeyError`` for any
name outside bash/read_file/tool_search, so one hallucinated tool name aborts
the whole benchmark: traceback, no ``tool_error``, no ``case_end``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mini_workbuddy import chapter_demo, providers
from mini_workbuddy.providers import ModelTurn, OfflineMockProvider, ToolCall

ROOT = Path(__file__).resolve().parents[1]


class UnknownToolThenDone(OfflineMockProvider):
    """Turn 1 calls a tool that was never offered; turn 2 finishes."""

    def create(self, request):  # type: ignore[override]
        self._step += 1
        if self._step == 1:
            call = ToolCall(id="call_1", name="list_files", arguments={"path": "."})
            return ModelTurn(text="", tool_calls=[call], raw_assistant={"step": 1})
        return ModelTurn(text="no such tool, stopping. DONE", raw_assistant={"step": 2})


def test_unknown_tool_is_recorded_as_tool_error_not_a_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINI_WORKBUDDY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(providers, "select_provider", lambda _name=None: UnknownToolThenDone())
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setattr(sys, "argv", ["code.py", "--eval", "--trace", str(trace)])

    chapter_demo.run_chapter_eval(
        str(ROOT / "s01_agent_loop" / "code.py"),
        {"chapter": "s01", "adds": ["agent loop"]},
    )

    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    types = [event["type"] for event in events]
    assert "tool_error" in types
    error = next(event for event in events if event["type"] == "tool_error")
    assert error["name"] == "list_files"
    assert "unknown tool" in error["error"]
    # Trajectory order: the attempted call is still traced before its error.
    assert types.index("tool_call") < types.index("tool_error")
    assert types[-1] == "case_end"
    out = capsys.readouterr().out
    assert "Tool failed:" in out
    # Scope: only the crash is fixed; scoring is unchanged (attempt counts).
    assert "RESULT: OK" in out


def test_mixed_unknown_calls_preserve_order_unicode_and_rejection(
    tmp_path, monkeypatch, capsys
):
    from mini_workbuddy.audit import AuditLog
    from mini_workbuddy.tools import ToolRegistry

    payload = {
        "path": "中文目录/资料.txt",
        "command": "printf 不应执行",
        "nested": {"说明": ["中文", 42]},
    }
    calls = [
        ToolCall(id="unknown_1", name="list_files", arguments=payload),
        ToolCall(id="known_1", name="tool_search", arguments={"query": ""}),
        ToolCall(id="unknown_2", name="execute_command", arguments=payload),
    ]
    audit_events = []
    delivered = []
    executed = []
    original_append = AuditLog.append

    def record_audit(self, action, data, *args, **kwargs):
        audit_events.append((action, dict(data)))
        return original_append(self, action, data, *args, **kwargs)

    def forbidden_executor(self, *args, **kwargs):
        executed.append(True)
        raise AssertionError("未知工具不得进入 bash/read_file 执行器")

    class MixedCallsThenDone(OfflineMockProvider):
        def create(self, request):
            self._step += 1
            if self._step == 1:
                offered = {tool.name for tool in request.tools}
                assert "tool_search" in offered
                assert "list_files" not in offered
                assert "execute_command" not in offered
                return ModelTurn(
                    text="", tool_calls=calls, raw_assistant={"step": 1}
                )
            return ModelTurn(text="DONE", raw_assistant={"step": 2})

        def format_tool_results(self, results):
            delivered.extend(results)
            return super().format_tool_results(results)

    provider = MixedCallsThenDone()
    monkeypatch.setenv("MINI_WORKBUDDY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(providers, "select_provider", lambda _name=None: provider)
    monkeypatch.setattr(AuditLog, "append", record_audit)
    monkeypatch.setattr(ToolRegistry, "_bash", forbidden_executor)
    monkeypatch.setattr(ToolRegistry, "_read_file", forbidden_executor)
    trace = tmp_path / "mixed.jsonl"
    monkeypatch.setattr(
        sys, "argv", ["code.py", "--eval", "--trace", str(trace)]
    )

    chapter_demo.run_chapter_eval(
        str(ROOT / "s01_agent_loop" / "code.py"),
        {"chapter": "s01", "adds": ["agent loop"]},
    )

    events = [
        json.loads(line)
        for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    trajectory = [
        (event["type"], event["id"], event["name"])
        for event in events
        if event["type"] in {"tool_call", "tool_error", "tool_result"}
    ]
    assert trajectory == [
        ("tool_call", "unknown_1", "list_files"),
        ("tool_error", "unknown_1", "list_files"),
        ("tool_call", "known_1", "tool_search"),
        ("tool_result", "known_1", "tool_search"),
        ("tool_call", "unknown_2", "execute_command"),
        ("tool_error", "unknown_2", "execute_command"),
    ]
    errors = [event for event in events if event["type"] == "tool_error"]
    assert len(errors) == 2
    assert all("unknown tool" in event["error"] for event in errors)
    assert [call.id for call, _output in delivered] == [
        call.id for call in calls
    ]
    assert all(
        delivered[index][1].startswith("Tool failed:")
        for index in (0, 2)
    )
    assert not delivered[1][1].startswith("Tool failed:")
    assert not executed

    tool_audit = [
        (action, data)
        for action, data in audit_events
        if action in {"eval_tool_call", "eval_tool_error", "eval_tool_result"}
    ]
    assert [(action, data["tool"]) for action, data in tool_audit] == [
        ("eval_tool_call", "list_files"),
        ("eval_tool_error", "list_files"),
        ("eval_tool_call", "tool_search"),
        ("eval_tool_result", "tool_search"),
        ("eval_tool_call", "execute_command"),
        ("eval_tool_error", "execute_command"),
    ]
    argument = json.dumps(payload, ensure_ascii=False)
    assert tool_audit[0][1]["argument"] == argument
    assert tool_audit[4][1]["argument"] == argument
    end = events[-1]
    assert end["type"] == "case_end"
    assert end["tool_calls"] == 3
    assert end["audit_verified"] is True
    assert end["done"] is True

    out = capsys.readouterr().out
    assert f"[tool_call] list_files: {argument}" in out
    assert f"[tool_call] execute_command: {argument}" in out
    assert out.count("Tool failed:") == 2
    assert "Tool calls: 3" in out
    assert "RESULT: OK" in out
