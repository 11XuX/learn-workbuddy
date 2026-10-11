"""self_evolving_skills 生效版本切换与回滚的离线契约。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FAMILY = "python-test-validation"
STEPS_V1 = [
    {"intent": "Inspect the project test configuration.", "tool": "read_file", "ok": True},
    {"intent": "Run the smallest relevant test target.", "tool": "bash", "ok": True},
]
STEPS_V2 = STEPS_V1 + [
    {"intent": "Record the verification result for review.", "tool": "bash", "ok": True},
]


@pytest.fixture(scope="module")
def evolution():
    module_name = "self_evolving_skills_rollback_test_module"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "examples" / "self_evolving_skills" / "code.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(module_name, None)


def _release(evolution, store, prefix: str, steps):
    # 每组轨迹都现写现读，source_digests 由真实文件动态算出
    ids = [f"{prefix}-train-1", f"{prefix}-train-2", f"{prefix}-check"]
    for trace_id, split in zip(ids, ("train", "train", "validation")):
        store.write_trajectory(
            trace_id=trace_id,
            task_family=FAMILY,
            task="Validate a Python change with focused tests.",
            split=split,
            outcome="success",
            steps=steps,
        )
    loaded = [store.load_trajectory(store.traces_dir / f"{item}.jsonl") for item in ids]
    pipeline = evolution.SkillEvolutionPipeline(store)
    candidate = pipeline.distill(loaded[:2], task_family=FAMILY)
    report = pipeline.evaluate(candidate, validation=loaded[2])
    return candidate, report, store.promote(candidate, report, approved_by="alice")


@pytest.fixture
def library(evolution, tmp_path: Path):
    store = evolution.EvolutionStore(tmp_path / "evolution")
    first = _release(evolution, store, "alpha", STEPS_V1)
    second = _release(evolution, store, "beta", STEPS_V2)
    return store, first, second


def _manifest(store) -> dict:
    return json.loads((store.skills_dir / FAMILY / "manifest.json").read_text(encoding="utf-8"))


def _snapshot(store) -> tuple[bytes, bytes]:
    return (store.skills_dir / FAMILY / "manifest.json").read_bytes(), store.audit_path.read_bytes()


def _rollback(store, version: int = 1):
    return store.set_active_version(FAMILY, version, approved_by="bob", reason="v2 regression")


def test_rollback_moves_pointer_and_audits_only(evolution, library) -> None:
    store, (candidate, _, v1), (_, _, v2) = library
    before = {path: path.read_bytes() for path in (v1, v2)}
    keys = set(_manifest(store))
    assert store.active_skill_path(FAMILY) == v2
    assert store.active_skill_path("never-published") is None
    # 还原的 candidate 必须与原对象相等（tuple 与 StepEvidence 类型都要对）
    restored = store._load_candidate(candidate.candidate_id)
    assert restored == candidate and isinstance(restored.source_digests, tuple)
    assert all(isinstance(step, evolution.StepEvidence) for step in restored.steps)

    assert _rollback(store) == v1
    manifest = _manifest(store)
    assert manifest["active_version"] == 1
    assert len(manifest["history"]) == 2
    assert set(manifest) == keys
    assert store.active_skill_path(FAMILY) == v1
    assert {path: path.read_bytes() for path in (v1, v2)} == before
    last = json.loads(store.audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["action"] == "skill_activated"
    assert last["details"] == {
        "title": FAMILY,
        "from_version": 2,
        "to_version": 1,
        "approved_by": "bob",
        "reason": "v2 regression",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"approved_by": ""}, {"reason": "   "}, {"approved_by": None}, {"reason": 7},
        {"approved_by": " bob"}, {"version": 0}, {"version": True}, {"version": "1"},
        {"version": 3}, {"title": "unknown-skill"}, {"title": f" {FAMILY}"},
        {"title": FAMILY.upper()}, {"title": None},
    ],
)
def test_invalid_arguments_are_rejected_without_writes(evolution, library, overrides) -> None:
    store = library[0]
    before = _snapshot(store)
    arguments = {"title": FAMILY, "version": 1, "approved_by": "bob", "reason": "rollback"}
    arguments.update(overrides)
    title, version = arguments.pop("title"), arguments.pop("version")

    with pytest.raises(evolution.EvolutionError):
        store.set_active_version(title, version, **arguments)
    assert _snapshot(store) == before


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


TAMPERS = {
    "release_deleted": lambda release, evidence: release.unlink(),
    "release_symlink_loop": lambda release, evidence: (release.unlink(), release.symlink_to(release)),
    "status_edited": lambda release, evidence: _edit(release, "status: approved", "status: candidate"),
    "version_edited": lambda release, evidence: _edit(release, "version: 1", "version: 2"),
    "procedure_only": lambda release, evidence: _edit(release, "1. Inspect", "1. Skip"),
    "candidate_deleted": lambda release, evidence: evidence.unlink(),
    "candidate_corrupt": lambda release, evidence: evidence.write_text("{", encoding="utf-8"),
    "candidate_malformed": lambda release, evidence: _edit(evidence, '"read_when": [', '"x": ['),
}


@pytest.mark.parametrize("tamper", sorted(TAMPERS))
def test_tampered_or_unverifiable_release_cannot_be_activated(
    evolution, library, tamper: str
) -> None:
    store, (candidate, _, v1), _ = library
    TAMPERS[tamper](v1, store.candidate_dir(candidate.candidate_id) / "candidate.json")
    before = _snapshot(store)

    with pytest.raises(evolution.EvolutionError):
        _rollback(store)
    assert _snapshot(store) == before


def test_activating_current_version_is_a_no_op(library) -> None:
    store, _, (_, _, v2) = library
    before = _snapshot(store)
    assert _rollback(store, 2) == v2
    assert _snapshot(store) == before


def test_reapproval_keeps_rollback_then_forward_and_new_release(evolution, library) -> None:
    store, _, (candidate, report, v2) = library
    _rollback(store)

    assert store.promote(candidate, report, approved_by="alice") == v2
    assert _manifest(store)["active_version"] == 1
    assert _rollback(store, 2) == v2
    assert store.active_skill_path(FAMILY) == v2

    _, _, v3 = _release(evolution, store, "gamma", STEPS_V2[::-1])
    assert v3 == store.skills_dir / FAMILY / "v3" / "SKILL.md"
    assert _manifest(store)["active_version"] == 3


def test_relocated_store_resolves_paths_from_new_root(evolution, library, tmp_path: Path) -> None:
    old_root = library[0].root
    new_root = tmp_path / "relocated"
    shutil.copytree(old_root, new_root)
    shutil.rmtree(old_root)
    store = evolution.EvolutionStore(new_root)

    path = _rollback(store)
    assert path == store.root / "skills" / FAMILY / "v1" / "SKILL.md"
    assert store.active_skill_path(FAMILY) == path
    assert all(Path(item["path"]).is_relative_to(old_root) for item in _manifest(store)["history"])


@pytest.mark.parametrize("damage", ["pointer_unknown", "pointer_text", "deleted", "body_edited"])
def test_active_skill_path_rejects_broken_state(evolution, library, damage: str) -> None:
    store, _, (_, _, v2) = library
    manifest_path = store.skills_dir / FAMILY / "manifest.json"
    if damage.startswith("pointer"):
        manifest = _manifest(store)
        manifest["active_version"] = 9 if damage == "pointer_unknown" else "2"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif damage == "deleted":
        v2.unlink()
    else:
        _edit(v2, "2. Run", "2. Skip")

    with pytest.raises(evolution.EvolutionError):
        store.active_skill_path(FAMILY)


def test_partial_audit_write_is_rolled_back_then_retry_succeeds(library, monkeypatch) -> None:
    store, before = library[0], _snapshot(library[0])

    def torn_write(action, details):
        store.audit_path.open("a", encoding="utf-8").write('{"action": "skill_act')
        raise OSError("disk full")

    monkeypatch.setattr(store, "append_audit", torn_write)
    with pytest.raises(OSError):
        _rollback(store)
    monkeypatch.undo()
    assert _snapshot(store) == before
    _rollback(store)
    assert store.audit_path.read_bytes().count(b"skill_activated") == 1
