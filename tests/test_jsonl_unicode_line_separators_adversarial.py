"""Pin JSONL framing separately from JSON string escaping and record counts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPLITTER_CHAPTERS = (
    "s09_jsonl_transcript",
    "s10_workspace_memory",
    "s12_cloud_memory",
    "s13_output_externalization",
)


@pytest.fixture
def chapter(request, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    relative = f"{request.param}/code.py"
    name = f"adversarial_jsonl_{request.param}"
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-key")
    monkeypatch.setattr(sys, "argv", [relative])
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("chapter", SPLITTER_CHAPTERS, indirect=True)
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(b"", [], id="empty"),
        pytest.param(b"\r\r", ["\n", "\n"], id="only-CR"),
        pytest.param(
            b"one\r\n\rthree\nunfinished",
            ["one\n", "\n", "three\n", "unfinished"],
            id="mixed-boundaries-and-tail",
        ),
    ],
)
def test_splitter_preserves_boundary_and_tail_contract(chapter, raw, expected):
    assert chapter._split_jsonl(raw) == expected


@pytest.mark.parametrize("chapter", SPLITTER_CHAPTERS, indirect=True)
def test_splitter_keeps_raw_unicode_and_literal_escapes_distinct(chapter):
    content = (
        "raw:\u2028\u2029\x85; "
        "literal:\\u2028\\u2029\\u0085\\n\\r; "
        "controls:\n\r\t; 中文"
    )
    first = json.dumps({"content": content}, ensure_ascii=False)
    second = json.dumps({"content": "second"}, ensure_ascii=False)
    tail = '{"content": "unfinished\u2028'
    raw = (first + "\r\n" + second + "\r" + tail).encode("utf-8")
    for char in ("\u2028", "\u2029", "\x85"):
        assert char.encode("utf-8") in raw

    lines = chapter._split_jsonl(raw)
    assert lines == [first + "\n", second + "\n", tail]
    assert json.loads(lines[0]) == {"content": content}
    assert json.loads(lines[1]) == {"content": "second"}
    assert not lines[-1].endswith("\n")


@pytest.mark.parametrize("chapter", ["s24_comprehensive"], indirect=True)
def test_s24_sequence_counts_records_not_unicode_breakers(chapter):
    content = "raw:\u2028\u2029\x85; literal:\\u2028\\n\\r; controls:\n\r"
    for index in range(3):
        chapter.Transcript("adversarial-sequence").append(
            {"type": "user", "content": f"{index}:{content}"}
        )

    transcript = chapter.Transcript("adversarial-sequence")
    raw = transcript.path.read_bytes()
    assert raw.count(b"\n") == 3
    for char in ("\u2028", "\u2029", "\x85"):
        assert raw.count(char.encode("utf-8")) == 3
    # Parse physical lines independently: read() must not hide a counting bug.
    records = [json.loads(line) for line in raw.split(b"\n") if line]
    assert [record["sequence"] for record in records] == [1, 2, 3]
    assert [record["content"] for record in records] == [
        f"{index}:{content}" for index in range(3)
    ]
    assert transcript.read() == records


@pytest.mark.parametrize("chapter", ["s24_comprehensive"], indirect=True)
def test_s24_workspace_window_counts_six_records(chapter, tmp_path: Path):
    memory = chapter.Memory(tmp_path / "project")
    entries = [
        f"item-{index}:\u2028A\u2029B\x85C literal:\\u2028"
        for index in range(8)
    ]
    for entry in entries:
        memory.append_workspace(entry)

    assert memory.workspace_log.read_bytes().count(b"\n") == 8
    reopened = chapter.Memory(tmp_path / "project")
    assert reopened.get_workspace() == "\n".join(
        f"- {entry}" for entry in entries[-6:]
    )
