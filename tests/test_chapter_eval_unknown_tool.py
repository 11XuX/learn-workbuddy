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
