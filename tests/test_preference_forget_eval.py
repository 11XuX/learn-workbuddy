"""Offline contracts for the s11 preference-forget gate example (card #45)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "examples" / "preference_forget_eval" / "code.py"
S11 = ROOT / "s11_user_memory" / "code.py"
CASE_IDS = ["chapter_tombstone_blocks_stale_replay", "naive_pop_allows_revival"]


@pytest.fixture(scope="module")
def pfe():
    name = "preference_forget_eval_test_module"
    spec = importlib.util.spec_from_file_location(name, CODE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(name, None)


def test_naive_delete_lets_stale_evidence_revive(pfe, tmp_path):
    chapter = pfe.load_user_memory_chapter()
    row = pfe.run_naive_forget(chapter, tmp_path)
    assert row["replay"] == "accepted"
    assert row["revived"] is True
    assert row["tombstones"] == []
    stored = json.loads(chapter.UserMemory(tmp_path).preferences_path.read_text(encoding="utf-8"))
    assert [item["key"] for item in stored["preferences"]] == [pfe.KEY]


def test_naive_delete_alone_writes_pop_to_disk(pfe, tmp_path):
    chapter = pfe.load_user_memory_chapter()
    memory = chapter.UserMemory(tmp_path)
    pfe.set_fixture(memory)
    pfe.naive_delete(memory, pfe.KEY)
    reopened = chapter.UserMemory(tmp_path)
    assert reopened.list_preferences() == []
    assert reopened.list_deleted_preferences() == []


def test_chapter_path_cannot_revive(pfe, tmp_path):
    chapter = pfe.load_user_memory_chapter()
    row = pfe.run_chapter_forget(chapter, tmp_path)
    assert row["replay"] == "rejected_stale"
    assert row["revived"] is False
    assert row["active_after_restart"] == []
    assert row["tombstones"] == [pfe.KEY]
    with pytest.raises(chapter.StalePreferenceUpdateError):
        pfe.set_fixture(chapter.UserMemory(tmp_path))


def test_reuses_memory_resilience_case_types(pfe):
    cases = pfe._CASES
    assert pfe.CaseDefinition is cases.CaseDefinition
    assert pfe.CaseObservation is cases.CaseObservation
    assert pfe.CaseResult is cases.CaseResult
    assert [case.case_id for case in pfe.CASES] == CASE_IDS


def test_gate_fails_when_naive_stops_reviving(pfe, tmp_path, monkeypatch):
    def fixed_delete(memory, key):
        memory.delete_preference(key)

    monkeypatch.setattr(pfe, "naive_delete", fixed_delete)
    report = pfe.run_evaluation(tmp_path)
    assert report["passed"] is False
    assert all(case["passed"] is False for case in report["cases"])


def test_cli_offline_green_report_and_no_naive_switch(tmp_path):
    before = hashlib.sha256(S11.read_bytes()).hexdigest()
    env = {k: v for k, v in os.environ.items() if k not in {"ANTHROPIC_API_KEY", "MODEL_ID"}}
    env["WORKBUDDY_HOME"] = str(tmp_path / "home")
    out_dir = tmp_path / "out"
    result = subprocess.run([sys.executable, str(CODE), "--output-dir", str(out_dir)],
                            capture_output=True, text=True, encoding="utf-8",
                            cwd=tmp_path, env=env, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: OK" in result.stdout
    report = json.loads((out_dir / "preference-forget-report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert [case["case_id"] for case in report["cases"]] == CASE_IDS
    assert all(case["passed"] for case in report["cases"])
    helped = subprocess.run([sys.executable, str(CODE), "--help"], capture_output=True,
                            text=True, encoding="utf-8", env=env, timeout=60)
    assert "naive" not in helped.stdout
    assert hashlib.sha256(S11.read_bytes()).hexdigest() == before


# --- Verifier adversarial tests ---


def test_adversarial_broken_chapter_makes_cli_fail(
    pfe, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(pfe, "run_chapter_forget", pfe.run_naive_forget)
    monkeypatch.setattr(
        sys, "argv", [str(CODE), "--output-dir", str(tmp_path)]
    )

    assert pfe.main() == 1
    stdout = capsys.readouterr().out
    assert "RESULT: FAILED" in stdout
    assert "RESULT: OK" not in stdout

    report = json.loads(
        (tmp_path / pfe.REPORT_NAME).read_text(encoding="utf-8")
    )
    assert report["passed"] is False
    assert [case["case_id"] for case in report["cases"]] == CASE_IDS
    assert all(case["passed"] is False for case in report["cases"])
    for case in report["cases"]:
        assert set(case["evidence"]) == {"chapter", "naive_pop"}
        assert case["evidence"]["chapter"]["revived"] is True
        assert case["evidence"]["naive_pop"]["revived"] is True


def test_adversarial_one_failed_case_controls_report_and_cli(
    pfe, tmp_path, monkeypatch, capsys
):
    first, second = pfe.CASES

    def fail_first(chapter, output_dir):
        observation = first.runner(chapter, output_dir)
        assert observation.passed is True
        return pfe.CaseObservation(
            passed=False,
            observed="injected single-case gate failure",
            evidence=observation.evidence,
        )

    monkeypatch.setattr(
        pfe,
        "CASES",
        (
            pfe.CaseDefinition(first.case_id, first.contract, fail_first),
            second,
        ),
    )
    report = pfe.run_evaluation(tmp_path)
    assert [case["passed"] for case in report["cases"]] == [False, True]
    assert report["passed"] is False
    assert [case["case_id"] for case in report["cases"]] == CASE_IDS

    disk_report = json.loads(
        (tmp_path / pfe.REPORT_NAME).read_text(encoding="utf-8")
    )
    assert disk_report == {
        key: value for key, value in report.items() if key != "report_path"
    }
    for case in disk_report["cases"]:
        assert set(case["evidence"]) == {"chapter", "naive_pop"}

    monkeypatch.setattr(
        sys, "argv", [str(CODE), "--output-dir", str(tmp_path)]
    )
    assert pfe.main() == 1
    assert "RESULT: FAILED" in capsys.readouterr().out
    cli_report = json.loads(
        (tmp_path / pfe.REPORT_NAME).read_text(encoding="utf-8")
    )
    assert cli_report == disk_report
