#!/usr/bin/env python3
"""Offline counterfactual gate for held-out evaluation in self_evolving_skills.

Promotion only trusts the evaluation report, so the evaluator itself must be
able to fail.  This example feeds the same leaked trajectory (``train-run-1``,
which was distilled into the candidate) to the example's real ``evaluate()``
and to a deliberately lazy evaluator that only checks ``validation.successful``.
Only the chapter evaluator rejects the leak.  Nothing is saved for promotion.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
EVOLVING_CODE = ROOT / "examples" / "self_evolving_skills" / "code.py"
DEFAULT_OUTPUT = ROOT / ".tmp" / "skill-promotion-eval"
REPORT_NAME = "skill-promotion-report.json"
TASK_FAMILY = "python-test-validation"
LEAKED_TRACE = "train-run-1"
HELD_OUT_CHECKS = ("held_out_trace", "held_out_digest")


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses consult sys.modules while executing
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


# Reuse the case types of the Memory resilience scorecard instead of copying them.
_CASES = _load_module(
    "_learn_workbuddy_skill_promotion_eval_cases",
    ROOT / "examples" / "memory_resilience_eval" / "code.py",
)
CaseDefinition = _CASES.CaseDefinition
CaseObservation = _CASES.CaseObservation
CaseResult = _CASES.CaseResult


def load_evolving_skills() -> ModuleType:
    return _load_module("_learn_workbuddy_skill_promotion_eval_evolving", EVOLVING_CODE)


def distill_with_leak(evolving: ModuleType, store_root: Path):
    """Seed a fresh store, distill the candidate, and return it with the leaked trace."""

    store = evolving.EvolutionStore(store_root)
    pipeline = evolving.SkillEvolutionPipeline(store)
    trajectories = evolving.seed_demo_trajectories(store)
    training, _rejected = pipeline.triage(trajectories, task_family=TASK_FAMILY)
    candidate = pipeline.distill(training, task_family=TASK_FAMILY)
    leaked = next(item for item in trajectories if item.trace_id == LEAKED_TRACE)
    return pipeline, candidate, leaked


def naive_evaluate(evolving: ModuleType, candidate, *, validation):
    """Deliberately lazy: only asks whether the validation run succeeded."""

    checks = {"validation_successful": validation.successful}
    return evolving.EvaluationReport(
        candidate_id=candidate.candidate_id,
        validation_trace_id=validation.trace_id,
        checks=checks,
        passed=all(checks.values()),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def _row(report, candidate, leaked) -> dict[str, object]:
    return {
        "validation_trace_id": report.validation_trace_id,
        "leaked_trace_in_sources": leaked.trace_id in candidate.source_trace_ids,
        "leaked_split": leaked.split,
        "passed": report.passed,
        "failed_checks": sorted(name for name, ok in report.checks.items() if not ok),
    }


def _chapter_rejects_leak(evolving: ModuleType, _output: Path) -> CaseObservation:
    with tempfile.TemporaryDirectory() as tmp:
        pipeline, candidate, leaked = distill_with_leak(evolving, Path(tmp) / "store")
        report = pipeline.evaluate(candidate, validation=leaked)
    row = _row(report, candidate, leaked)
    held_out_failed = all(name in row["failed_checks"] for name in HELD_OUT_CHECKS)
    return CaseObservation(
        passed=report.passed is False and held_out_failed,
        observed=f"chapter evaluate() on {LEAKED_TRACE}: passed={report.passed}; "
                 f"held-out checks failed: {held_out_failed}",
        evidence={"chapter": row},
    )


def _naive_accepts_leak(evolving: ModuleType, _output: Path) -> CaseObservation:
    with tempfile.TemporaryDirectory() as tmp:
        _pipeline, candidate, leaked = distill_with_leak(evolving, Path(tmp) / "store")
        report = naive_evaluate(evolving, candidate, validation=leaked)
    # passed=true means the leak was demonstrated, not that the naive evaluator is correct.
    return CaseObservation(
        passed=report.passed is True,
        observed=f"naive evaluator on {LEAKED_TRACE}: passed={report.passed}",
        evidence={"naive": _row(report, candidate, leaked)},
    )


CASES = (
    CaseDefinition(
        "chapter_rejects_leaked_validation",
        "evaluate() with a training trajectory fails, including held_out_trace and held_out_digest",
        _chapter_rejects_leak,
    ),
    CaseDefinition(
        "naive_evaluator_accepts_leak",
        "an evaluator that only checks validation.successful passes the same leaked trajectory",
        _naive_accepts_leak,
    ),
)


def run_evaluation(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for case in CASES:
        observation = case.runner(load_evolving_skills(), output_dir)
        results.append(CaseResult(case.case_id, case.contract, observation.passed,
                                  observation.observed, observation.evidence))
    report = {
        "passed": all(result.passed for result in results),
        "cases": [result.to_dict() for result in results],
    }
    report_path = output_dir / REPORT_NAME
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the keyless held-out evaluation leak gate.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_evaluation(args.output_dir)
    print("Skill promotion held-out gate")
    for case in report["cases"]:
        print(f"[{'PASS' if case['passed'] else 'FAIL'}] {case['case_id']}: {case['observed']}")
        for name, row in case["evidence"].items():
            print(f"  {name}: passed={row['passed']} failed_checks={row['failed_checks']}")
    print(f"Report: {report['report_path']}")
    print("RESULT: OK" if report["passed"] else "RESULT: FAILED")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
