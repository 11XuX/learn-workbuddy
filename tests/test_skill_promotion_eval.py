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


# Verifier adversarial tests (card #49, code gate 2).

@pytest.mark.parametrize("failed_index", [0, 1])
def test_main_exits_nonzero_when_either_gate_fails(
    spe, tmp_path, monkeypatch, failed_index
):
    from dataclasses import replace

    def passing_runner(evolving, output):
        return spe.CaseObservation(True, "controlled pass", {})

    def failing_runner(evolving, output):
        return spe.CaseObservation(False, "controlled failure", {})

    cases = tuple(
        replace(
            case,
            runner=failing_runner if index == failed_index else passing_runner,
        )
        for index, case in enumerate(spe.CASES)
    )
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(spe, "CASES", cases)
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        sys, "argv", [str(CODE), "--output-dir", str(out_dir)]
    )

    assert spe.main() != 0

    report = json.loads(
        (out_dir / spe.REPORT_NAME).read_text(encoding="utf-8")
    )
    assert report["passed"] is False
    assert [case["case_id"] for case in report["cases"]] == CASE_IDS
    assert [case["passed"] for case in report["cases"]] == [
        index != failed_index for index in range(len(CASE_IDS))
    ]


@pytest.mark.parametrize("missing_check", ["held_out_trace", "held_out_digest"])
def test_chapter_gate_requires_both_held_out_failures(
    spe, tmp_path, monkeypatch, missing_check
):
    from dataclasses import replace

    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(spe.tempfile, "tempdir", str(tmp_path))
    evolving = spe.load_evolving_skills()
    original_evaluate = evolving.SkillEvolutionPipeline.evaluate

    def evaluate_without_one_check(pipeline, candidate, *, validation):
        report = original_evaluate(
            pipeline, candidate, validation=validation
        )
        assert report.passed is False
        assert report.checks[missing_check] is False
        checks = dict(report.checks)
        del checks[missing_check]
        return replace(report, checks=checks)

    monkeypatch.setattr(
        evolving.SkillEvolutionPipeline,
        "evaluate",
        evaluate_without_one_check,
    )

    observation = spe._chapter_rejects_leak(evolving, tmp_path / "out")

    assert observation.passed is False


def test_both_cases_actually_evaluate_the_leaked_training_trace(
    spe, tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(spe.tempfile, "tempdir", str(tmp_path))
    evolving = spe.load_evolving_skills()
    original_evaluate = evolving.SkillEvolutionPipeline.evaluate
    original_naive = spe.naive_evaluate
    original_distill = spe.distill_with_leak
    calls = []
    store_roots = []

    def check_leak(candidate, validation):
        assert validation.trace_id == "train-run-1"
        assert validation.split == "train"
        assert validation.trace_id in candidate.source_trace_ids
        assert validation.source_digest in candidate.source_digests

    def chapter_spy(pipeline, candidate, *, validation):
        check_leak(candidate, validation)
        calls.append(("chapter", validation.trace_id))
        return original_evaluate(
            pipeline, candidate, validation=validation
        )

    def naive_spy(module, candidate, *, validation):
        check_leak(candidate, validation)
        calls.append(("naive", validation.trace_id))
        return original_naive(module, candidate, validation=validation)

    def distill_spy(module, store_root):
        result = original_distill(module, store_root)
        root = result[0].store.root
        assert tmp_path.resolve() in root.parents
        store_roots.append(root)
        return result

    monkeypatch.setattr(spe, "load_evolving_skills", lambda: evolving)
    monkeypatch.setattr(
        evolving.SkillEvolutionPipeline, "evaluate", chapter_spy
    )
    monkeypatch.setattr(spe, "naive_evaluate", naive_spy)
    monkeypatch.setattr(spe, "distill_with_leak", distill_spy)

    report = spe.run_evaluation(tmp_path / "out")

    assert report["passed"] is True
    assert calls == [
        ("chapter", "train-run-1"),
        ("naive", "train-run-1"),
    ]
    assert len(store_roots) == 2
    assert store_roots[0] != store_roots[1]
