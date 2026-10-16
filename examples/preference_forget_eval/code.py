#!/usr/bin/env python3
"""Offline counterfactual gate for S11 preference forgetting.

The chapter forgets a preference by writing a tombstone, so replaying the old
evidence cannot bring it back.  This example runs the same fixture twice: once
through the chapter's real ``delete_preference``, once through a deliberately
wrong naive delete that only pops the key and writes the file back.  Only the
naive path lets the stale evidence revive the preference.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
S11_CODE = ROOT / "s11_user_memory" / "code.py"
STUB_DIR = ROOT / "tests" / "stubs"
DEFAULT_OUTPUT = ROOT / ".tmp" / "preference-forget-eval"
REPORT_NAME = "preference-forget-report.json"

KEY = "response.language"
VALUE = "Answer in Traditional Chinese."
UPDATED_AT = "2026-08-12T10:00:00+08:00"
SOURCE_EVENT_ID = "transcript:demo:7"


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
    "_learn_workbuddy_preference_forget_eval_cases",
    ROOT / "examples" / "memory_resilience_eval" / "code.py",
)
CaseDefinition = _CASES.CaseDefinition
CaseObservation = _CASES.CaseObservation
CaseResult = _CASES.CaseResult


def load_user_memory_chapter() -> ModuleType:
    """Load a fresh S11 instance against the offline anthropic stub."""

    saved_anthropic = sys.modules.pop("anthropic", None)
    saved_model = os.environ.get("MODEL_ID")
    os.environ["MODEL_ID"] = "offline-test-model"
    sys.path.insert(0, str(STUB_DIR))
    try:
        return _load_module("_learn_workbuddy_preference_forget_eval_s11", S11_CODE)
    finally:
        sys.path.remove(str(STUB_DIR))
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        if saved_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = saved_model


def set_fixture(memory) -> None:
    memory.set_preference(KEY, VALUE, updated_at=UPDATED_AT, source_event_id=SOURCE_EVENT_ID)


def naive_delete(memory, key: str) -> None:
    """Deliberately wrong: pop the key and write the file back, no tombstone."""

    preferences, deleted = memory._load_preference_state()
    preferences.pop(key, None)
    memory._store_preferences(preferences.values(), deleted.values())


def _replay_and_restart(chapter: ModuleType, base_dir: Path) -> dict[str, object]:
    """Replay the same evidence, then reopen the store to read what survived."""

    memory = chapter.UserMemory(base_dir)
    try:
        set_fixture(memory)
        replay = "accepted"
    except chapter.StalePreferenceUpdateError:
        replay = "rejected_stale"
    restarted = chapter.UserMemory(base_dir)
    active = [item.key for item in restarted.list_active_preferences()]
    tombstones = [item.key for item in restarted.list_deleted_preferences()]
    return {"replay": replay, "active_after_restart": active,
            "tombstones": tombstones, "revived": KEY in active}


def run_chapter_forget(chapter: ModuleType, base_dir: Path) -> dict[str, object]:
    memory = chapter.UserMemory(base_dir)
    set_fixture(memory)
    memory.delete_preference(KEY)
    return _replay_and_restart(chapter, base_dir)


def run_naive_forget(chapter: ModuleType, base_dir: Path) -> dict[str, object]:
    memory = chapter.UserMemory(base_dir)
    set_fixture(memory)
    naive_delete(memory, KEY)
    return _replay_and_restart(chapter, base_dir)


def _tombstone_blocks_stale_replay(chapter: ModuleType, _output: Path) -> CaseObservation:
    with tempfile.TemporaryDirectory() as tmp:
        chapter_row = run_chapter_forget(chapter, Path(tmp) / "chapter")
        naive_row = run_naive_forget(chapter, Path(tmp) / "naive")
    chapter_ok = (chapter_row["replay"] == "rejected_stale" and not chapter_row["revived"]
                  and KEY in chapter_row["tombstones"])
    naive_revived = naive_row["replay"] == "accepted" and naive_row["revived"]
    # The gate passes when the chapter blocks the replay AND the naive pop is caught reviving.
    return CaseObservation(
        passed=chapter_ok and naive_revived,
        observed=f"chapter blocks stale replay: {chapter_ok}; naive pop revives: {naive_revived}",
        evidence={"chapter": chapter_row, "naive_pop": naive_row},
    )


def _naive_pop_allows_revival(chapter: ModuleType, _output: Path) -> CaseObservation:
    with tempfile.TemporaryDirectory() as tmp:
        naive_row = run_naive_forget(chapter, Path(tmp) / "naive")
        chapter_row = run_chapter_forget(chapter, Path(tmp) / "chapter")
    naive_revived = naive_row["revived"] and not naive_row["tombstones"]
    chapter_ok = not chapter_row["revived"]
    # passed=true means the bug was demonstrated, not that the naive forget is correct.
    return CaseObservation(
        passed=naive_revived and chapter_ok,
        observed=f"naive pop leaves no tombstone and revives: {naive_revived}; chapter stays forgotten: {chapter_ok}",
        evidence={"naive_pop": naive_row, "chapter": chapter_row},
    )


CASES = (
    CaseDefinition(
        "chapter_tombstone_blocks_stale_replay",
        "after delete_preference, replaying the same evidence raises and nothing revives after restart",
        _tombstone_blocks_stale_replay,
    ),
    CaseDefinition(
        "naive_pop_allows_revival",
        "a delete that only pops the key lets the same evidence revive the preference",
        _naive_pop_allows_revival,
    ),
)


def run_evaluation(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for case in CASES:
        observation = case.runner(load_user_memory_chapter(), output_dir)
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
    parser = argparse.ArgumentParser(description="Run the keyless s11 preference-forget gate.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_evaluation(args.output_dir)
    print("Preference forget gate")
    for case in report["cases"]:
        print(f"[{'PASS' if case['passed'] else 'FAIL'}] {case['case_id']}: {case['observed']}")
        for name, row in case["evidence"].items():
            print(f"  {name}: replay={row['replay']} revived={row['revived']} "
                  f"tombstones={row['tombstones']}")
    print(f"Report: {report['report_path']}")
    print("RESULT: OK" if report["passed"] else "RESULT: FAILED")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
