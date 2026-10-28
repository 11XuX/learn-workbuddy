"""s05 bash: non-UTF-8 command output must not kill the Main process.

`main_process.run_bash` turns a timeout into "Error: ..." text so the agent
loop can feed it back to the model. Output bytes that are not valid UTF-8
(a GBK text file, a binary file) currently raise UnicodeDecodeError out of
`main_process`, so Main exits and the renderer's `send_message()` (which
waits on its queue with no timeout) hangs forever.
"""

from __future__ import annotations

import importlib.util
import queue
import sys
import threading
import types

import pytest

from test_s01_agent_loop import ROOT, fake_client, response, text_block, tool_block


def load_s05(monkeypatch, tmp_path, client):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    spec = importlib.util.spec_from_file_location("s05_non_utf8", ROOT / "s05_electron_shell" / "code.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "s05_non_utf8", module)
    spec.loader.exec_module(module)
    # main_process imports Anthropic at call time; hand it the scripted client.
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda **_kwargs: client
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return module


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("notes_gbk.txt", "中文笔记\n".encode("gbk")),
        ("logo.bin", b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x80"),
        ("notes.txt", "中文笔记\n".encode("utf-8")),
    ],
    ids=["gbk-text", "binary", "utf8-control"],
)
def test_main_answers_and_stays_alive(monkeypatch, tmp_path, name, data) -> None:
    (tmp_path / name).write_bytes(data)
    client = fake_client(
        response(tool_block("call_1", f"cat {name}"), stop_reason="tool_use"),
        response(text_block("done"), stop_reason="end_turn"),
    )
    s05 = load_s05(monkeypatch, tmp_path, client)
    to_main, to_renderer = queue.Queue(), queue.Queue()
    main = threading.Thread(target=s05.main_process, args=(to_main, to_renderer, None), daemon=True)
    main.start()

    to_main.put({"type": "agent/message", "data": "show me the file"})
    try:
        reply = to_renderer.get(timeout=5)
    except queue.Empty:
        pytest.fail(f"Main never answered agent/message (alive={main.is_alive()})")
    assert reply == {"type": "result", "data": "done"}
    # Main must still serve the renderer after the tool call.
    to_main.put({"type": "ping"})
    assert to_renderer.get(timeout=5)["type"] == "pong"
    to_main.put(None)
    main.join(timeout=5)
