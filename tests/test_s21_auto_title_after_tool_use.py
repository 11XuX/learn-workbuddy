"""s21: the REPL's "Auto-set title from first query" misses tool-using turns.

code.py sets the title only when ``len(history) == 2`` after the first
agent_loop. A first turn where the model calls ``bash`` leaves history at 4
items (user, tool_use, tool_result, final text), so the session keeps
``'New Session'`` in /sessions and in the sessions table.
The fake client below plays the model; no network is used.
"""

from __future__ import annotations

import builtins
import runpy
import sqlite3
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s21_sqlite_database" / "code.py"
NS = types.SimpleNamespace


def _fake_anthropic(first_turn_uses_tool: bool):
    replies = [NS(type="text", text="done")]
    if first_turn_uses_tool:
        replies.insert(0, NS(type="tool_use", id="t1", name="bash", input={"command": "echo hi"}))

    def create(**_kwargs):
        block = replies.pop(0)
        stop = "tool_use" if block.type == "tool_use" else "end_turn"
        return NS(stop_reason=stop, content=[block], usage=NS(input_tokens=1, output_tokens=1))

    module = types.ModuleType("anthropic")
    module.Anthropic = lambda **_kw: NS(messages=NS(create=create))
    return module


@pytest.mark.parametrize("uses_tool", [False, True], ids=["text-only", "bash-tool"])
def test_first_query_becomes_title(tmp_path, monkeypatch, uses_tool):
    answers = ["list the files here", "q"]
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic(uses_tool))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "argv", [str(CODE)])
    monkeypatch.setattr(builtins, "input", lambda prompt="": answers.pop(0))
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(CODE), run_name="__main__")
    db = sqlite3.connect(str(tmp_path / "home" / "workbuddy.db"))
    titles = [t for (t,) in db.execute("SELECT title FROM sessions")]
    db.close()
    assert titles == ["list the files here"]
