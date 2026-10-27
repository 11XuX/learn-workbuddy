"""s01 bash: non-UTF-8 command output must come back as a tool_result.

`run_bash` already turns timeouts and OS errors into "Error: ..." text so the
loop can feed them back to the model. Output bytes that are not valid UTF-8
(a GBK text file, a binary file) currently raise UnicodeDecodeError out of
`agent_loop`, so the REPL exits and the assistant tool_use has no tool_result.
"""

from __future__ import annotations

import pytest

from test_s01_agent_loop import fake_client, load_s01, response, text_block, tool_block


@pytest.mark.parametrize(
    "command",
    [
        "cat notes_gbk.txt",
        "cat logo.bin",
    ],
    ids=["gbk-text", "binary"],
)
def test_non_utf8_bash_output_is_fed_back(monkeypatch, tmp_path, command) -> None:
    (tmp_path / "notes_gbk.txt").write_bytes("中文笔记\n".encode("gbk"))
    (tmp_path / "logo.bin").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x80")
    monkeypatch.chdir(tmp_path)
    s01 = load_s01(monkeypatch)
    s01.client = fake_client(
        response(tool_block("call_1", command), stop_reason="tool_use"),
        response(text_block("done"), stop_reason="end_turn"),
    )
    messages = [{"role": "user", "content": "show me the file"}]

    result = s01.agent_loop(messages, max_turns=3)

    assert result.stop_reason is s01.LoopStopReason.FINAL_ANSWER
    assert result.tool_calls == 1
    tool_result = messages[2]["content"][0]
    assert tool_result["tool_use_id"] == "call_1"
    assert isinstance(tool_result["content"], str) and tool_result["content"]


def test_utf8_bash_output_still_works(monkeypatch, tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("中文笔记\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    s01 = load_s01(monkeypatch)

    assert s01.run_bash("cat notes.txt") == "中文笔记"
