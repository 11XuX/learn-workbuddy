"""Adversarial checks for the example's retraction contract gate."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "examples" / "memory_resilience_eval" / "code.py"


def load_module():
    name = "examples_retract_gate_adversarial_module"
    spec = importlib.util.spec_from_file_location(name, CODE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_gate(module, chapter, tmp_path):
    cases = tuple(
        case
        for case in module.DEFAULT_CASES
        if case.case_id == "retract_survives_restart"
    )
    assert len(cases) == 1
    report = module.evaluate_cases(chapter, tmp_path, cases=cases)
    assert report.summary == {"total": 1, "passed": 0, "failed": 1}
    result = report.cases[0]
    assert result.observed == "retraction_leaked"
    return report, result.evidence


def test_pre_retraction_query_must_find_both_records(tmp_path, monkeypatch):
    module = load_module()
    chapter = module.load_memory_chapter()
    original = chapter.RecallEngine.recall
    calls = 0

    def hide_target_before_retraction(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(self, *args, **kwargs)
        if calls == 1:
            return replace(
                result,
                hits=tuple(
                    hit for hit in result.hits
                    if hit.memory_id != "mem-deploy-old"
                ),
            )
        return result

    monkeypatch.setattr(
        chapter.RecallEngine, "recall", hide_target_before_retraction
    )
    _, evidence = run_gate(module, chapter, tmp_path)
    assert "mem-deploy-old" not in evidence["pre_retract_hit_ids"]
    assert evidence["hit_ids"] == ["mem-deploy-new"]
    assert evidence["audit_kept"] is True


@pytest.mark.parametrize("damage", ["original_id", "event_id", "retracts"])
def test_audit_checks_identity_and_target_not_just_count(
    tmp_path, monkeypatch, damage
):
    module = load_module()
    chapter = module.load_memory_chapter()
    original_active = chapter.RemoteMemoryStore.active_records
    original_read = chapter.RemoteMemoryStore.read_all
    active_calls = 0

    def count_active_reads(self):
        nonlocal active_calls
        records = original_active(self)
        active_calls += 1
        return records

    def damage_final_audit(self):
        records = original_read(self)
        # Pre-retraction recall, rebuilt recall, explicit active view.
        # Alter only the subsequent audit response, leaving retrieval intact.
        if active_calls < 3:
            return records
        altered = []
        for record in records:
            if damage == "original_id" and record.memory_id == "mem-deploy-old":
                record = replace(record, memory_id="wrong-original")
            elif record.kind is chapter.MemoryKind.RETRACTION:
                if damage == "event_id":
                    record = replace(record, memory_id="wrong-event")
                elif damage == "retracts":
                    record = replace(record, retracts="mem-deploy-new")
            altered.append(record)
        return altered

    monkeypatch.setattr(
        chapter.RemoteMemoryStore, "active_records", count_active_reads
    )
    monkeypatch.setattr(
        chapter.RemoteMemoryStore, "read_all", damage_final_audit
    )
    _, evidence = run_gate(module, chapter, tmp_path)
    assert evidence["durable_records"] == 3
    assert evidence["hit_ids"] == ["mem-deploy-new"]
    assert evidence["active_ids"] == ["mem-deploy-new"]
    assert evidence["audit_kept"] is False
    assert evidence["store_unchanged"] is True


def test_repeat_retraction_must_return_same_id(tmp_path, monkeypatch):
    module = load_module()
    chapter = module.load_memory_chapter()
    original = chapter.RemoteMemoryStore.retract
    calls = 0

    def different_second_id(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        record = original(self, *args, **kwargs)
        return (
            replace(record, memory_id="wrong-repeat-id")
            if calls == 2 else record
        )

    monkeypatch.setattr(
        chapter.RemoteMemoryStore, "retract", different_second_id
    )
    _, evidence = run_gate(module, chapter, tmp_path)
    assert evidence["retraction_ids"][0] != evidence["retraction_ids"][1]
    assert evidence["audit_kept"] is True
    assert evidence["durable_records"] == 3


def test_rebuilt_recall_cannot_change_durable_bytes(tmp_path, monkeypatch):
    module = load_module()
    chapter = module.load_memory_chapter()
    original = chapter.RecallEngine.recall
    calls = 0

    def modifying_recall(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(self, *args, **kwargs)
        if calls == 2:
            # Valid JSONL whitespace changes bytes without changing records.
            with self.store.path.open("ab") as stream:
                stream.write(b"\n")
        return result

    monkeypatch.setattr(chapter.RecallEngine, "recall", modifying_recall)
    report, evidence = run_gate(module, chapter, tmp_path)
    assert evidence["audit_kept"] is True
    assert evidence["active_ids"] == ["mem-deploy-new"]
    assert evidence["store_unchanged"] is False

    # Verify a failed mechanism gate propagates to the CLI exit status.
    monkeypatch.setattr(module, "run_evaluation", lambda *_: report)
    monkeypatch.setattr(sys, "argv", [str(CODE)])
    assert module.main() == 1
