"""验证半行边界修复失败及仅写出换行后失败的恢复、重试契约。"""

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
    module_name = "self_evolving_skills_adversarial_r4_module"
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
    """通过真实轨迹、独立评测和审批准备版本。"""
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
    assert report.passed is True
    return store.promote(candidate, report, approved_by="reviewer")


@pytest.mark.parametrize(
    "failure_mode",
    ["none", "before-pad", "pad-only-truncate-denied"],
)
def test_torn_tail_padding_and_retry(
    evolution, tmp_path, monkeypatch, failure_mode
):
    """补边界失败必须恢复；成功重试必须留下完整独立事件。"""
    store = evolution.EvolutionStore(tmp_path / "library")
    v1 = _publish(evolution, store, "first", ["检查配置", "运行测试"])
    v2 = _publish(
        evolution,
        store,
        "second",
        ["检查配置", "运行测试", "记录结果"],
    )
    manifest_path = store.skills_dir / FAMILY / "manifest.json"
    manifest_before = manifest_path.read_bytes()
    manifest_data = json.loads(manifest_before)
    releases_before = (v1.read_bytes(), v2.read_bytes())
    audit_before = store.audit_path.read_bytes()
    assert audit_before.endswith(b"\n")
    assert manifest_data["active_version"] == 2

    # 模拟此前失败且无法截断留下的半行。
    torn = b'{"action": "skill_act'
    with store.audit_path.open("ab") as handle:
        handle.write(torn)
    audit_with_torn = audit_before + torn
    assert store.audit_path.read_bytes() == audit_with_torn

    original_open = Path.open
    original_truncate = evolution.os.truncate
    failure = OSError("补换行写入失败")
    attempts = []

    class FailingWriter:
        """保留真实文件行为，仅在合并写入处注入失败。"""

        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def write(self, payload):
            attempts.append(payload)
            assert payload.startswith(b"\n")
            assert payload.endswith(b"\n")
            event = json.loads(payload[1:])
            assert event["action"] == "skill_activated"
            if failure_mode == "pad-only-truncate-denied":
                assert self.handle.write(payload[:1]) == 1
                self.handle.flush()
            raise failure

    def guarded_open(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if path == store.audit_path and mode == "ab+":
            return FailingWriter(handle)
        return handle

    def guarded_truncate(path, length):
        if Path(path) == store.audit_path:
            # 清理被拒之前，manifest 必须已经恢复。
            assert manifest_path.read_bytes() == manifest_before
            raise PermissionError("审计截断被拒")
        return original_truncate(path, length)

    if failure_mode != "none":
        with monkeypatch.context() as patch:
            patch.setattr(Path, "open", guarded_open)
            if failure_mode == "pad-only-truncate-denied":
                patch.setattr(evolution.os, "truncate", guarded_truncate)
            with pytest.raises(OSError) as raised:
                store.set_active_version(
                    FAMILY, 1, approved_by="reviewer", reason="回退验证"
                )
            assert raised.value is failure

        assert len(attempts) == 1
        assert manifest_path.read_bytes() == manifest_before
        assert store.active_skill_path(FAMILY) == v2
        expected = audit_with_torn
        if failure_mode == "pad-only-truncate-denied":
            expected += b"\n"
        assert store.audit_path.read_bytes() == expected
        assert (v1.read_bytes(), v2.read_bytes()) == releases_before

    # 直接追加或解除故障后重试，都必须成功且有独立事件。
    assert store.set_active_version(
        FAMILY, 1, approved_by="reviewer", reason="回退验证"
    ) == v1
    assert store.active_skill_path(FAMILY) == v1
    assert (v1.read_bytes(), v2.read_bytes()) == releases_before

    expected_manifest = dict(manifest_data)
    expected_manifest["active_version"] = 1
    assert json.loads(manifest_path.read_bytes()) == expected_manifest

    audit_after = store.audit_path.read_bytes()
    assert audit_after.startswith(audit_before + torn + b"\n")
    suffix = audit_after[len(audit_before):]
    lines = suffix.splitlines()
    assert lines[0] == torn
    assert len(lines) == 2
    event = json.loads(lines[1])
    assert event["action"] == "skill_activated"
    assert event["details"] == {
        "title": FAMILY,
        "from_version": 2,
        "to_version": 1,
        "approved_by": "reviewer",
        "reason": "回退验证",
    }
    assert audit_after.endswith(b"\n")

    # 当前版本幂等不能再写入或补记事件。
    final_manifest = manifest_path.read_bytes()
    assert store.set_active_version(
        FAMILY, 1, approved_by="reviewer", reason="回退验证"
    ) == v1
    assert manifest_path.read_bytes() == final_manifest
    assert store.audit_path.read_bytes() == audit_after
