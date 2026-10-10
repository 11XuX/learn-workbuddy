"""紧贴命令名的重定向不能让直接 rm 递归强制删除降级为 ASK。

只调用 PermissionPolicy.decide()，不执行真实命令。
每个已确认的漏拒用例都配有加空格的对照，两种写法均须硬拒绝。
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
    module_name = "s04_rm_hard_deny_adversarial_r2_module"
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


def decide(s04, workspace: Path, command: str):
    """只构造请求并调用权限决策，不调用命令执行器。"""

    policy = s04.PermissionPolicy(s04.WorkspaceScope(workspace))
    return policy.decide(s04.ToolRequest("call_1", "bash", {"command": command}))


@pytest.mark.parametrize(
    "attached,spaced",
    [
        pytest.param(
            "rm</dev/null -r -f ./build",
            "rm </dev/null -r -f ./build",
            id="stdin",
        ),
        pytest.param(
            "/bin/rm</dev/null -r -f ./build",
            "/bin/rm </dev/null -r -f ./build",
            id="absolute-path-stdin",
        ),
        pytest.param(
            "rm>/dev/null -r -f ./build",
            "rm >/dev/null -r -f ./build",
            id="stdout",
        ),
        pytest.param(
            "cat | rm</dev/null -r -f ./build | cat",
            "cat | rm </dev/null -r -f ./build | cat",
            id="pipeline-stdin",
        ),
    ],
)
@pytest.mark.parametrize("layout", ["attached", "spaced-control"])
def test_command_adjacent_redirection_keeps_rm_hard_denied(
    s04,
    tmp_path: Path,
    attached: str,
    spaced: str,
    layout: str,
) -> None:
    """未引用的重定向必须保留 rm 命令身份及其后续选项。"""

    # 两种布局独立运行，漏拒用例失败不能阻止空格对照接受检查。
    # 分写 -r 和 -f，避免旧正则掩盖命令名切词错误。
    command = attached if layout == "attached" else spaced
    decision = decide(s04, tmp_path, command)

    assert (decision.action, decision.rule_id) == (
        s04.PermissionAction.DENY,
        "bash.hard_deny",
    ), f"{command!r} 是直接 rm 递归强制删除，重定向不能使其降级为 ASK"
