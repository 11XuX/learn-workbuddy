"""JSONL writers use json.dumps(ensure_ascii=False); readers use str.splitlines().

ensure_ascii=False leaves U+2028, U+2029 and U+0085 raw inside JSON strings,
and str.splitlines() breaks on all three, so one record becomes fragments.
Contract: one JSON object per "\\n"-terminated line (s09/s10/s12/s23 READMEs,
s24 adapters, mini_workbuddy AuditLog chain); a round-trip keeps one record.

Locked fix shape: keep read_text() newline handling ("\\r\\n"/"\\r" -> "\\n",
as pinned by test_s09_unterminated_line_adversarial), then split on "\\n" only.

Sites: s09 replay, s10 daily log, s12 RemoteMemoryStore, s23 audit chain,
s24 Transcript/Memory, mini_workbuddy/audit.py, s13 retention journal (writer
fields are ASCII-validated, so the repro is a hash-valid extra field). Plus an
s09 #11 partial-tail boundary and an s23 legacy-chain control.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Characters that str.splitlines() breaks on but JSONL writers do not escape
# when ensure_ascii=False.
LINE_BREAKERS = (
    pytest.param("\u2028", id="U+2028"),
    pytest.param("\u2029", id="U+2029"),
    pytest.param("\x85", id="U+0085"),
)


def _load(name: str, relative: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Import a chapter module with an isolated WORKBUDDY_HOME / MODEL_ID."""
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-key")
    # Avoid chapter_demo treating pytest args as --demo.
    monkeypatch.setattr(sys, "argv", [relative])
    stub = ROOT / "tests" / "stubs"
    if str(stub) not in sys.path:
        sys.path.insert(0, str(stub))
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def s09(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    name = "hunt_u2028_s09"
    module = _load(name, "s09_jsonl_transcript/code.py", monkeypatch, tmp_path)
    yield module
    sys.modules.pop(name, None)


@pytest.fixture
def s10(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    name = "hunt_u2028_s10"
    module = _load(name, "s10_workspace_memory/code.py", monkeypatch, tmp_path)
    yield module
    sys.modules.pop(name, None)


@pytest.fixture
def s12(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    name = "hunt_u2028_s12"
    module = _load(name, "s12_cloud_memory/code.py", monkeypatch, tmp_path)
    yield module
    sys.modules.pop(name, None)


@pytest.fixture
def s23(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    name = "hunt_u2028_s23"
    module = _load(name, "s23_audit_sandbox/code.py", monkeypatch, tmp_path)
    yield module
    sys.modules.pop(name, None)


@pytest.fixture
def s24(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    name = "hunt_u2028_s24"
    module = _load(name, "s24_comprehensive/code.py", monkeypatch, tmp_path)
    yield module
    sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# 1. s09
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_s09_transcript_roundtrips_line_separator_in_content(
    s09, tmp_path: Path, breaker: str
) -> None:
    """Bug repro: append succeeds; replay raises invalid complete JSON record."""
    path = tmp_path / "session.jsonl"
    content = f"hello{breaker}world"
    s09.JSONLTranscript(path).append(
        {"type": "message", "role": "user", "content": content}
    )
    state = s09.JSONLTranscript(path).replay_state()
    assert state.total_events == 1
    assert state.messages == [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------
# 2. s10
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_s10_daily_log_roundtrips_line_separator_in_source(
    s10, tmp_path: Path, breaker: str
) -> None:
    """Bug repro: source is not whitespace-normalized; read_daily_facts breaks."""
    mem = s10.WorkspaceMemory(tmp_path / "project")
    source = f"agent{breaker}tool"
    mem.append_daily_log("normal content", source=source)
    facts = mem.read_daily_facts()
    assert len(facts) == 1
    assert facts[0].source == source
    assert facts[0].content == "normal content"


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_s10_daily_log_roundtrips_line_separator_in_evidence(
    s10, tmp_path: Path, breaker: str
) -> None:
    """Bug repro: evidence values are also stored raw."""
    mem = s10.WorkspaceMemory(tmp_path / "project")
    note = f"cite{breaker}ref"
    mem.append_daily_log("normal content", evidence={"note": note})
    facts = mem.read_daily_facts()
    assert len(facts) == 1
    assert facts[0].evidence["note"] == note


# ---------------------------------------------------------------------------
# 3. s12
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_s12_store_roundtrips_line_separator_in_memory_id(
    s12, tmp_path: Path, breaker: str
) -> None:
    """Bug repro: caller-chosen memory_id is not cleaned; read_all breaks."""
    store = s12.RemoteMemoryStore(tmp_path / "records.jsonl", user_id="u1")
    memory_id = f"mem{breaker}id"
    source = s12.MemorySource(
        source_id="src-1",
        source_type="transcript",
        title="t",
        captured_at=datetime.now(timezone.utc).isoformat(),
    )
    store.append(
        kind=s12.MemoryKind.PROFILE,
        content="hello",
        summary="sum",
        source=source,
        memory_id=memory_id,
    )
    records = store.read_all()
    assert len(records) == 1
    assert records[0].memory_id == memory_id


# ---------------------------------------------------------------------------
# 4. s23
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_s23_audit_chain_verify_survives_line_separator_in_params(
    s23, breaker: str
) -> None:
    """Bug repro: head count uses splitlines; verify_chain false-positives."""
    s23.append_audit_entry("run", {"cmd": f"echo{breaker}hi"}, "ok")
    ok, count = s23.verify_chain()
    assert ok is True
    assert count == 1


# ---------------------------------------------------------------------------
# 5. s24
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_s24_transcript_roundtrips_line_separator_in_content(
    s24, breaker: str
) -> None:
    """Bug repro: Transcript.read raises invalid transcript JSON."""
    transcript = s24.Transcript("sess-u2028")
    content = f"hello{breaker}world"
    transcript.append({"type": "user", "content": content})
    records = s24.Transcript("sess-u2028").read()
    assert len(records) == 1
    assert records[0]["content"] == content


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_s24_workspace_memory_keeps_line_separator_fact(
    s24, tmp_path: Path, breaker: str
) -> None:
    """Bug repro: get_workspace silently drops the split fact."""
    mem = s24.Memory(tmp_path / "proj")
    entry = f"note{breaker}keep"
    mem.append_workspace(entry)
    text = s24.Memory(tmp_path / "proj").get_workspace()
    assert entry in text


# ---------------------------------------------------------------------------
# 6. mini_workbuddy/audit.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_mini_audit_verify_and_append_survive_line_separator(
    tmp_path: Path, breaker: str
) -> None:
    """Bug repro: verify() False; next append raises audit chain is unreadable."""
    from mini_workbuddy.audit import AuditLog
    from mini_workbuddy.config import HarnessConfig

    audit = AuditLog(HarnessConfig(root_dir=tmp_path / "home"))
    audit.append("act", {"payload": f"a{breaker}b"})
    assert audit.verify() is True
    # A healthy chain must still accept a further append.
    audit.append("act2", {"payload": "ok"})
    assert audit.verify() is True


# ---------------------------------------------------------------------------
# 7. s13 retention journal
# ---------------------------------------------------------------------------


@pytest.fixture
def s13(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    name = "hunt_u2028_s13"
    module = _load(name, "s13_output_externalization/code.py", monkeypatch, tmp_path)
    yield module
    sys.modules.pop(name, None)


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_s13_retention_journal_reads_hash_valid_record_with_line_separator(
    s13, tmp_path: Path, breaker: str
) -> None:
    """Bug repro: an extra field covered by event_sha256 is accepted by the
    reader, but decode().splitlines() rejects the record as invalid JSON."""
    session_dir = tmp_path / "sess-u2028"
    artifact = s13.ToolResultExternalizer(session_dir).externalize(
        "durable evidence", "search", summary="Evidence for the journal."
    ).artifact
    claim = s13.ArtifactRetentionClaim.from_memory_reference(artifact.for_memory())
    journal = s13.ArtifactRetentionJournal(session_dir)
    journal.prepare(claim, transaction_id="tx-u2028")
    raw = journal.path.read_bytes()
    assert raw.count(b"\n") == 1

    payload = json.loads(raw)
    payload.pop("event_sha256")
    payload["operator_note"] = f"kept{breaker}verbatim"
    payload["event_sha256"] = s13._canonical_sha256(payload)
    journal.path.write_bytes(
        (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode("utf-8")
    )

    recovery = s13.ArtifactRetentionJournal(session_dir).recover()
    assert recovery.pending_transaction_ids == ("tx-u2028",)
    assert [c.source_id for c in recovery.claims] == [claim.source_id]


# ---------------------------------------------------------------------------
# 8. s09 boundary with #11: separator record + partial tail still refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_s09_separator_record_then_partial_tail_still_refuses_append(
    s09, tmp_path: Path, breaker: str
) -> None:
    """After switching to b"\\n" splitting, the partial-tail rule must hold."""
    path = tmp_path / "session.jsonl"
    content = f"hello{breaker}world"
    s09.JSONLTranscript(path).append(
        {"type": "message", "role": "user", "content": content}
    )
    with path.open("ab") as handle:
        handle.write(b'{"type": "message", "role": "assis')
    before = path.read_bytes()

    state = s09.JSONLTranscript(path).replay_state()
    assert state.total_events == 1
    assert state.messages == [{"role": "user", "content": content}]
    assert state.ignored_partial_tail is True

    with pytest.raises(s09.TranscriptCorruptionError, match="partial tail"):
        s09.JSONLTranscript(path).append(
            {"type": "message", "role": "assistant", "content": "new"}
        )
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# 9. s23 control: legacy chains keep the same head count and verify
# ---------------------------------------------------------------------------


def test_s23_legacy_chain_head_count_unchanged_and_verifies(s23) -> None:
    """Control (passes on main and after the fix): legacy heads stay valid."""
    payloads = [{"cmd": "ls"}, {"cmd": "echo 你好"}, {"cmd": "tab\there"}]
    for params in payloads:
        s23.append_audit_entry("run", params, "ok")
    raw = s23.audit_log_path().read_bytes()
    assert raw.count(b"\n") == len(payloads)
    head = json.loads(s23.audit_head_path().read_text(encoding="utf-8"))
    assert head["count"] == len(payloads)
    assert s23.verify_chain() == (True, len(payloads))
