"""验证审计半行清理失败后，重试成功必须产生独立可解析的切换事件。"""

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
    """离线加载示例，恢复模块、搜索路径及环境变量。"""
    module_name = "self_evolving_skills_adversarial_r3_torn_retry_module"
    old_path = sys.path[:]
    old_environment = os.environ.copy()
    saved_anthropic = sys.modules.pop("anthropic", None)
    saved_module = sys.modules.get(module_name)
    sys.path.insert(0, str(ROOT / "tests" / "stubs"))
    os.environ["MODEL_ID"] = "offline-test-model"
    source = ROOT / "examples" / "self_evolving_skills" / "code.py"
    if not source.is_file():
        source = Path(__file__).with_name("code.py")
    try:
        spec = importlib.util.spec_from_file_location(module_name, source)
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
    """用真实轨迹、评测及审批准备版本。"""
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
    candidate = pipeline.distill(trajectories[:2], task_family=FAMILY)
    report = pipeline.evaluate(candidate, validation=trajectories[2])
    assert report.passed
    return store.promote(candidate, report, approved_by="reviewer")


def test_retry_after_torn_audit_and_failed_truncate_records_complete_event(
    evolution, tmp_path, monkeypatch
):
    """允许失败留下半行，但随后成功的切换必须有完整独立审计。"""
    store = evolution.EvolutionStore(tmp_path / "library")
    v1 = _publish(evolution, store, "first", ["检查配置", "运行测试"])
    v2 = _publish(
        evolution, store, "second", ["检查配置", "运行测试", "记录结果"]
    )
    manifest_path = store.skills_dir / FAMILY / "manifest.json"
    manifest_before = manifest_path.read_bytes()
    audit_before = store.audit_path.read_bytes()
    releases_before = (v1.read_bytes(), v2.read_bytes())
    original_truncate = evolution.os.truncate
    failure = OSError("审计追加失败")

    def torn_append(action, details):
        with store.audit_path.open("ab") as handle:
            handle.write(b'{"action": "skill_act')
        raise failure

    def failed_truncate(path, length):
        if Path(path) == store.audit_path:
            raise PermissionError("审计截断失败")
        return original_truncate(path, length)

    with monkeypatch.context() as patch:
        patch.setattr(store, "append_audit", torn_append)
        patch.setattr(evolution.os, "truncate", failed_truncate)
        with pytest.raises(OSError) as raised:
            store.set_active_version(
                FAMILY, 1, approved_by="reviewer", reason="回退验证"
            )
        assert raised.value is failure

    assert manifest_path.read_bytes() == manifest_before
    assert store.active_skill_path(FAMILY) == v2
    assert store.audit_path.read_bytes().startswith(audit_before)

    assert store.set_active_version(
        FAMILY, 1, approved_by="reviewer", reason="回退验证"
    ) == v1
    assert store.active_skill_path(FAMILY) == v1
    assert (v1.read_bytes(), v2.read_bytes()) == releases_before

    events = []
    for line in store.audit_path.read_bytes().splitlines():
        try:
            events.append(json.loads(line))
        except (ValueError, UnicodeDecodeError):
            pass
    activations = [
        event for event in events
        if event.get("action") == "skill_activated"
    ]
    assert len(activations) == 1
    assert activations[0]["details"] == {
        "title": FAMILY,
        "from_version": 2,
        "to_version": 1,
        "approved_by": "reviewer",
        "reason": "回退验证",
    }
