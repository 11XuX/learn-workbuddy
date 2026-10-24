"""s21: the REPL's "Auto-set title from first query" misses tool-using turns.

code.py sets the title only when ``len(history) == 2`` after the first
agent_loop. A first turn where the model calls ``bash`` leaves history at 4
items (user, tool_use, tool_result, final text), so the session keeps
``'New Session'`` in /sessions and in the sessions table. A second query
must not overwrite the title taken from the first one.
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
    replies = [NS(type="text", text="done"), NS(type="text", text="again")]
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
    answers = ["list the files here", "and the hidden ones?", "q"]
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


@pytest.mark.parametrize(
    "query_length, manual_title",
    [(50, None), (51, None), (51, "manual session title")],
    ids=["exactly-50", "truncate-51", "preserve-manual-title"],
)
def test_multi_tool_turns_title_boundaries_and_manual_override(
    tmp_path, monkeypatch, query_length, manual_title
):
    first_query = "a" * query_length

    def tool_block(number):
        return NS(
            type="tool_use",
            id=f"tool-{number}",
            name="bash",
            input={"command": "echo hi"},
        )

    replies = [
        tool_block(1),
        tool_block(2),
        NS(type="text", text="first answer"),
        tool_block(3),
        NS(type="text", text="second answer"),
    ]

    def create(**_kwargs):
        block = replies.pop(0)
        return NS(
            stop_reason="tool_use" if block.type == "tool_use" else "end_turn",
            content=[block],
            usage=NS(input_tokens=1, output_tokens=1),
        )

    module = types.ModuleType("anthropic")
    module.Anthropic = lambda **_kw: NS(messages=NS(create=create))
    answers = ["", "/sessions", first_query]
    if manual_title is not None:
        answers.append(f"/title {manual_title}")
    answers.extend(["a different second question", "q"])

    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "argv", [str(CODE)])
    monkeypatch.setattr(builtins, "input", lambda prompt="": answers.pop(0))
    monkeypatch.chdir(tmp_path)

    runpy.run_path(str(CODE), run_name="__main__")

    expected = (
        manual_title
        if manual_title is not None
        else first_query[:50] + ("..." if query_length > 50 else "")
    )
    db = sqlite3.connect(str(tmp_path / "home" / "workbuddy.db"))
    try:
        assert db.execute("SELECT title FROM sessions").fetchall() == [(expected,)]
        assert db.execute(
            "SELECT SUM(call_count) FROM tool_usage WHERE tool_name='bash'"
        ).fetchone() == (3,)
    finally:
        db.close()
    assert not replies
    assert not answers
