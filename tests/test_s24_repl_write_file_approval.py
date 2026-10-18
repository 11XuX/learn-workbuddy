"""Reproduce: the s24 REPL can never approve ``write_file``.

``write_file`` is ``ASK`` ("Write a workspace file after approval."), but the
REPL builds ``ComprehensiveAgent()`` with no approval resolver, so ASK always
becomes DENY. We run the real REPL entry, grab the agent it built from the
frame calling ``input()`` (no module-global name), and drive the same
``execute_tool(..., agent.approval_resolver)`` call that ``run()`` makes.
The offline stub forbids fake model output, so ``run()`` itself is not driven.
"""

from __future__ import annotations

import builtins
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s24_comprehensive" / "code.py"


@pytest.fixture
def repl(tmp_path, monkeypatch):
    answers, seen = ["q"], {}

    def fake_input(prompt: str = "") -> str:
        frame = sys._getframe(1)
        for value in {**frame.f_globals, **frame.f_locals}.values():
            if type(value).__name__ == "ComprehensiveAgent":
                seen["agent"] = value
        if not answers:
            raise EOFError
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    saved = sys.modules.pop("anthropic", None)
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "argv", [str(CODE)])
    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.chdir(tmp_path)
    try:
        g = runpy.run_path(str(CODE), run_name="__main__")
    finally:
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved

    def write(answer):
        answers.append(answer)
        out = g["execute_tool"]("write_file", {"path": "n.txt", "content": "hi"},
                                seen["agent"].approval_resolver)
        return out, tmp_path / "n.txt", answers
    return write


def test_repl_user_can_approve_write_file(repl):
    out, target, unread = repl("y")
    assert out == "File written" and target.read_text() == "hi"
    assert not unread, "the approval prompt must read the user's answer"


@pytest.mark.parametrize("answer", ["n", "", "yes please", EOFError(),
                                    KeyboardInterrupt()],
                         ids=["no", "enter", "not-y", "eof", "ctrl-c"])
def test_repl_rejected_or_cancelled_write_is_not_executed(repl, answer):
    out, target, _ = repl(answer)
    assert "approval required" in out and not target.exists()


# Verifier adversarial tests (#55): existing files survive rejection, every
# write asks again, and an approved escape is still blocked by _scoped_path.
@pytest.mark.parametrize("answer", ["n", "", "yes please", EOFError(),
                                    KeyboardInterrupt()],
                         ids=["no", "enter", "not-y", "eof", "ctrl-c"])
def test_rejected_write_preserves_existing_file(repl, answer):
    target = Path.cwd() / "n.txt"
    target.write_text("original", encoding="utf-8")
    out, actual, unread = repl(answer)
    assert actual == target
    assert "approval required" in out
    assert target.read_text(encoding="utf-8") == "original"
    assert not unread


@pytest.mark.parametrize("answer", ["Y", " y "])
def test_each_write_needs_fresh_approval(repl, answer, capsys):
    capsys.readouterr()
    out, target, unread = repl(answer)
    assert out == "File written"
    assert target.read_text(encoding="utf-8") == "hi"
    assert not unread
    prompt = capsys.readouterr().out
    assert "write_file" in prompt and "n.txt" in prompt

    target.write_text("keep", encoding="utf-8")
    out, _, unread = repl("n")
    assert "approval required" in out
    assert target.read_text(encoding="utf-8") == "keep"
    assert not unread
    prompt = capsys.readouterr().out
    assert "write_file" in prompt and "n.txt" in prompt


def test_approved_escape_is_still_blocked(repl, monkeypatch, capsys):
    import inspect

    state = inspect.getclosurevars(repl).nonlocals
    execute = state["g"]["execute_tool"]
    approve = state["seen"]["agent"].approval_resolver
    workspace = Path.cwd()
    nested = workspace / "nested"
    nested.mkdir()
    outside = workspace / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    monkeypatch.chdir(nested)
    calls = []

    def allow(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr(builtins, "input", allow)
    capsys.readouterr()
    out = execute("write_file", {"path": "../outside.txt", "content": "bad"},
                  approve)
    assert len(calls) == 1
    assert out.startswith("Tool failed:") and "escapes workspace" in out
    assert outside.read_text(encoding="utf-8") == "keep"
    prompt = capsys.readouterr().out
    assert "write_file" in prompt and "../outside.txt" in prompt
