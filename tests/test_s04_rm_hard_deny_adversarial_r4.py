"""核对重定向运算符内部续行不会改变递归强制删除的权限判断。"""

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
    module_name = "s04_rm_hard_deny_adversarial_r4_module"
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


@pytest.mark.parametrize("redirect", [">&1", "<&0", "&>out", ">|out"])
@pytest.mark.parametrize(
    "continued",
    [False, True],
    ids=["plain-control", "continued"],
)
def test_redirect_operator_continuation_keeps_rm_hard_denied(
    s04, redirect, continued
):
    """攻击点：运算符内部续行不得把重定向误拆成命令分隔符。"""

    if continued:
        redirect = redirect[:1] + "\\\n" + redirect[1:]

    # 分写选项，避免旧正则掩盖切词和状态重置错误。
    # 消除有效续行后，四种布局都只是同一条命令内的重定向。
    command = f"rm -r {redirect} -f build"
    policy = s04.PermissionPolicy(s04.WorkspaceScope(ROOT))
    decision = policy.decide(
        s04.ToolRequest("call_1", "bash", {"command": command})
    )

    assert (decision.action.value, decision.rule_id) == (
        "deny",
        "bash.hard_deny",
    ), f"{command!r} 的递归和强制选项仍属于同一条 rm 命令"
