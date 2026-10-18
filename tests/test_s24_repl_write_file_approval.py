"""Reproduce: the s24 REPL can never approve ``write_file``.

``write_file`` is registered as ``ASK`` ("Write a workspace file after
approval."). s04 routes ASK to an approver that asks the user (y/N, EOF or
Ctrl-C fails closed). The s24 README says a single-chapter ``ASK`` must not
drift in the comprehensive chapter. But the REPL builds
``ComprehensiveAgent()`` without an ``approval_resolver``, so every
``write_file`` tool call is denied without asking: ASK silently became DENY.
"""

from __future__ import annotations

import builtins
import os
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s24_comprehensive" / "code.py"


@pytest.fixture
def repl(tmp_path, monkeypatch):
    """Run the real REPL entry (user types ``q``) and return its globals."""
    answers: list[str] = ["q"]

    def fake_input(prompt: str = "") -> str:
        if not answers:
            raise EOFError
        return answers.pop(0)

    stub = str(ROOT / "tests" / "stubs")
    saved = sys.modules.pop("anthropic", None)
    monkeypatch.syspath_prepend(stub)
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "argv", [str(CODE)])
    monkeypatch.setattr(builtins, "input", fake_input)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    try:
        g = runpy.run_path(str(CODE), run_name="__main__")
    finally:
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved
    assert not answers, "REPL should have consumed the 'q' input"
    return g, answers, work


def _write(g, work):
    output = g["execute_tool"](
        "write_file", {"path": "note.txt", "content": "hello"},
        g["agent"].approval_resolver,
    )
    return output, work / "note.txt"


def test_repl_user_can_approve_write_file(repl):
    g, answers, work = repl
    assert g["check_permission"]("write_file") is g["PermissionDecision"].ASK
    answers.append("y")
    output, target = _write(g, work)
    assert output == "File written"
    assert target.read_text() == "hello"
    assert not answers, "approval prompt should read the user's answer"


@pytest.mark.parametrize("answer", ["n", "", None], ids=["no", "enter", "eof"])
def test_repl_rejected_or_cancelled_write_is_not_executed(repl, answer):
    g, answers, work = repl
    if answer is not None:
        answers.append(answer)
    output, target = _write(g, work)
    assert "approval required" in output
    assert not target.exists()
