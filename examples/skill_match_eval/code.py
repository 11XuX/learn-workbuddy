#!/usr/bin/env python3
"""Offline regression gates for the S16 keyword skill matcher.

This is a regression lock on the current ``match_skill`` contract (lowercase
substring match on ``read_when`` triggers), not a quality score.  Known false
matches are recorded in the report but do not affect ``passed``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
S16_CODE = ROOT / "s16_skills_system" / "code.py"
STUB_DIR = ROOT / "tests" / "stubs"
DEFAULT_OUTPUT = ROOT / ".tmp" / "skill-match-eval"
REPORT_NAME = "skill-match-report.json"


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
    "_learn_workbuddy_skill_match_eval_cases",
    ROOT / "examples" / "memory_resilience_eval" / "code.py",
)
CaseDefinition = _CASES.CaseDefinition
CaseObservation = _CASES.CaseObservation
CaseResult = _CASES.CaseResult

# Single-intent inputs only, written from this repo's seed read_when triggers.
GOLD: tuple[tuple[str, str | None], ...] = (
    ("please commit my changes", "git-commit"),
    ("Please COMMIT this", "git-commit"),
    ("ready to git push", "git-commit"),
    ("帮我做代码审查", "code-review"),
    ("部署前检查一下", "deploy-check"),
    ("review this PR", "code-review"),
    ("hello world", None),
    ("", None),
    ("   ", None),
)

# Recorded only: current substring matching misfires here.  Not part of any gate.
KNOWN_FALSE_MATCHES: tuple[tuple[str, str | None], ...] = (
    ("preview the deploy", "deploy-check"),
    ("uncommitted changes", None),
)

Matcher = Callable[[str], "str | None"]


def load_skills_chapter() -> ModuleType:
    """Load a fresh S16 instance so skill_index is always the seed index."""

    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    os.environ["MODEL_ID"] = "offline-test-model"
    sys.path.insert(0, str(STUB_DIR))
    try:
        return _load_module("_learn_workbuddy_skill_match_eval_s16", S16_CODE)
    finally:
        sys.path.remove(str(STUB_DIR))
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        if old_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = old_model


def case_sensitive_matcher(chapter: ModuleType) -> Matcher:
    """Naive: forgets .lower() on the input."""

    def match(user_input: str) -> str | None:
        for skill in chapter.skill_index:
            if any(trigger in user_input for trigger in skill.read_when):
                return skill.title
        return None

    return match


def tokenized_matcher(chapter: ModuleType) -> Matcher:
    """Naive: whole-word match, the natural fix for the 'preview' false match."""

    def match(user_input: str) -> str | None:
        words = user_input.lower().split()
        for skill in chapter.skill_index:
            if any(trigger.lower() in words for trigger in skill.read_when):
                return skill.title
        return None

    return match


def score_gold(matcher: Matcher) -> list[dict[str, object]]:
    rows = []
    for query, expected in GOLD:
        got = matcher(query)
        rows.append({"query": query, "expected": expected, "got": got, "passed": got == expected})
    return rows


def _failed_queries(rows: list[dict[str, object]]) -> list[str]:
    return [str(row["query"]) for row in rows if not row["passed"]]


def _chapter_matches_gold(chapter: ModuleType, _output: Path) -> CaseObservation:
    chapter_rows = score_gold(chapter.match_skill)
    naive_failed = _failed_queries(score_gold(case_sensitive_matcher(chapter)))
    passed = not _failed_queries(chapter_rows) and "Please COMMIT this" in naive_failed
    return CaseObservation(
        passed=passed,
        observed=f"chapter {len(chapter_rows) - len(_failed_queries(chapter_rows))}/{len(GOLD)}; "
        f"case_sensitive naive failed on {naive_failed}",
        evidence={"chapter": chapter_rows, "case_sensitive_failed": naive_failed},
    )


def _token_split_fix_breaks_cjk(chapter: ModuleType, _output: Path) -> CaseObservation:
    chapter_failed = _failed_queries(score_gold(chapter.match_skill))
    naive_failed = _failed_queries(score_gold(tokenized_matcher(chapter)))
    required = ["帮我做代码审查", "ready to git push"]
    passed = not chapter_failed and all(query in naive_failed for query in required)
    return CaseObservation(
        passed=passed,
        observed=f"tokenized naive failed on {naive_failed}; chapter failed on {chapter_failed}",
        evidence={"tokenized_failed": naive_failed, "required": required, "chapter_failed": chapter_failed},
    )


CASES = (
    CaseDefinition(
        "chapter_matches_gold",
        "chapter match_skill passes every GOLD row; a matcher without .lower() does not",
        _chapter_matches_gold,
    ),
    CaseDefinition(
        "token_split_fix_breaks_cjk",
        "whole-word matching breaks CJK and multi-word triggers that the chapter handles",
        _token_split_fix_breaks_cjk,
    ),
)


def known_false_matches(chapter: ModuleType) -> list[dict[str, object]]:
    return [
        {"query": query, "expected": expected, "got": chapter.match_skill(query),
         "status": "known_false_match"}
        for query, expected in KNOWN_FALSE_MATCHES
    ]


def run_evaluation(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for case in CASES:
        observation = case.runner(load_skills_chapter(), output_dir)
        results.append(CaseResult(case.case_id, case.contract, observation.passed,
                                  observation.observed, observation.evidence))
    report = {
        "passed": all(result.passed for result in results),
        "cases": [result.to_dict() for result in results],
        "known_false_matches": known_false_matches(load_skills_chapter()),
    }
    report_path = output_dir / REPORT_NAME
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the keyless match_skill regression gates.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_evaluation(args.output_dir)
    print("Skill match regression gates")
    for case in report["cases"]:
        print(f"[{'PASS' if case['passed'] else 'FAIL'}] {case['case_id']}: {case['observed']}")
    for item in report["known_false_matches"]:
        print(f"[KNOWN] {item['query']!r}: got {item['got']!r}, expected {item['expected']!r}")
    print(f"Report: {report['report_path']}")
    print("RESULT: OK" if report["passed"] else "RESULT: FAILED")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
