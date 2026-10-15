"""Repro: an explicit ``bash `` prompt is rerouted by keyword matching.

``MiniAgent._plan`` has a dedicated ``bash <command>`` branch (the demo and
tests drive the shell with prompts like ``bash rm -rf .``).  But the
``list files`` substring check and the unanchored ``read\\s+(.+)`` search run
first, so a shell command that merely contains those words never reaches
bash: ``... | while read l; ...`` becomes ``read_file("l; do ...")`` and
``bash echo list files`` becomes ``ls -la``.  The prefix is also cut from
the unstripped text, so ``"  bash ..."`` loses the first letter of the command.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_workbuddy.agent import MiniAgent
from mini_workbuddy.config import HarnessConfig
from mini_workbuddy.events import EventBus
from mini_workbuddy.storage import Storage
from mini_workbuddy.tools import ToolRegistry


@pytest.mark.parametrize(
    ("prompt", "expected_stdout"),
    [
        ("bash printf 'a\\nb\\n' | while read l; do echo \"$l\"; done", "a\nb\n"),
        ("bash echo list files", "list files\n"),
        ("  bash echo hi", "hi\n"),
    ],
)
def test_bash_prefix_runs_the_given_command(
    tmp_path: Path, prompt: str, expected_stdout: str
) -> None:
    config = HarnessConfig(root_dir=tmp_path / "home")
    storage = Storage(config)
    agent = MiniAgent(storage, ToolRegistry(config, storage), EventBus())
    session = storage.create_session(str(tmp_path), "bash prefix")

    agent.prompt(session, prompt)

    calls = [
        (e["tool"], e["argument"])
        for e in storage.read_transcript(session)
        if e.get("type") == "tool_call"
    ]
    assert calls == [("bash", prompt.strip()[len("bash "):])]
    results = [
        e["content"]
        for e in storage.read_transcript(session)
        if e.get("type") == "tool_result"
    ]
    assert results == [expected_stdout]


def test_bash_prefix_still_hits_command_policy(tmp_path: Path) -> None:
    config = HarnessConfig(root_dir=tmp_path / "home")
    storage = Storage(config)
    agent = MiniAgent(storage, ToolRegistry(config, storage), EventBus())
    session = storage.create_session(str(tmp_path), "bash deny")
    (tmp_path / "x").write_text("keep", encoding="utf-8")

    agent.prompt(session, "bash rm -rf x; while read l; do :; done")

    errors = [
        (e["tool"], e["error"])
        for e in storage.read_transcript(session)
        if e.get("type") == "tool_error"
    ]
    assert len(errors) == 1 and errors[0][0] == "bash", errors
    assert "denied" in errors[0][1]
    assert (tmp_path / "x").exists()
