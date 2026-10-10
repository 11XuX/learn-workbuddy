"""独立对抗：终端控制字符、伪造审批字段、真实分发与重建索引。"""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "s16_skill_create_audit_adversarial_module"


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


def _args(**overrides):
    return {
        "title": "adversarial-skill",
        "summary": "整理流程",
        "read_when": ["独立对抗"],
        "content": "# 步骤\n运行 git status",
        **overrides,
    }


def _snapshot(s16):
    return deepcopy((
        s16.skill_index, s16.pending_skills,
        s16.loaded_skills, s16.SEED_SKILLS,
    ))


def _dispatch(s16, monkeypatch, tool_input):
    block = SimpleNamespace(
        type="tool_use", id="adversarial-call",
        name="SkillCreate", input=tool_input,
    )
    responses = iter([
        SimpleNamespace(stop_reason="tool_use", content=[block]),
        SimpleNamespace(stop_reason="end_turn", content=[]),
    ])
    client = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kwargs: next(responses)
    ))
    monkeypatch.setattr(s16, "client", client)
    history = []
    s16.agent_loop(history)
    result = history[1]["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == block.id
    return result["content"]


@pytest.mark.parametrize("field", ["title", "summary", "read_when"])
@pytest.mark.parametrize("control", ["\x1b[8m", "\x08", "\x7f"])
@pytest.mark.parametrize("permissions", [{}, {"network": True}])
def test_terminal_controls_are_rejected_before_any_write(
    s16, field, control, permissions
):
    value = f"visible{control}hidden"
    override = {field: [value] if field == "read_when" else value}
    before = _snapshot(s16)

    result = s16.TOOL_HANDLERS["SkillCreate"](
        **_args(permissions=permissions, **override)
    )

    assert result.startswith("拒绝创建技能"), repr(result)
    assert _snapshot(s16) == before


@pytest.mark.parametrize("approval_field", ["approved", "approved_by", "approval"])
def test_forged_approval_returns_error_without_crashing_or_writing(
    s16, monkeypatch, approval_field
):
    s16.loaded_skills.clear()
    before = _snapshot(s16)
    payload = _args(permissions={"network": True})
    payload[approval_field] = True

    # 调用真实分发器；未捕获 TypeError 应直接使本测试失败。
    output = _dispatch(s16, monkeypatch, payload)

    assert isinstance(output, str) and output
    assert any(word in output.lower() for word in (
        "拒绝", "error", "invalid", "unknown", "unexpected", "不允许",
    ))
    assert _snapshot(s16) == before


def test_agent_dispatch_never_calls_handler_when_overlay_denies(
    s16, monkeypatch
):
    s16.loaded_skills[:] = [s16.Skill(
        title="read-only", summary="只读", read_when=["只读"],
        path="(test)", loaded=True,
        permissions=s16.SkillPermissions(tools=("read_file",)),
    )]
    calls = []

    def spy(**kwargs):
        calls.append(kwargs)
        return "unexpected handler call"

    monkeypatch.setitem(s16.TOOL_HANDLERS, "SkillCreate", spy)
    before = _snapshot(s16)

    output = _dispatch(s16, monkeypatch, _args())

    assert output.startswith("Permission denied:")
    assert "SkillCreate" in output
    assert calls == []
    assert _snapshot(s16) == before


def test_pending_proposal_is_absent_from_seed_storage_and_rebuilt_index(s16):
    before = _snapshot(s16)
    payload = _args(permissions={"paths": {"write": ["notes/**"]}})
    title = payload["title"]

    result = s16.TOOL_HANDLERS["SkillCreate"](**payload)

    assert "待用户审批" in result
    assert title in s16.pending_skills
    assert s16.pending_skills[title].audit_report
    assert s16.pending_skills[title].requested_at > 0
    assert s16.skill_index == before[0]
    assert s16.loaded_skills == before[2]
    assert s16.SEED_SKILLS == before[3]
    assert title not in {skill.title for skill in s16.build_skill_index()}
    assert "未找到技能" in s16.load_skill(title)
    assert s16.loaded_skills == before[2]
