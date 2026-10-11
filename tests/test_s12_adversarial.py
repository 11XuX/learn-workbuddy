"""Adversarial coverage for s12 append-only retractions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import threading

import pytest
from test_remote_memory import s12  # noqa: F401


def _source(s12):
    return s12.MemorySource(
        source_id="adversarial",
        source_type="transcript",
        title="Adversarial evidence",
        captured_at="2026-08-01T12:00:00+00:00",
    )


def _add(s12, store, memory_id):
    return store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id=memory_id,
        content="python deployment evidence",
        summary="python deployment evidence",
        source=_source(s12),
        stored_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
    )


def _rid(target):
    return "retraction-" + hashlib.sha256(target.encode("utf-8")).hexdigest()[:24]


@pytest.fixture
def store(s12, tmp_path):
    return s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="u")


def test_concurrent_retractions_return_one_durable_event(s12, store):
    _add(s12, store, "target")
    barrier = threading.Barrier(4)

    def retract(index):
        writer = s12.RemoteMemoryStore(store.path, user_id="u")
        barrier.wait(timeout=10)
        return writer.retract(
            "target", reason=f"request {index}", source=_source(s12)
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(retract, range(4)))

    assert all(record == results[0] for record in results)
    assert results[0].memory_id == _rid("target")
    assert len(store.read_all()) == 2
    assert store.active_records() == []
    before = store.path.read_bytes()
    assert s12.RecallEngine(store).recall("python deployment").hits == ()
    assert store.path.read_bytes() == before


def test_legacy_integer_id_retraction_does_not_corrupt_store(s12, store):
    # Allow a future fix to reject integer ids; do not lock in acceptance.
    try:
        _add(s12, store, 123)
    except s12.RemoteMemoryValidationError:
        assert not store.path.exists() or store.path.read_bytes() == b""
        return

    assert [r.memory_id for r in store.active_records()] == ["123"]
    before = store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryValidationError):
        store.retract(123, reason="forget", source=_source(s12))
    assert store.path.read_bytes() == before

    event = store.retract("123", reason="forget", source=_source(s12))
    assert event.memory_id == _rid("123")
    reopened = s12.RemoteMemoryStore(store.path, user_id="u")
    assert [r.memory_id for r in reopened.read_all()] == ["123", event.memory_id]
    assert reopened.active_records() == []
    before = store.path.read_bytes()
    assert reopened.retract(
        "123", reason="retry", source=_source(s12)
    ) == event
    assert store.path.read_bytes() == before
    _add(s12, reopened, "survivor")
    assert [r.memory_id for r in reopened.active_records()] == ["survivor"]


@pytest.mark.parametrize("separator", ["\x85", "\u2028", "\u2029"])
def test_separator_ids_stay_distinct_and_round_trip(s12, store, separator):
    target = f"mem{separator}id"
    _add(s12, store, target)
    _add(s12, store, "memid")
    event = store.retract(target, reason="forget", source=_source(s12))
    assert event.memory_id == _rid(target)

    reopened = s12.RemoteMemoryStore(store.path, user_id="u")
    audit = reopened.read_all()
    assert [r.memory_id for r in audit] == [target, "memid", _rid(target)]
    assert audit[-1].retracts == target
    assert [r.memory_id for r in reopened.active_records()] == ["memid"]
    assert [
        hit.memory_id
        for hit in s12.RecallEngine(reopened).recall("python deployment").hits
    ] == ["memid"]

    before = store.path.read_bytes()
    for bad in (separator + "memid", "memid" + separator):
        with pytest.raises(s12.RemoteMemoryValidationError):
            reopened.retract(bad, reason="forget", source=_source(s12))
        assert store.path.read_bytes() == before


@pytest.mark.parametrize("field", ["memory_id", "retracts"])
@pytest.mark.parametrize(
    "bad",
    [None, True, 123, [], {}, "", " target", "target ",
     "\x85target", "target\u2029", "x" * 201],
)
def test_raw_retraction_types_and_boundaries_are_corruption(
    s12, store, field, bad
):
    _add(s12, store, "target")
    store.retract("target", reason="forget", source=_source(s12))
    rows = [json.loads(line) for line in store.path.read_bytes().split(b"\n") if line]
    rows[-1][field] = bad
    store.path.write_bytes(
        b"".join(
            json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n"
            for row in rows
        )
    )
    before = store.path.read_bytes()
    for read in (
        store.active_records,
        store.latest_profile,
        lambda: s12.RecallEngine(store).recall("python"),
    ):
        with pytest.raises(s12.RemoteMemoryCorruptionError):
            read()
        assert store.path.read_bytes() == before


def test_duplicate_retraction_event_is_corruption(s12, store):
    _add(s12, store, "target")
    store.retract("target", reason="forget", source=_source(s12))
    event_line = store.path.read_bytes().split(b"\n")[-2]
    with store.path.open("ab") as stream:
        stream.write(event_line + b"\n")
    before = store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryCorruptionError):
        store.active_records()
    assert store.path.read_bytes() == before


def test_correctly_derived_event_cannot_target_another_retraction(s12, store):
    _add(s12, store, "target")
    first = store.retract("target", reason="forget", source=_source(s12))
    forged = first.to_dict()
    forged.update(memory_id=_rid(first.memory_id), retracts=first.memory_id)
    with store.path.open("ab") as stream:
        stream.write(json.dumps(forged).encode("utf-8") + b"\n")
    before = store.path.read_bytes()
    with pytest.raises(s12.RemoteMemoryCorruptionError):
        store.active_records()
    assert store.path.read_bytes() == before


def test_plain_record_keeps_legacy_serialization_bytes(s12, store):
    record = _add(s12, store, "legacy")
    legacy = {
        "memory_id": record.memory_id,
        "user_scope": record.user_scope,
        "kind": record.kind.value,
        "content": record.content,
        "summary": record.summary,
        "source": {
            "source_id": record.source.source_id,
            "source_type": record.source.source_type,
            "title": record.source.title,
            "captured_at": record.source.captured_at,
        },
        "stored_at": record.stored_at,
        "schema_version": record.schema_version,
    }
    expected = (
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    assert store.path.read_bytes() == expected
    reopened = s12.RemoteMemoryStore(store.path, user_id="u")
    assert reopened.read_all()[0].retracts is None
    assert reopened.active_records() == reopened.read_all()
