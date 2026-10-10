"""核对重定向的引用信息、目标消费和紧贴命令分隔符的状态重置。

只调用权限决策，不执行命令、不调用模型、不联网。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s04():
    """用离线替身导入章节，并恢复模块缓存与模型配置。"""

    stub_dir = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    os.environ["MODEL_ID"] = "offline-test-model"
    module_name = "s04_rm_hard_deny_adversarial_r3_module"
    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            ROOT / "s04_permission_hooks" / "code.py",
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(stub_dir))
        sys.modules.pop(module_name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        if old_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = old_model


def decision_pair(s04, workspace: Path, command: str):
    """只构造请求并读取决策，不调用命令执行器。"""

    policy = s04.PermissionPolicy(s04.WorkspaceScope(workspace))
    decision = policy.decide(
        s04.ToolRequest("call_1", "bash", {"command": command})
    )
    return decision.action.value, decision.rule_id


@pytest.mark.parametrize(
    "command",
    [
        'rm -r ">" -f build',
        "rm -r '<' -f build",
        r"rm -r \> -f build",
    ],
)
def test_quoted_redirect_symbols_are_operands(s04, tmp_path, command):
    """攻击点：引用或转义的符号只是文件名，不得吞掉后面的强制选项。"""

    # 分写选项，避免旧正则掩盖切词错误。
    assert decision_pair(s04, tmp_path, command) == (
        "deny",
        "bash.hard_deny",
    )


@pytest.mark.parametrize(
    "command",
    [
        "rm -r >'>' -f build",
        "rm -r >out -f build",
    ],
)
def test_redirect_target_can_be_a_quoted_operator(s04, tmp_path, command):
    """攻击点：目标词即使值为运算符，也只能消费一次并保留后续选项。"""

    assert decision_pair(s04, tmp_path, command) == (
        "deny",
        "bash.hard_deny",
    )


@pytest.mark.parametrize("operator", ["<>", ">|", ">"])
def test_redirect_target_is_not_an_rm_option(s04, tmp_path, operator):
    """攻击点：合法重定向的目标名为 -f 时，不得把它计为 rm 强制选项。"""

    # shell 将 -f 用作重定向目标，rm 只收到 -r 和 notes.txt。
    command = f"rm -r {operator} -f notes.txt"
    assert decision_pair(s04, tmp_path, command) == (
        "ask",
        "bash.requires_approval",
    )


@pytest.mark.parametrize("gap", ["", " "], ids=["attached", "spaced"])
@pytest.mark.parametrize(
    "before,after,expected",
    [
        (
            "rm -- notes",
            "rm -r -f build",
            ("deny", "bash.hard_deny"),
        ),
        (
            "rm -r build",
            "rm -f notes",
            ("ask", "bash.requires_approval"),
        ),
    ],
)
def test_adjacent_separator_and_redirect_reset_state(
    s04, tmp_path, gap, before, after, expected
):
    """攻击点：紧贴的分隔符与重定向不得延续终止状态或合并两条命令的选项。"""

    command = f"{before};{gap}>out {after}"
    assert decision_pair(s04, tmp_path, command) == expected
