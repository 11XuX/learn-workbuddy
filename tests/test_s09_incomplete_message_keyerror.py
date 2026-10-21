"""s09: incomplete message / tool events must fail closed, not KeyError on replay.

Contract (s09 README §6 种事件类型 + §损坏处理):
  - ``message`` requires ``role`` + ``content``
  - ``function_call`` requires ``callId`` / ``name`` / ``arguments``
  - ``function_call_result`` requires ``callId`` / ``output``
  - Complete persisted records that break the evidence chain must raise
    ``TranscriptCorruptionError`` (fail closed), not crash recovery with
    a bare ``KeyError``.

Today ``append`` only rejects reserved envelope keys. A complete line missing
``role`` (or tool fields) is written, then ``replay`` / ``recover`` hits
``_event_to_message``'s ``event["role"]`` / ``event["arguments"]`` and raises
``KeyError``, so crash recovery itself crashes.

Real entry: ``JSONLTranscript.append`` (harness write path used by demo /
interactive) and ``replay`` / ``recover`` on the resulting file.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s09():
    name = "s09_incomplete_message_keyerror_test_module"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "s09_jsonl_transcript" / "code.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def test_message_missing_role_fails_closed_not_keyerror(s09, tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    transcript = s09.JSONLTranscript(path)
    # append may validate or persist; either way replay/recover must not KeyError.
    try:
        transcript.append({"type": "message", "content": "hi"})
    except s09.TranscriptValidationError:
        return
    before = path.read_bytes()
    with pytest.raises(s09.TranscriptCorruptionError):
        transcript.recover()
    assert path.read_bytes() == before


def test_function_call_missing_arguments_fails_closed_not_keyerror(
    s09, tmp_path: Path
) -> None:
    path = tmp_path / "session.jsonl"
    transcript = s09.JSONLTranscript(path)
    try:
        transcript.append(
            {"type": "function_call", "name": "read_file", "callId": "c1"}
        )
    except s09.TranscriptValidationError:
        return
    with pytest.raises(s09.TranscriptCorruptionError):
        transcript.replay()
