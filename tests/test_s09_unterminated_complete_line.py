"""s09: append must refuse when the last line is complete JSON but has no trailing newline.

README §损坏处理 (s09_jsonl_transcript/README.md ~252–256) and append() comments
(code.py ~183–184) say: if the log does not end on a clean durable boundary,
append must refuse so new JSON is not spliced onto residual bytes. Callers keep
the old file and open a new transcript; no auto truncate / pad newline.

Today `_read_all_events` only sets `_ignored_partial_tail` on JSONDecodeError
plus a missing newline (code.py ~223–226). A last line that `json.loads` accepts
but that lacks `\\n` is treated as healthy, so O_APPEND concatenates and turns a
still-replayable prefix into a complete corrupt line.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s09():
    name = "s09_unterminated_complete_line_test_module"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "s09_jsonl_transcript" / "code.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def test_append_refuses_complete_json_last_line_without_trailing_newline(
    s09, tmp_path: Path
) -> None:
    """Main repro: truncated trailing newline after a complete JSON record."""
    path = tmp_path / "session.jsonl"
    transcript = s09.JSONLTranscript(path)
    transcript.append({"type": "message", "role": "user", "content": "saved"})

    # Durable boundary broken: last written record lost its trailing newline
    # (short write of encoded[:-1], crash between JSON and \\n, or truncate).
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    path.write_bytes(raw[:-1])
    before = path.read_bytes()
    assert before and not before.endswith(b"\n")
    assert isinstance(json.loads(before.decode("utf-8")), dict)

    replacement = s09.JSONLTranscript(path)
    state = replacement.replay_state()
    assert state.total_events == 1
    assert state.messages == [{"role": "user", "content": "saved"}]
    # Current main leaves ignored_partial_tail False here; the contract under test
    # is that append must still refuse this unclean tail.

    with pytest.raises(
        s09.TranscriptCorruptionError, match="partial tail|newline|unterminated"
    ):
        replacement.append({"type": "message", "role": "assistant", "content": "new"})

    assert path.read_bytes() == before
    recovered = s09.JSONLTranscript(path).replay_state()
    assert recovered.total_events == 1
    assert recovered.messages == [{"role": "user", "content": "saved"}]


def test_short_write_missing_newline_then_retry_append_must_refuse(
    s09, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """append itself can create this state via short write; retry must refuse."""
    path = tmp_path / "session.jsonl"
    transcript = s09.JSONLTranscript(path)
    real_write = s09.os.write
    calls = {"n": 0}

    def write_all_but_newline(fd: int, data: bytes) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            written = real_write(fd, data[:-1])
            assert written == len(data) - 1
            return written
        return real_write(fd, data)

    with monkeypatch.context() as patch:
        patch.setattr(s09.os, "write", write_all_but_newline)
        with pytest.raises(OSError, match="short transcript write"):
            transcript.append({"type": "message", "role": "user", "content": "saved"})

    before = path.read_bytes()
    assert before and not before.endswith(b"\n")
    assert isinstance(json.loads(before.decode("utf-8")), dict)

    replacement = s09.JSONLTranscript(path)
    with pytest.raises(
        s09.TranscriptCorruptionError, match="partial tail|newline|unterminated"
    ):
        replacement.append({"type": "message", "role": "assistant", "content": "retry"})

    assert path.read_bytes() == before
    recovered = s09.JSONLTranscript(path).replay_state()
    assert recovered.total_events == 1
    assert recovered.messages == [{"role": "user", "content": "saved"}]


def test_append_still_works_when_file_ends_with_newline(s09, tmp_path: Path) -> None:
    """Control: a clean durable boundary (trailing newline) still appends."""
    path = tmp_path / "session.jsonl"
    transcript = s09.JSONLTranscript(path)
    transcript.append({"type": "message", "role": "user", "content": "a"})
    assert path.read_bytes().endswith(b"\n")

    transcript.append({"type": "message", "role": "assistant", "content": "b"})
    state = transcript.replay_state()
    assert state.total_events == 2
    assert state.messages == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    assert state.ignored_partial_tail is False
