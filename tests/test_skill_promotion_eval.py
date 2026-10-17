"""Offline contracts for the held-out skill promotion gate example (card #49)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "examples" / "skill_promotion_eval" / "code.py"
EVOLVING = ROOT / "examples" / "self_evolving_skills" / "code.py"
CASE_IDS = ["chapter_rejects_leaked_validation", "naive_evaluator_accepts_leak"]


@pytest.fixture(scope="module")
def spe():
    name = "skill_promotion_eval_test_module"
    spec = importlib.util.spec_from_file_location(name, CODE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(name, None)


def test_chapter_evaluator_rejects_training_trace(spe, tmp_path):
    evolving = spe.load_evolving_skills()
    pipeline, candidate, leaked = spe.distill_with_leak(evolving, tmp_path / "store")
    assert leaked.trace_id in candidate.source_trace_ids
    report = pipeline.evaluate(candidate, validation=leaked)
    assert report.passed is False
    failed = {name for name, ok in report.checks.items() if not ok}
    assert {"held_out_trace", "held_out_digest"} <= failed


def test_naive_evaluator_accepts_same_leak(spe, tmp_path):
    evolving = spe.load_evolving_skills()
    _pipeline, candidate, leaked = spe.distill_with_leak(evolving, tmp_path / "store")
    report = spe.naive_evaluate(evolving, candidate, validation=leaked)
    assert isinstance(report, evolving.EvaluationReport)
    assert report.passed is True
    assert report.validation_trace_id == leaked.trace_id
    assert not (tmp_path / "store" / "candidates" / candidate.candidate_id / "evaluation.json").exists()


def test_reuses_memory_resilience_case_types(spe):
    assert spe.CaseDefinition is spe._CASES.CaseDefinition
    assert spe.CaseObservation is spe._CASES.CaseObservation
    assert spe.CaseResult is spe._CASES.CaseResult
    assert [case.case_id for case in spe.CASES] == CASE_IDS


def test_gate_fails_when_naive_starts_checking_held_out(spe, tmp_path, monkeypatch):
    def strict(evolving, candidate, *, validation):
        pipeline = evolving.SkillEvolutionPipeline(evolving.EvolutionStore(tmp_path / "strict"))
        return pipeline.evaluate(candidate, validation=validation)

    monkeypatch.setattr(spe, "naive_evaluate", strict)
    report = spe.run_evaluation(tmp_path / "out")
    assert report["passed"] is False
    assert [case["passed"] for case in report["cases"]] == [True, False]


def test_cli_green_report_and_example_untouched(tmp_path):
    before = hashlib.sha256(EVOLVING.read_bytes()).hexdigest()
    out_dir = tmp_path / "out"
    result = subprocess.run([sys.executable, str(CODE), "--output-dir", str(out_dir)],
                            capture_output=True, text=True, encoding="utf-8",
                            cwd=tmp_path, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: OK" in result.stdout
    report = json.loads((out_dir / "skill-promotion-report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert [case["case_id"] for case in report["cases"]] == CASE_IDS
    assert all(case["passed"] for case in report["cases"])
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out"]
    assert hashlib.sha256(EVOLVING.read_bytes()).hexdigest() == before
