"""s04 硬拒绝规则对「递归强制删除」的识别不应依赖选项的书写方式。

s04 README 和 docs/security-boundaries.md 都写明：`sudo`、递归强制删除等命令
由 `bash.hard_deny` 不可覆盖地拒绝，且硬拒绝必须先于普通 bash 审批，
否则会被降级成「询问后可执行」。

当前实现只检查 `rm` 后面的第一个选项 token 是否同时含 r 和 f，
因此 `rm -v -rf /`、`rm -r -f /`、`rm --recursive --force ./build`
这类同义写法会落到 `bash.requires_approval`（ASK）。
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
    """用离线 anthropic stub 导入 s04 章节代码。"""

    stub_dir = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    os.environ["MODEL_ID"] = "offline-test-model"
    module_name = "s04_rm_hard_deny_flags_test_module"
    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            ROOT / "s04_permission_hooks" / "code.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
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


def decide(s04, workspace: Path, command: str):
    policy = s04.PermissionPolicy(s04.WorkspaceScope(workspace))
    return policy.decide(s04.ToolRequest("call_1", "bash", {"command": command}))


@pytest.mark.parametrize(
    "command",
    [
        "rm -v -rf /",
        "rm -i -rf ./build",
        "rm -r -f /",
        "rm -R -f ./build",
        "rm --recursive --force ./build",
        "rm -r ./build -f",
        "ls && rm -v -rf /",
    ],
)
def test_recursive_force_rm_is_hard_denied_regardless_of_flag_layout(
    s04, tmp_path: Path, command: str
) -> None:
    decision = decide(s04, tmp_path, command)

    assert (decision.action, decision.rule_id) == (
        s04.PermissionAction.DENY,
        "bash.hard_deny",
    ), f"{command!r} 是递归强制删除，应被硬拒绝"


@pytest.mark.parametrize(
    "command",
    ["rm -rf /", "rm -fr ./build", "rm -RF ./build"],
)
def test_combined_flag_forms_stay_hard_denied(s04, tmp_path: Path, command: str) -> None:
    # 对照：现有写法在修复前后都必须保持硬拒绝。
    decision = decide(s04, tmp_path, command)

    assert decision.action is s04.PermissionAction.DENY
    assert decision.rule_id == "bash.hard_deny"


@pytest.mark.parametrize(
    "command",
    [
        "rm -f notes.txt",
        "rm -r build",
        "rm -v notes.txt",
        "rm -- -rf",
        "rm -f a.txt; rm -r build",
    ],
)
def test_non_recursive_force_rm_still_asks(s04, tmp_path: Path, command: str) -> None:
    # 对照：只递归、只强制、`--` 之后的文件名、分属两条命令的选项，都不是递归强制删除，
    # 仍走普通 bash 审批而不是硬拒绝。
    decision = decide(s04, tmp_path, command)

    assert decision.action is s04.PermissionAction.ASK
    assert decision.rule_id == "bash.requires_approval"
