"""验证审计文件不可写时，清理失败不能阻止恢复生效指针。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FAMILY = "python-test-validation"


@pytest.fixture(scope="module")
def evolution():
    """使用离线加载方式，并恢复模块、搜索路径和环境变量。"""
    module_name = "self_evolving_skills_adversarial_r2_restore_module"
    stub_dir = str(ROOT / "tests" / "stubs")
    old_path = sys.path[:]
    old_environment = os.environ.copy()
    saved_anthropic = sys.modules.pop("anthropic", None)
    saved_module = sys.modules.get(module_name)
    sys.path.insert(0, stub_dir)
    os.environ["MODEL_ID"] = "offline-test-model"
    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            ROOT / "examples" / "self_evolving_skills" / "code.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = old_path
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        sys.modules.pop(module_name, None)
        if saved_module is not None:
            sys.modules[module_name] = saved_module
        os.environ.clear()
        os.environ.update(old_environment)


def _publish(evolution, store, prefix, intents):
    """通过真实轨迹、独立评测和审批生成发布版本。"""
    trajectories = []
    steps = [
        {"intent": intent, "tool": "bash", "ok": True}
        for intent in intents
    ]
    for suffix, split in (
        ("train-one", "train"),
        ("train-two", "train"),
        ("check", "validation"),
    ):
        path = store.write_trajectory(
            trace_id=f"{prefix}-{suffix}",
            task_family=FAMILY,
            task="验证项目测试结果",
            split=split,
            outcome="success",
            steps=steps,
        )
        trajectories.append(store.load_trajectory(path))
    pipeline = evolution.SkillEvolutionPipeline(store)
    candidate = pipeline.distill(
        trajectories[:2], task_family=FAMILY
    )
    report = pipeline.evaluate(
        candidate, validation=trajectories[2]
    )
    assert report.passed is True
    return store.promote(candidate, report, approved_by="reviewer")


def test_readonly_audit_cleanup_must_not_block_manifest_restore(
    evolution, tmp_path, monkeypatch
):
    """审计追加和截断都被拒绝时，仍须恢复可写的 manifest。"""
    store = evolution.EvolutionStore(tmp_path / "library")
    v1 = _publish(
        evolution, store, "first", ["检查配置", "运行测试"]
    )
    v2 = _publish(
        evolution,
        store,
        "second",
        ["检查配置", "运行测试", "记录结果"],
    )
    manifest_path = store.skills_dir / FAMILY / "manifest.json"
    manifest_before = manifest_path.read_bytes()
    audit_before = store.audit_path.read_bytes()
    releases_before = (v1.read_bytes(), v2.read_bytes())
    assert json.loads(manifest_before)["active_version"] == 2

    original_open = Path.open
    original_truncate = os.truncate

    def guarded_open(path, mode="r", *args, **kwargs):
        # 同一不可写条件同时拒绝追加和清理，不影响 manifest。
        if path == store.audit_path and any(
            flag in mode for flag in ("a", "w", "x", "+")
        ):
            raise PermissionError("审计文件不可写")
        return original_open(path, mode, *args, **kwargs)

    def guarded_truncate(path, length):
        if Path(path) == store.audit_path:
            raise PermissionError("审计文件不可截断")
        return original_truncate(path, length)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded_open)
        patch.setattr(evolution.os, "truncate", guarded_truncate)
        with pytest.raises(OSError):
            store.set_active_version(
                FAMILY,
                1,
                approved_by="reviewer",
                reason="回退验证",
            )

    assert manifest_path.read_bytes() == manifest_before
    assert store.audit_path.read_bytes() == audit_before
    assert (v1.read_bytes(), v2.read_bytes()) == releases_before
    assert store.active_skill_path(FAMILY) == v2

    assert store.set_active_version(
        FAMILY,
        1,
        approved_by="reviewer",
        reason="回退验证",
    ) == v1
    assert store.active_skill_path(FAMILY) == v1

    events = store.audit_path.read_bytes().splitlines()
    assert events[:-1] == audit_before.splitlines()
    event = json.loads(events[-1])
    assert event["action"] == "skill_activated"
    assert event["details"] == {
        "title": FAMILY,
        "from_version": 2,
        "to_version": 1,
        "approved_by": "reviewer",
        "reason": "回退验证",
    }
