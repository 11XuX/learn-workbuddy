"""第二轮独立对抗：缺失参数分发与审批后的权限一致性。"""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "s16_skill_create_audit_adversarial_r2_module"


def _load_s16():
    stub_dir = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    os.environ["MODEL_ID"] = "offline-test-model"
    try:
        spec = importlib.util.spec_from_file_location(
            MODULE_NAME, ROOT / "s16_skills_system" / "code.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[MODULE_NAME] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(stub_dir))
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        if old_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = old_model


@pytest.fixture(scope="module")
def s16_module():
    try:
        yield _load_s16()
    finally:
        sys.modules.pop(MODULE_NAME, None)


@pytest.fixture
def s16(s16_module):
    m = s16_module
    index, loaded = list(m.skill_index), list(m.loaded_skills)
    flags = [skill.loaded for skill in index]
    pending, seeds = dict(m.pending_skills), dict(m.SEED_SKILLS)
    try:
        yield m
    finally:
        m.skill_index[:], m.loaded_skills[:] = index, loaded
        for skill, flag in zip(index, flags):
            skill.loaded = flag
        m.pending_skills.clear()
        m.pending_skills.update(pending)
        m.SEED_SKILLS.clear()
        m.SEED_SKILLS.update(seeds)


def _args():
    return {
        "title": "r2-workflow",
        "summary": "整理流程",
        "read_when": ["整理流程"],
        "content": "# 步骤\n运行 git status",
    }


def _snapshot(m):
    return deepcopy((
        m.skill_index, m.pending_skills,
        m.loaded_skills, m.SEED_SKILLS,
    ))


@pytest.mark.parametrize(
    "missing", ["title", "summary", "read_when", "content"]
)
def test_missing_required_field_never_reaches_handler(
    s16, monkeypatch, missing
):
    """攻击点：缺失必填字段不得进入处理器或中断真实工具分发。"""
    s16.loaded_skills.clear()
    before = _snapshot(s16)
    payload = _args()
    del payload[missing]
    calls = []

    def spy(**kwargs):
        calls.append(kwargs)
        return "处理器不应被调用"

    monkeypatch.setitem(s16.TOOL_HANDLERS, "SkillCreate", spy)
    block = SimpleNamespace(
        type="tool_use", id="r2-missing-call",
        name="SkillCreate", input=payload,
    )
    responses = iter([
        SimpleNamespace(stop_reason="tool_use", content=[block]),
        SimpleNamespace(stop_reason="end_turn", content=[]),
    ])
    monkeypatch.setattr(
        s16, "client",
        SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kwargs: next(responses)
        )),
    )

    history = []
    s16.agent_loop(history)

    assert calls == []
    result = history[1]["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "r2-missing-call"
    assert result["content"] == (
        "Error: invalid arguments for SkillCreate: "
        f"unknown=[] missing=['{missing}']"
    )
    assert _snapshot(s16) == before


def test_permissions_survive_reaudit_and_index_rebuild(s16, monkeypatch):
    """攻击点：规范化权限不得在待审批、重审或重建索引时丢失。"""
    original_audit = s16.audit_skill
    audited = []

    def record_audit(content, *args, **kwargs):
        audited.append(kwargs.get("requested_permissions"))
        return original_audit(content, *args, **kwargs)

    monkeypatch.setattr(s16, "audit_skill", record_audit)
    payload = _args()
    payload["permissions"] = {
        "tools": ["read_file", "read_file"],
        "network": True,
        "paths": {
            "read": ["docs/./**"],
            "write": ["notes/./**"],
        },
    }

    assert "待用户审批" in s16.TOOL_HANDLERS["SkillCreate"](**payload)
    pending = s16.pending_skills["r2-workflow"]
    expected = s16.SkillPermissions(
        tools=("read_file",),
        network=True,
        read_paths=("docs/**",),
        write_paths=("notes/**",),
    )
    assert pending.permissions == expected
    assert len(audited) == 1
    assert audited[0] is pending.permissions
    assert "r2-workflow" not in s16.SEED_SKILLS

    assert s16.approve_skill("r2-workflow") == (
        "技能 'r2-workflow' 已经用户批准并创建。"
    )
    assert len(audited) == 2
    assert audited[1] is pending.permissions
    assert "r2-workflow" not in s16.pending_skills

    indexed = [
        skill for skill in s16.skill_index
        if skill.title == "r2-workflow"
    ]
    assert len(indexed) == 1
    assert indexed[0].permissions is pending.permissions
    assert indexed[0].permissions == expected

    # 重建索引独立读取保存的文本，不能靠已有对象掩盖权限遗漏。
    rebuilt = [
        skill for skill in s16.build_skill_index()
        if skill.title == "r2-workflow"
    ]
    assert len(rebuilt) == 1
    assert rebuilt[0].permissions == expected
    assert rebuilt[0].summary == "整理流程"
    assert rebuilt[0].read_when == ["整理流程"]
    assert rebuilt[0].content == "# 步骤\n运行 git status"
    assert rebuilt[0].agent_created is True
