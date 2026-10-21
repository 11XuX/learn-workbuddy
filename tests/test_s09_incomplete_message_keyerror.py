"""s09: incomplete persisted message/tool events must fail closed, not KeyError.

Contract anchors (s09 README §损坏处理 / §为什么选 JSONL + TranscriptCorruptionError):
  - Complete records that break the evidence chain must raise
    ``TranscriptCorruptionError`` (fail closed), not escape as bare ``KeyError``.
  - README event examples: ``message`` carries ``role``+``content``;
    ``function_call`` carries ``name``/``arguments``/``callId``.
  - ``_event_to_message`` folds those fields with direct subscript; missing
    keys today crash ``replay`` / ``recover``.

Scope (narrowed after Critic round-1): this card targets the *fold/recover*
path for already-persisted complete JSONL lines. ``append`` type-field
validation is optional defense-in-depth, not required acceptance.

Real entry: a complete JSONL line already on disk (buggy writer / hand edit /
prior ``append``), then ``recover`` / ``replay``.
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


def _persist(path: Path, payload: dict) -> None:
    """Write one complete envelope line (simulates already-on-disk evidence)."""
    session_id = path.stem
    envelope = {
        "schema_version": 1,
        "sequence": 1,
        "session_id": session_id,
        "event_id": f"transcript:{session_id}:1",
        "recorded_at": "2026-01-01T00:00:00+00:00",
        **payload,
    }
    path.write_text(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_persisted_message_missing_role_recover_not_keyerror(s09, tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    _persist(path, {"type": "message", "content": "hi"})  # no role
    before = path.read_bytes()
    with pytest.raises(s09.TranscriptCorruptionError):
        s09.JSONLTranscript(path).recover()
    assert path.read_bytes() == before


def test_persisted_function_call_missing_arguments_replay_not_keyerror(
    s09, tmp_path: Path
) -> None:
    path = tmp_path / "session.jsonl"
    _persist(path, {"type": "function_call", "name": "read_file", "callId": "c1"})
    with pytest.raises(s09.TranscriptCorruptionError):
        s09.JSONLTranscript(path).replay()


@pytest.mark.parametrize(
    ("entrypoint", "payload"),
    [
        ("replay", {"type": "message", "content": "missing role"}),
        (
            "recover",
            {
                "type": "function_call",
                "name": "read_file",
                "callId": "c1",
                # 故意缺 arguments
            },
        ),
    ],
)
def test_persisted_missing_fields_cross_entrypoints_fail_closed(
    s09, tmp_path: Path, entrypoint: str, payload: dict
) -> None:
    """Verifier adversarial: cross entrypoints + byte-stable fail-closed.

    Hunter covers message→recover and function_call→replay. This flips the
    pairs so a fix that only patches one path, or silently .get/None-defaults,
    still fails. Disk bytes must stay unchanged (no rewrite-on-error).
    """
    path = tmp_path / "cross_entrypoint.jsonl"
    _persist(path, payload)
    before = path.read_bytes()

    with pytest.raises(s09.TranscriptCorruptionError):
        getattr(s09.JSONLTranscript(path), entrypoint)()

    assert path.read_bytes() == before
