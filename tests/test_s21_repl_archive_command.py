"""Reproduce: the s21 REPL lists ``/archive`` but never handles it.

The banner prints ``命令: /stats | /sessions | /tools | /archive | q 退出`` and
``WorkBuddyDB.archive_session`` exists (README: 会话 CRUD — 创建、列表、归档),
but the loop has no ``/archive`` branch, so the command is saved as a user
message and sent to the model. The offline stub raises on any model call,
so reaching the model shows up as ``RuntimeError``.
"""

from __future__ import annotations

import builtins
import runpy
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s21_sqlite_database" / "code.py"


@pytest.fixture
def run_repl(tmp_path, monkeypatch):
    def run(answers):
        answers = list(answers)

        def fake_input(prompt: str = "") -> str:
            if not answers:
                raise EOFError
            return answers.pop(0)

        saved = sys.modules.pop("anthropic", None)
        monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
        monkeypatch.setenv("MODEL_ID", "offline-test-model")
        monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
        monkeypatch.setattr(sys, "argv", [str(CODE)])
        monkeypatch.setattr(builtins, "input", fake_input)
        monkeypatch.chdir(tmp_path)
        model_called = False
        try:
            runpy.run_path(str(CODE), run_name="__main__")
        except RuntimeError as exc:  # stub: messages.create() was reached
            model_called = "messages.create()" in str(exc)
            if not model_called:
                raise
        finally:
            sys.modules.pop("anthropic", None)
            if saved is not None:
                sys.modules["anthropic"] = saved
        db = sqlite3.connect(str(tmp_path / "home" / "workbuddy.db"))
        try:
            sessions = db.execute("SELECT id, status FROM sessions").fetchall()
            messages = db.execute("SELECT content FROM messages").fetchall()
        finally:
            db.close()
        return model_called, sessions, [m[0] for m in messages]
    return run


def test_archive_is_a_local_command_not_a_model_prompt(run_repl):
    model_called, sessions, messages = run_repl(["/archive", "q"])
    assert not model_called, "/archive was sent to the model"
    assert "/archive" not in messages
    assert len(sessions) == 1


def test_archive_marks_the_current_session_archived(run_repl):
    _, sessions, _ = run_repl(["/archive", "q"])
    assert [status for _, status in sessions] == ["archived"]
