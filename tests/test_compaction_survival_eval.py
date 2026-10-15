"""Offline contracts for the s14 compaction survival gate example (card #40)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "examples" / "compaction_survival_eval" / "code.py"
LOSSY = {"tool_result_truncation", "message_pruning", "conversation_summary"}


@pytest.fixture(scope="module")
def cse():
    name = "compaction_survival_eval_test_module"
    spec = importlib.util.spec_from_file_location(name, CODE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(name, None)


def test_bypass_keeps_fact_and_pending_through_lossy_layers(cse):
    row = cse.run_bypass(cse.load_compact_chapter())
    assert LOSSY <= set(row["applied_layers"])
    assert row["fact_in_prompt"] and row["pending_in_prompt"]


def test_inline_naive_loses_both_on_the_real_pipeline(cse):
    row = cse.run_inline_durable_then_compact(cse.load_compact_chapter())
    assert "conversation_summary" in row["applied_layers"]
    assert LOSSY <= set(row["applied_layers"])
    assert not row["fact_in_prompt"] and not row["pending_in_prompt"]


def test_cli_exits_0_and_report_contains_the_gate(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(CODE), "--output-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads((tmp_path / "compaction-survival-report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert [c["case_id"] for c in report["cases"]] == ["durable_bypass_survives_lossy_compaction"]


def test_gate_fails_when_bypass_stops_carrying_durable_state(cse, monkeypatch, tmp_path):
    real = cse.load_compact_chapter

    def broken():
        chapter = real()
        original = chapter.compact_context

        def drop_durable(messages, durable_state=None, **kwargs):
            return original(messages, chapter.EMPTY_DURABLE_STATE, **kwargs)

        chapter.compact_context = drop_durable
        return chapter

    monkeypatch.setattr(cse, "load_compact_chapter", broken)
    report = cse.run_evaluation(tmp_path)
    assert report["passed"] is False


def test_threshold_overrides_do_not_leak_to_other_instances(cse):
    forced = cse.load_compact_chapter()
    assert forced.TOKEN_THRESHOLD == 1
    name = "compaction_survival_eval_plain_s14"
    sys.path.insert(0, str(ROOT / "tests" / "stubs"))
    saved = sys.modules.pop("anthropic", None)
    try:
        spec = importlib.util.spec_from_file_location(name, ROOT / "s14_context_compact" / "code.py")
        plain = importlib.util.module_from_spec(spec)
        sys.modules[name] = plain
        spec.loader.exec_module(plain)
    finally:
        sys.path.remove(str(ROOT / "tests" / "stubs"))
        sys.modules.pop(name, None)
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved
    assert plain.TOKEN_THRESHOLD == 80_000
    assert plain.MAX_TOOL_RESULT_TOKENS == 5_000
