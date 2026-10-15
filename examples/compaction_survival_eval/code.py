#!/usr/bin/env python3
"""Offline counterfactual gate for S14 durable state under lossy compaction.

The chapter keeps facts and pending work in ``durable_state``, which bypasses
the L1-L4 message pipeline.  This example runs the same input twice through
the chapter's real ``compact_context``: once with the durable bypass, once with
the durable block inlined as a message.  Only the bypass keeps both items.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
S14_CODE = ROOT / "s14_context_compact" / "code.py"
STUB_DIR = ROOT / "tests" / "stubs"
DEFAULT_OUTPUT = ROOT / ".tmp" / "compaction-survival-eval"
REPORT_NAME = "compaction-survival-report.json"

FACT = "Workspace persistence uses SQLite WAL."
PENDING = "Run the migration rollback test."
CONFIRMED_AT = "2026-08-12T10:00:00+08:00"


def adversarial_summary(_conversation: str) -> str:
    return "Persistence uses plain JSON. There is no pending migration work."


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
    "_learn_workbuddy_compaction_survival_eval_cases",
    ROOT / "examples" / "memory_resilience_eval" / "code.py",
)
CaseDefinition = _CASES.CaseDefinition
CaseObservation = _CASES.CaseObservation
CaseResult = _CASES.CaseResult


def load_compact_chapter() -> ModuleType:
    """Load a fresh S14 instance and force L1/L3/L4 with tiny thresholds."""

    saved_anthropic = sys.modules.pop("anthropic", None)
    sys.path.insert(0, str(STUB_DIR))
    try:
        chapter = _load_module("_learn_workbuddy_compaction_survival_eval_s14", S14_CODE)
    finally:
        sys.path.remove(str(STUB_DIR))
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
    chapter.TOKEN_THRESHOLD = 1
    chapter.MAX_TOOL_RESULT_TOKENS = 1
    chapter.KEEP_RECENT_TURNS = 6
    return chapter


def durable_state(chapter: ModuleType):
    return chapter.DurableContextState(
        facts=(chapter.DurableFact("fact-persistence", FACT, "transcript:demo:17", CONFIRMED_AT),),
        pending_items=(
            chapter.PendingItem("pending-rollback", PENDING, "transcript:demo:21", CONFIRMED_AT),
        ),
    )


def long_messages() -> list[dict]:
    """About eight long turns plus one large tool_result, with valid tool pairing."""

    messages: list[dict] = [{"role": "user", "content": "Keep the original request."}]
    for index in range(8):
        content: str | list[dict] = f"turn-{index} " + ("detail " * 80)
        if index == 0:
            content = [
                {"type": "text", "text": content},
                {"type": "tool_use", "id": "call-1", "name": "read_file",
                 "input": {"path": "src/app.py"}},
            ]
        messages.append({"role": "assistant" if index % 2 == 0 else "user", "content": content})
    messages.insert(2, {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call-1", "content": "large-result " * 200},
    ]})
    return messages


def prompt_view(chapter: ModuleType, result) -> str:
    """What agent_loop sends the model: messages plus the durable system block."""

    return str(result.messages) + chapter.render_durable_context(result.durable_state)


def _observe(chapter: ModuleType, result) -> dict[str, object]:
    view = prompt_view(chapter, result)
    return {
        "applied_layers": list(result.applied_layers),
        "tokens_before": result.tokens_before,
        "tokens_after": result.tokens_after,
        "fact_in_prompt": FACT in view,
        "pending_in_prompt": PENDING in view,
    }


def run_bypass(chapter: ModuleType) -> dict[str, object]:
    result = chapter.compact_context(
        long_messages(), durable_state(chapter), summarizer=adversarial_summary, verbose=False
    )
    return _observe(chapter, result)


def run_inline_durable_then_compact(chapter: ModuleType) -> dict[str, object]:
    """Naive: treat memory as one more context message, then use the same pipeline."""

    messages = long_messages()
    messages.insert(1, {"role": "user", "content": chapter.render_durable_context(durable_state(chapter))})
    result = chapter.compact_context(
        messages, chapter.EMPTY_DURABLE_STATE, summarizer=adversarial_summary, verbose=False
    )
    return _observe(chapter, result)


def _durable_bypass_survives(chapter: ModuleType, _output: Path) -> CaseObservation:
    bypass = run_bypass(chapter)
    inline = run_inline_durable_then_compact(chapter)
    bypass_ok = bool(bypass["fact_in_prompt"] and bypass["pending_in_prompt"])
    inline_lost = not inline["fact_in_prompt"] and not inline["pending_in_prompt"]
    # The gate passes when the bypass keeps both items AND the inline naive is caught losing them.
    return CaseObservation(
        passed=bypass_ok and inline_lost,
        observed=f"bypass keeps fact+pending: {bypass_ok}; inline naive loses both: {inline_lost}",
        evidence={"bypass": bypass, "inline_durable_then_compact": inline},
    )


CASES = (
    CaseDefinition(
        "durable_bypass_survives_lossy_compaction",
        "durable facts and pending work survive L1-L4 only through the durable bypass",
        _durable_bypass_survives,
    ),
)


def run_evaluation(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for case in CASES:
        observation = case.runner(load_compact_chapter(), output_dir)
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
    parser = argparse.ArgumentParser(description="Run the keyless s14 compaction survival gate.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_evaluation(args.output_dir)
    print("Compaction survival gate")
    for case in report["cases"]:
        print(f"[{'PASS' if case['passed'] else 'FAIL'}] {case['case_id']}: {case['observed']}")
        for name, row in case["evidence"].items():
            print(f"  {name}: layers={','.join(row['applied_layers'])} "
                  f"tokens {row['tokens_before']}->{row['tokens_after']} "
                  f"fact={row['fact_in_prompt']} pending={row['pending_in_prompt']}")
    print(f"Report: {report['report_path']}")
    print("RESULT: OK" if report["passed"] else "RESULT: FAILED")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
