"""Round 2: validate no-op batches and recover from state construction failure."""

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
WEEK = timedelta(days=7)


@pytest.fixture(scope="module")
def s14():
    saved_path = sys.path[:]
    saved_env = os.environ.copy()
    saved_anthropic = sys.modules.pop("anthropic", None)
    name = "s14_adversarial_r2_module"
    sys.path.insert(0, str(ROOT / "tests" / "stubs"))
    os.environ["MODEL_ID"] = "offline-test-model"
    try:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "s14_context_compact" / "code.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = saved_path
        sys.modules.pop(name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        os.environ.clear()
        os.environ.update(saved_env)


@pytest.mark.parametrize("status", ["open", "blocked", "done"])
@pytest.mark.parametrize("field,value", [
    ("status", "invalid"),
    ("last_confirmed_at", "bad"),
    ("last_confirmed_at", "2026-10-01T00:00:00"),
])
def test_no_due_batch_still_validates_every_entry(
    s14, monkeypatch, status, field, value
):
    bad = s14.PendingItem(
        "bad", "work", "transcript:demo:1", NOW.isoformat(),
        status=status, reason="waiting" if status == "blocked" else None,
    )
    # Exercise defensive validation of an exact PendingItem, not a duck type.
    object.__setattr__(bad, field, value)
    state = s14.DurableContextState(pending_items=(bad,))
    original = state.pending_items
    attempts = []

    def unexpected_archive(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("validation must precede audit construction")

    monkeypatch.setattr(s14, "ArchivedPendingItem", unexpected_archive)
    with pytest.raises(ValueError):
        s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)

    assert attempts == []
    assert state.pending_items is original
    assert state.pending_items[0] is bad
    assert getattr(bad, field) == value


def test_state_construction_failure_preserves_input_and_allows_retry(
    s14, monkeypatch
):
    due = s14.PendingItem(
        "due", "work", "transcript:demo:1",
        "2026-10-01T00:00:00Z", status="done",
    )
    state = s14.DurableContextState(pending_items=(due,))
    original = state.pending_items
    audited = []
    replacements = []
    constructor = s14.ArchivedPendingItem

    def record_audit(item, archived_at):
        record = constructor(item, archived_at)
        audited.append(record)
        return record

    def fail_replace(obj, **changes):
        replacements.append((obj, changes))
        raise RuntimeError("state construction failed")

    with monkeypatch.context() as patch:
        patch.setattr(s14, "ArchivedPendingItem", record_audit)
        patch.setattr(s14, "replace", fail_replace)
        with pytest.raises(RuntimeError, match="state construction failed"):
            s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)

    assert len(audited) == 1
    assert audited[0].item is due
    assert audited[0].archived_at == NOW.isoformat()
    assert len(replacements) == 1
    assert replacements[0][0] is state
    assert replacements[0][1] == {"pending_items": ()}
    assert state.pending_items is original
    assert state.pending_items[0] is due

    after, records = s14.archive_done_pending_items(
        state, now=NOW, older_than=WEEK
    )
    assert after.pending_items == ()
    assert len(records) == 1
    assert records[0].item is due
    assert state.pending_items is original
