"""s09: replay(max_items=0) must return an empty window, not the whole log.

Contract (s09 README §会话回放, replay docstring):
  ``replay(max_items)`` / ``replay_state(max_items)`` take the last N events and
  return reconstructed messages with ``len(messages) <= max_items`` (metadata
  events may make it strictly smaller).

Python quirk: ``events[-0:]`` is identical to ``events[0:]`` (because ``-0 == 0``),
so today's ``recent = events[-max_items:]`` returns the *entire* transcript when
``max_items=0``. That violates ``<= max_items`` and inverts the prompt-budget
knob already exercised by ``test_prompt_replay_bound_does_not_define_memory_retention``
(max_items=1). Sibling s10 already guards the same pattern:
``recent_candidates[-recent_limit:] if recent_limit > 0 else []``.

Negative ``max_items`` is likewise not a valid window (it silently drops a
prefix via ``events[k:]``); expected fail-closed with ValueError.

Real entry: public ``JSONLTranscript.replay`` / ``replay_state`` (same surface
the chapter README and existing contract tests use for the prompt window).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s09():
    name = "s09_replay_max_items_zero_test_module"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "s09_jsonl_transcript" / "code.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _five_messages(s09, tmp_path: Path):
    path = tmp_path / "session.jsonl"
    transcript = s09.JSONLTranscript(path)
    for i in range(5):
        transcript.append(
            {"type": "message", "role": "user", "content": f"m{i}"}
        )
    return transcript


def test_replay_max_items_zero_returns_empty_window(s09, tmp_path: Path) -> None:
    transcript = _five_messages(s09, tmp_path)

    assert transcript.replay(max_items=0) == []
    state = transcript.replay_state(max_items=0)
    assert state.messages == []
    # Evidence fold still sees the full log; only the prompt window is empty.
    assert state.total_events == 5
    assert state.next_sequence == 6


def test_replay_rejects_negative_max_items(s09, tmp_path: Path) -> None:
    transcript = _five_messages(s09, tmp_path)

    with pytest.raises(ValueError, match="max_items"):
        transcript.replay(max_items=-1)
    with pytest.raises(ValueError, match="max_items"):
        transcript.replay_state(max_items=-1)
