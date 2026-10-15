"""Offline contracts for the match_skill regression gates example (card #39)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "examples" / "skill_match_eval" / "code.py"


@pytest.fixture(scope="module")
def sme():
    name = "skill_match_eval_test_module"
    spec = importlib.util.spec_from_file_location(name, CODE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(name, None)


def _failed(rows):
    return [row["query"] for row in rows if not row["passed"]]


def test_cli_exits_0_and_reports_two_gates_and_two_known_false_matches(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(CODE), "--output-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads((tmp_path / "skill-match-report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert [c["case_id"] for c in report["cases"]] == [
        "chapter_matches_gold", "token_split_fix_breaks_cjk"]
    assert all(c["passed"] for c in report["cases"])
    assert len(report["known_false_matches"]) == 2


def test_chapter_passes_gold_and_naive_matchers_fail_named_rows(sme):
    chapter = sme.load_skills_chapter()
    assert _failed(sme.score_gold(chapter.match_skill)) == []
    assert "Please COMMIT this" in _failed(sme.score_gold(sme.case_sensitive_matcher(chapter)))
    tokenized = _failed(sme.score_gold(sme.tokenized_matcher(chapter)))
    assert "帮我做代码审查" in tokenized and "ready to git push" in tokenized


def test_gates_turn_red_when_chapter_matcher_breaks(sme, monkeypatch, tmp_path):
    real = sme.load_skills_chapter

    def broken():
        chapter = real()
        chapter.match_skill = sme.case_sensitive_matcher(chapter)
        return chapter

    monkeypatch.setattr(sme, "load_skills_chapter", broken)
    report = sme.run_evaluation(tmp_path)
    assert report["passed"] is False
    assert not any(c["passed"] for c in report["cases"])


def test_known_false_matches_are_recorded_without_affecting_passed(sme, tmp_path):
    report = sme.run_evaluation(tmp_path)
    assert report["passed"] is True
    got = {item["query"]: item for item in report["known_false_matches"]}
    assert got["preview the deploy"]["got"] == "code-review"
    assert got["uncommitted changes"]["got"] == "git-commit"
    for item in report["known_false_matches"]:
        assert item["status"] == "known_false_match"
        assert item["got"] != item["expected"], (
            f"{item['query']!r} now matches as expected: move it from KNOWN_FALSE_MATCHES into GOLD"
        )


def test_fresh_instance_ignores_skills_appended_to_another_instance(sme):
    polluted = sme.load_skills_chapter()
    seed = polluted.skill_index[0]
    polluted.skill_index.insert(0, type(seed)(
        title="hijack", summary="", read_when=["commit", "审查", "部署"], path="(memory)/hijack",
        content="", loaded=False, agent_created=True, permissions=seed.permissions,
    ))
    assert polluted.match_skill("please commit my changes") == "hijack"
    fresh = sme.load_skills_chapter()
    assert [s.title for s in fresh.skill_index] == ["git-commit", "code-review", "deploy-check"]
    assert _failed(sme.score_gold(fresh.match_skill)) == []
