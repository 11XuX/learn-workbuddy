"""Reproduce: mini ``bash`` with non-UTF-8 output leaves an orphan tool_call.

The mini demo README promises every ``tool_call`` is followed by a
``tool_result`` or a ``tool_error`` with the same id, then an assistant message
("工具被拒绝、超时或执行失败时，Transcript 也不会只剩..."). ``ToolRegistry._bash``
runs ``subprocess.run(..., text=True)``, so stdout that is not valid UTF-8
(``cat`` of a PNG or a GBK note) raises ``UnicodeDecodeError`` inside
``tools.run``. It is not in ``MiniAgent.prompt``'s except list, so the prompt
raises and the transcript ends at ``tool_call``. ``read_file`` on the same
files already succeeds (``errors="replace"``). The png bytes (0x81, 0xff) fail
under UTF-8, ASCII, GBK and cp1252 locales. The demo's real-API loop calls
``tools.run`` with no ``try``, so ``tools.run`` itself must return too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_workbuddy.agent import MiniAgent
from mini_workbuddy.config import HarnessConfig
from mini_workbuddy.events import EventBus
from mini_workbuddy.storage import Storage
from mini_workbuddy.tools import ToolRegistry

FILES = {
    "notes-gbk.txt": "会议纪要：周五发布".encode("gbk"),
    "logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x81\xff\xfe",
}


def _run(tmp_path: Path, prompt: str):
    for name, data in FILES.items():
        (tmp_path / name).write_bytes(data)
    config = HarnessConfig(root_dir=tmp_path / "home")
    storage = Storage(config)
    tools = ToolRegistry(config, storage)
    agent = MiniAgent(storage, tools, EventBus())
    session = storage.create_session(str(tmp_path), "pytest session")
    try:
        agent.prompt(session, prompt)
        if prompt.startswith("bash "):  # examples/mini_workbuddy_demo path
            tools.run("bash", prompt[5:], session)
    except Exception as exc:  # the REST/ACP caller would see this
        raised = f"{type(exc).__name__}: {exc}"
    else:
        raised = None
    return raised, [event["type"] for event in storage.read_transcript(session)]


@pytest.mark.parametrize(
    "prompt",
    ["bash cat notes-gbk.txt", "bash cat logo.png"],
    ids=["gbk-text", "png"],
)
def test_bash_non_utf8_output_closes_tool_call(tmp_path, prompt):
    raised, types = _run(tmp_path, prompt)
    assert raised is None, raised
    assert types[1] == "tool_call"
    assert types[2] in {"tool_result", "tool_error"}
    assert types[-1] == "message"


def test_read_file_same_bytes_already_closes_tool_call(tmp_path):
    raised, types = _run(tmp_path, "read logo.png")
    assert raised is None
    assert types == ["message", "tool_call", "tool_result", "message"]
