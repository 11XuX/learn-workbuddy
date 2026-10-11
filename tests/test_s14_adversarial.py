"""S14 adversarial tests: batch validation, audit failure, exact boundaries."""

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
WEEK = timedelta(days=7)


@pytest.fixture(scope="module")
def s14():
    saved_path = sys.path[:]
    saved_environ = os.environ.copy()
    saved_anthropic = sys.modules.pop("anthropic", None)
    name = "s14_adversarial_module"
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
        os.environ.update(saved_environ)


def item(s14, name, at):
    return s14.PendingItem(
        name, "confirmed work", f"transcript:demo:{name}",
        at, status="done",
    )


@pytest.mark.parametrize("status", ["invalid", "open"])
@pytest.mark.parametrize("bad_first", [False, True])
def test_malformed_batch_must_fail_before_any_archive(s14, status, bad_first):
    due = item(s14, "1", "2026-10-01T10:00:00Z")
    # Public state construction accepts this object; no frozen-state bypass.
    bad = SimpleNamespace(
        item_id="bad",
        description="malformed work",
        source_pointer="transcript:demo:2",
        last_confirmed_at="not-a-timestamp",
        status=status,
        reason=None,
    )
    items = (bad, due) if bad_first else (due, bad)
    state = s14.DurableContextState(pending_items=items)
    original = state.pending_items

    with pytest.raises(ValueError):
        s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)

    assert state.pending_items is original
    assert all(a is b for a, b in zip(state.pending_items, items))


def test_microsecond_boundary_order_identity_and_repeat(s14):
    due = item(s14, "1", "2026-10-02T18:00:00+08:00")
    young = item(s14, "2", "2026-10-02T18:00:00.000001+08:00")
    older = item(s14, "3", "2026-10-02T09:59:59.999999Z")
    fact = s14.DurableFact(
        "fact", "keep evidence", "transcript:demo:4",
        "2026-10-01T00:00:00Z",
    )
    state = s14.DurableContextState(
        facts=(fact,), pending_items=(due, young, older)
    )

    after, records = s14.archive_done_pending_items(
        state, now=NOW, older_than=WEEK
    )

    assert tuple(r.item for r in records) == (due, older)
    assert records[0].item is due and records[1].item is older
    assert all(r.archived_at == NOW.isoformat() for r in records)
    assert after.pending_items == (young,)
    assert after.pending_items[0] is young
    assert after.facts[0] is fact
    assert state.pending_items == (due, young, older)

    repeated, again = s14.archive_done_pending_items(
        after, now=NOW, older_than=WEEK
    )
    assert repeated is after
    assert again == ()


def test_second_audit_failure_leaves_entire_input_intact(s14, monkeypatch):
    first = item(s14, "1", "2026-10-01T10:00:00Z")
    second = item(s14, "2", "2026-10-01T11:00:00Z")
    state = s14.DurableContextState(pending_items=(first, second))
    original = state.pending_items
    constructor = s14.ArchivedPendingItem
    attempted = []

    def fail_second(pending, archived_at):
        attempted.append(pending)
        if pending is second:
            raise RuntimeError("audit construction failed")
        return constructor(pending, archived_at)

    monkeypatch.setattr(s14, "ArchivedPendingItem", fail_second)
    with pytest.raises(RuntimeError, match="audit construction failed"):
        s14.archive_done_pending_items(state, now=NOW, older_than=WEEK)

    assert attempted == [first, second]
    assert state.pending_items is original
    assert state.pending_items[0] is first
    assert state.pending_items[1] is second

    monkeypatch.setattr(s14, "ArchivedPendingItem", constructor)
    after, records = s14.archive_done_pending_items(
        state, now=NOW, older_than=WEEK
    )
    assert after.pending_items == ()
    assert tuple(r.item for r in records) == (first, second)
