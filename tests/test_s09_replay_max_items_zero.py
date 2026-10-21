"""s09: replay(max_items=0) must return an empty window, not the whole log.

Published contract (replay docstring):
  ``return reconstructed messages (<= max_items)`` /
  ``Takes the last max_items events``.

Python quirk: ``events[-0:]`` equals ``events[0:]`` (``-0 == 0``), so today's
``recent = events[-max_items:]`` returns the *entire* transcript when
``max_items=0``. That violates ``<= max_items`` and inverts the prompt-budget
knob already exercised by ``test_prompt_replay_bound_does_not_define_memory_retention``
(``max_items=1``). Sibling s10 guards the same pattern and its suite already
calls ``get_context_for_agent(recent_limit=0)`` expecting no recent block:
``recent_candidates[-recent_limit:] if recent_limit > 0 else []``.

Scope (narrowed after Critic round-1): only the ``max_items=0`` empty-window
case. Negative ``max_items`` is not a separate ValueError acceptance here —
the s10-aligned one-liner also yields ``[]`` for ``< 0`` as a side effect
(instead of today's silent ``events[1:]`` prefix drop), but that is not a
new fail-closed surface claimed by this card.

Real entry: public ``JSONLTranscript.replay`` / ``replay_state``.
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


def test_replay_max_items_zero_returns_empty_window(s09, tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    transcript = s09.JSONLTranscript(path)
    for i in range(5):
        transcript.append(
            {"type": "message", "role": "user", "content": f"m{i}"}
        )

    assert transcript.replay(max_items=0) == []
    state = transcript.replay_state(max_items=0)
    assert state.messages == []
    # Evidence fold still sees the full log; only the prompt window is empty.
    assert state.total_events == 5
    assert state.next_sequence == 6


def test_replay_window_changes_preserve_full_evidence(s09, tmp_path: Path) -> None:
    """Adversarial: alternate zero/positive windows; evidence fold stays full.

    Guards acceptance: max_items=0 empties only the prompt window; positive
    windows keep last-N message reconstruction; total_events/next_sequence
    (and title/reasoning/snapshots) still fold the whole log. Real entry:
    public replay / replay_state max_items.
    """
    transcript = s09.JSONLTranscript(tmp_path / "window_changes.jsonl")
    events = [
        {"type": "ai-title", "title": "session title"},
        {"type": "reasoning", "content": "inspect evidence"},
        {"type": "file-history-snapshot", "path": "main.py", "hash": "abc"},
        {"type": "message", "role": "user", "content": "first"},
        {"type": "message", "role": "assistant", "content": "last"},
    ]
    for event in events:
        transcript.append(event)

    first = {"role": "user", "content": "first"}
    last = {"role": "assistant", "content": "last"}

    for limit, expected in [
        (1, [last]),
        (0, []),
        (2, [first, last]),
        (10, [first, last]),
        (0, []),
        (1, [last]),
    ]:
        messages = transcript.replay(max_items=limit)
        state = transcript.replay_state(max_items=limit)

        assert messages == expected
        assert state.messages == expected
        assert state.total_events == 5
        assert state.next_sequence == 6
        assert state.title == "session title"
        assert state.reasoning_count == 1
        assert state.file_snapshots == [{"path": "main.py", "hash": "abc"}]

    transcript.append(
        {"type": "message", "role": "user", "content": "after zero window"}
    )
    expected = [{"role": "user", "content": "after zero window"}]
    assert transcript.replay(max_items=1) == expected
    state = transcript.replay_state(max_items=1)
    assert state.messages == expected
    assert state.total_events == 6
    assert state.next_sequence == 7
