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


# --- Verifier adversarial tests (#85) ---


def _tools(tmp_path: Path, **config_kwargs):
    config = HarnessConfig(root_dir=tmp_path / "home", **config_kwargs)
    storage = Storage(config)
    return ToolRegistry(config, storage), storage.create_session(str(tmp_path), "adv")


def test_adversarial_bash_mixed_utf8_and_invalid_bytes_keeps_text(tmp_path):
    # UTF-8 Chinese survives; only the bad bytes become U+FFFD (on stdout and stderr).
    (tmp_path / "mixed.txt").write_bytes("纪要：".encode() + b"\x81\xff" + "发布\n".encode())
    tools, session = _tools(tmp_path)
    result = tools.run("bash", "cat mixed.txt; cat mixed.txt 1>&2; exit 3", session)
    line = "纪要：\ufffd\ufffd发布\n"
    assert result.content == line + "\n--- stderr ---\n" + line
    assert result.exit_code == 3


def test_adversarial_bash_externalized_non_utf8_output_is_written(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"\xff" * 2048 + "尾".encode())
    tools, session = _tools(tmp_path, tool_result_threshold=1024)
    result = tools.run("bash", "cat big.bin", session)
    assert result.externalized_path
    saved = Path(result.externalized_path).read_text(encoding="utf-8")
    assert saved == "\ufffd" * 2048 + "尾"


def test_adversarial_bash_utf8_output_under_c_locale(tmp_path):
    # Explicit encoding="utf-8": a non-UTF-8 locale must not mangle UTF-8 text.
    import os, subprocess, sys
    (tmp_path / "zh.txt").write_bytes("会议纪要".encode())
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
        "from mini_workbuddy.config import HarnessConfig\n"
        "from mini_workbuddy.storage import Storage\n"
        "from mini_workbuddy.tools import ToolRegistry\n"
        "c = HarnessConfig(root_dir=Path(sys.argv[2]) / 'home'); s = Storage(c)\n"
        "r = ToolRegistry(c, s).run('bash', 'cat zh.txt', s.create_session(sys.argv[2], 'c'))\n"
        "print(ascii(r.content))\n"
    )
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
    repo = str(Path(__file__).resolve().parents[1])
    out = subprocess.run([sys.executable, "-c", code, repo, str(tmp_path)],
                         capture_output=True, text=True, env=env, check=True, timeout=30).stdout
    assert out.strip() == ascii("会议纪要")
