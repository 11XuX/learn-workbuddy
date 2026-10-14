"""Offline CLI contracts for rolling back self_evolving_skills (card #33)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "examples" / "self_evolving_skills" / "code.py"
TITLE = "python-test-validation"
REASON = "v2-regression-in-step-3"


def _cli(home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CODE), "--home", str(home), *args],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )


def _ok(home: Path, *args: str) -> subprocess.CompletedProcess:
    proc = _cli(home, *args)
    assert proc.returncode == 0, proc.stderr
    return proc


def _tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _manifest(home: Path) -> dict:
    return json.loads((home / "skills" / TITLE / "manifest.json").read_text(encoding="utf-8"))


def _audit(home: Path) -> list[dict]:
    lines = (home / "evolution-audit.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


@pytest.fixture
def evolution():
    spec = importlib.util.spec_from_file_location("self_evolving_skills_cli_test", CODE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture
def two_versions(tmp_path):
    home = tmp_path / "home"
    _ok(home, "--approve", "--approved-by", "alice")
    revised = _ok(home, "--seed", "revised", "--approve", "--approved-by", "alice")
    assert "Held-out evaluation passed: True" in revised.stdout
    manifest = _manifest(home)
    assert manifest["active_version"] == 2 and len(manifest["history"]) == 2
    return home


def test_set_active_rolls_back_with_signed_audit_and_is_idempotent(two_versions, evolution):
    home = two_versions
    v2 = home / "skills" / TITLE / "v2" / "SKILL.md"
    v2_bytes = v2.read_bytes()
    _ok(home, "--set-active", "1", "--approved-by", "bob", "--reason", REASON)
    store = evolution.EvolutionStore(home)
    assert store.active_skill_path(TITLE) == home.resolve() / "skills" / TITLE / "v1" / "SKILL.md"
    last = _audit(home)[-1]
    assert last["action"] == "skill_activated"
    assert last["details"]["from_version"] == 2 and last["details"]["to_version"] == 1
    assert last["details"]["approved_by"] == "bob" and last["details"]["reason"] == REASON
    assert v2.read_bytes() == v2_bytes
    count = len(_audit(home))
    _ok(home, "--set-active", "1", "--approved-by", "bob", "--reason", REASON)
    assert len(_audit(home)) == count


@pytest.mark.parametrize("args", [
    ["--set-active", "1", "--approved-by", "bob"],
    ["--set-active", "1", "--reason", REASON],
    ["--set-active", "1", "--approved-by", "bob", "--reason", REASON, "--approve"],
])
def test_set_active_usage_errors_change_nothing(two_versions, args):
    before = _tree(two_versions)
    assert _cli(two_versions, *args).returncode == 2
    assert _tree(two_versions) == before


def test_set_active_library_rejections_exit_1_without_traceback(two_versions):
    home = two_versions
    snapshot = _tree(home)
    proc = _cli(home, "--set-active", "3", "--approved-by", "bob", "--reason", REASON)
    assert proc.returncode == 1
    assert "activation rejected" in proc.stderr and "Traceback" not in proc.stderr
    assert _tree(home) == snapshot
    v1 = home / "skills" / TITLE / "v1" / "SKILL.md"
    v1.write_text(v1.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    manifest_bytes = (home / "skills" / TITLE / "manifest.json").read_bytes()
    audit_bytes = (home / "evolution-audit.jsonl").read_bytes()
    proc = _cli(home, "--set-active", "1", "--approved-by", "bob", "--reason", REASON)
    assert proc.returncode == 1
    assert "activation rejected" in proc.stderr and "Traceback" not in proc.stderr
    assert (home / "skills" / TITLE / "manifest.json").read_bytes() == manifest_bytes
    assert (home / "evolution-audit.jsonl").read_bytes() == audit_bytes


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_approver_is_rejected_before_any_write(tmp_path, blank, evolution):
    fresh = tmp_path / "fresh"
    proc = _cli(fresh, "--approve", "--approved-by", blank)
    assert proc.returncode != 0 and "Traceback" not in proc.stderr
    assert not fresh.exists() or not any(fresh.iterdir())

    home = tmp_path / "home"
    _ok(home, "--approve", "--approved-by", "alice")
    snapshot, audit_count = _tree(home), len(_audit(home))
    proc = _cli(home, "--approve", "--approved-by", blank)
    assert proc.returncode != 0 and "Traceback" not in proc.stderr
    assert _tree(home) == snapshot
    assert len(_manifest(home)["history"]) == 1 and len(_audit(home)) == audit_count

    lib_home = tmp_path / "lib"
    with pytest.raises(evolution.EvolutionError, match="approved_by"):
        evolution.run_demo(lib_home, approve=True, approved_by=blank)
    assert not (lib_home / "skills" / TITLE).exists()


def test_default_cli_output_and_manifest_unchanged(tmp_path):
    home = tmp_path / "home"
    proc = _ok(home)
    root = home.resolve()
    cand = "candidate-ff7efd9e7d13bbf1"
    assert proc.stdout.splitlines() == [
        "Self-evolving skills demo",
        f"Home: {root}",
        f"Candidate: {root}/candidates/{cand}/SKILL.md",
        "Source traces: train-run-1, train-run-2",
        'Rejected traces: {"validation-run-1": "held-out validation evidence", '
        '"failed-run-1": "trajectory did not complete successfully"}',
        "Held-out evaluation passed: True",
        "Promotion: stopped at the human approval gate (use --approve to continue)",
        f"Audit: {root}/evolution-audit.jsonl",
    ]
    assert json.loads((home / "run_manifest.json").read_text(encoding="utf-8")) == {
        "audit_path": f"{root}/evolution-audit.jsonl",
        "candidate_id": cand,
        "candidate_path": f"{root}/candidates/{cand}/SKILL.md",
        "evaluation_passed": True,
        "evaluation_path": f"{root}/candidates/{cand}/evaluation.json",
        "home": str(root),
        "promoted_skill_path": None,
        "promotion_requested": False,
        "rejected_traces": {
            "failed-run-1": "trajectory did not complete successfully",
            "validation-run-1": "held-out validation evidence",
        },
        "source_trace_ids": ["train-run-1", "train-run-2"],
        "validation_trace_id": "validation-run-1",
    }


def test_plain_approve_still_signs_as_demo_user(tmp_path):
    home = tmp_path / "home"
    _ok(home, "--approve")
    assert _manifest(home)["history"][0]["approved_by"] == "demo-user"
    promoted = [e for e in _audit(home) if e["action"] == "skill_promoted"]
    assert promoted[-1]["details"]["approved_by"] == "demo-user"


def test_adversarial_revised_seed_changes_training_and_held_out(tmp_path, evolution):
    def by_split(seed: str) -> dict[str, set]:
        store = evolution.EvolutionStore(tmp_path / seed)
        found: dict[str, set] = {}
        for item in evolution.seed_demo_trajectories(store, seed):
            if item.successful:
                found.setdefault(item.split, set()).add(item.signature)
        return found

    base, revised = by_split("baseline"), by_split("revised")
    assert revised["train"] == revised["validation"]
    assert revised["train"].isdisjoint(base["train"])
    assert revised["validation"].isdisjoint(base["validation"])
