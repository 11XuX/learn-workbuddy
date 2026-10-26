"""Reproduce: mini ``bash`` ignores ``~`` in the session cwd.

A client may create a session with ``cwd: "~/proj"`` (REST ``/api/v1/runs``,
``/api/v1/sessions`` or ACP ``session/new``).  ``ToolRegistry`` already treats
that cwd as the expanded home path in two places: ``_resolve_session_path``
(``read_file``) calls ``expanduser()``, and ``build_subprocess_env`` sets
``HOME``/``PWD`` to the expanded workspace ("HOME and PWD point at the session
workspace").  ``_bash`` passes the literal string to ``subprocess.run(cwd=...)``,
so ``pwd`` / ``bash cat notes.txt`` in the same session fail with
``[Errno 2] No such file or directory: '~/proj'`` while ``read notes.txt``
works.  The bash tool is described as "Run a shell command in the session cwd".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_workbuddy.agent import MiniAgent
from mini_workbuddy.config import HarnessConfig
from mini_workbuddy.events import EventBus
from mini_workbuddy.storage import Storage
from mini_workbuddy.tools import ToolRegistry


def _run(tmp_path: Path, monkeypatch, cwd: str, prompt: str):
    home = tmp_path / "user"
    (home / "proj").mkdir(parents=True)
    (home / "proj" / "notes.txt").write_text("hello from proj\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)  # a literal "~" never exists here
    config = HarnessConfig(root_dir=tmp_path / "state")
    storage = Storage(config)
    agent = MiniAgent(storage, ToolRegistry(config, storage), EventBus())
    session = storage.create_session(cwd, "pytest session")
    agent.prompt(session, prompt)
    events = storage.read_transcript(session)
    tool_events = [e for e in events if e["type"] in ("tool_result", "tool_error")]
    return home.resolve(), tool_events[0]


@pytest.mark.parametrize(
    ("cwd", "prompt", "expected"),
    [
        ("~/proj", "pwd", "{home}/proj\n"),
        ("~/proj", "bash cat notes.txt", "hello from proj\n"),
        ("~", "bash cat proj/notes.txt", "hello from proj\n"),
    ],
    ids=["pwd", "cat-relative", "home-root"],
)
def test_bash_runs_in_expanded_tilde_cwd(tmp_path, monkeypatch, cwd, prompt, expected):
    home, event = _run(tmp_path, monkeypatch, cwd, prompt)
    assert event["type"] == "tool_result", event.get("error")
    assert event["content"] == expected.format(home=home)
    assert event["exit_code"] == 0



def test_bash_missing_component_before_dotdot(tmp_path, monkeypatch):
    _, event = _run(
        tmp_path, monkeypatch, "missing/../user/proj", "pwd"
    )
    assert event["type"] == "tool_error", event


def test_read_file_keeps_tilde_cwd_expansion(tmp_path, monkeypatch):
    _, event = _run(
        tmp_path, monkeypatch, "~/proj", "read notes.txt"
    )
    assert event["type"] == "tool_result", event.get("error")
    assert event["content"] == "hello from proj\n"
    assert event["exit_code"] == 0
