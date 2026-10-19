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
    db_path = tmp_path / "home" / "workbuddy.db"

    def sql(query):
        db = sqlite3.connect(str(db_path))
        try:
            rows = db.execute(query).fetchall()
            db.commit()
            return rows
        finally:
            db.close()

    def run(answers):
        answers = list(answers)
        saved = sys.modules.pop("anthropic", None)
        monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
        monkeypatch.setenv("MODEL_ID", "offline-test-model")
        monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
        monkeypatch.setattr(sys, "argv", [str(CODE)])
        monkeypatch.setattr(builtins, "input",
                            lambda prompt="": answers.pop(0) if answers else "q")
        monkeypatch.chdir(tmp_path)
        try:
            runpy.run_path(str(CODE), run_name="__main__")
        except RuntimeError as exc:  # stub: messages.create() was reached
            assert "messages.create()" in str(exc)
            return True
        finally:
            sys.modules.pop("anthropic", None)
            if saved is not None:
                sys.modules["anthropic"] = saved
        return False
    return run, sql


def test_archive_is_a_local_command_not_a_model_prompt(run_repl):
    run, sql = run_repl
    assert not run(["/archive", "q"]), "/archive was sent to the model"
    assert ("/archive",) not in sql("SELECT content FROM messages")


def test_archive_soft_deletes_only_the_current_session(run_repl):
    run, sql = run_repl
    run(["q"])  # an earlier session the user still has
    sql("UPDATE sessions SET id = 'sess_earlier'")
    run(["/archive", "hello", "q"])
    sessions = sql("SELECT id, status FROM sessions ORDER BY rowid")
    assert sessions[0] == ("sess_earlier", "active")
    current = sessions[1][0]
    assert sessions[1][1] == "archived"
    assert sql("SELECT content FROM messages WHERE session_id = "
               f"'{current}'") == [], "archived session kept receiving messages"


# --- Verifier adversarial tests (#58) ---
# /archive soft-deletes only the current session, keeps all data, and stops the REPL.
def test_archive_preserves_data_and_stops_input(run_repl, monkeypatch, capsys):
    run, sql = run_repl
    original_run_path = runpy.run_path
    reads = []

    def answer(prompt=""):
        assert not reads, "归档后仍读取输入"
        reads.append(True)
        current = sql("SELECT id FROM sessions")[0][0]
        sql(
            "INSERT INTO sessions "
            "(id, cwd, title, status, created_at, updated_at) "
            "VALUES ('other', '.', 'other', 'active', "
            "'2000-01-01', '9999-12-31')"
        )
        sql(
            "INSERT INTO messages (session_id, role, content, created_at) "
            "SELECT id, 'user', 'keep:' || id, '2000-01-01' FROM sessions"
        )
        assert current != "other"
        return "  /archive  "

    def invoke(*args, **kwargs):
        monkeypatch.setattr(builtins, "input", answer)
        return original_run_path(*args, **kwargs)

    monkeypatch.setattr(runpy, "run_path", invoke)
    assert not run([])
    assert len(reads) == 1
    assert sql("SELECT status FROM sessions WHERE id = 'other'") == [("active",)]
    assert sql("SELECT status FROM sessions WHERE id != 'other'") == [
        ("archived",)
    ]
    assert sql(
        "SELECT session_id, role, content FROM messages ORDER BY session_id"
    ) == [
        (sid, "user", "keep:" + sid)
        for (sid,) in sql("SELECT id FROM sessions ORDER BY id")
    ]
    output = capsys.readouterr().out
    assert "命令: /stats | /sessions | /tools | /archive | q 退出" in output
    assert "数据库已关闭" in output
