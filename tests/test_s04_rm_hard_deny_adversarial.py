"""对抗 s04 递归强制删除识别中的终止状态、切词和长选项匹配。"""

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
    module_name = "s04_rm_hard_deny_adversarial_module"
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
    """只调用权限决策，不执行命令。"""

    policy = s04.PermissionPolicy(s04.WorkspaceScope(workspace))
    return policy.decide(s04.ToolRequest("call_1", "bash", {"command": command}))


@pytest.mark.parametrize(
    "command",
    [
        "rm -- rm -r -f notes.txt",
        "rm -r -- rm -f notes.txt",
        "rm -f -- ./rm -r notes.txt",
        "rm -- -- rm -r -f notes.txt",
        "rm -- rm -- rm -r -f notes.txt",
        'rm -- "rm" "-r" "-f" notes.txt',
    ],
)
def test_option_terminator_cannot_be_reopened_by_rm_filename(
    s04, tmp_path: Path, command: str
) -> None:
    """攻击点：终止后的 rm 文件名不得重启选项解析或复用终止前的标志。"""

    # 刻意分写 -r 和 -f，避免旧正则的保守拒绝掩盖状态机错误。
    decision = decide(s04, tmp_path, command)

    assert (decision.action, decision.rule_id) == (
        s04.PermissionAction.ASK,
        "bash.requires_approval",
    ), f"{command!r} 中终止符之后全部是文件名"


@pytest.mark.parametrize(
    "command",
    [
        "echo -- rm -r -f notes.txt",
        "rm -- rm -r notes.txt; rm -r -f other.txt",
        "rm -- rm -r notes.txt && rm -r -f other.txt",
        "rm -- rm -r notes.txt\nrm -r -f other.txt",
        "rm -- rm -r notes.txt |& rm -r -f other.txt",
    ],
)
def test_option_terminator_is_scoped_to_one_rm_segment(
    s04, tmp_path: Path, command: str
) -> None:
    """攻击点：终止状态不能跨命令保留，rm 之前的终止符也不能屏蔽 rm。"""

    decision = decide(s04, tmp_path, command)

    assert (decision.action, decision.rule_id) == (
        s04.PermissionAction.DENY,
        "bash.hard_deny",
    )


@pytest.mark.parametrize(
    "command",
    [
        "rm -v --cursive --force notes.txt",
        "rm -v --recursive --orce notes.txt",
        "rm -v --recursivee --force notes.txt",
        "rm -v --recursive --forceful notes.txt",
        "rm -v --one-file-system --preserve-root=all --interactive=never -r notes.txt",
    ],
)
def test_non_prefix_long_options_do_not_count_as_flags(
    s04, tmp_path: Path, command: str
) -> None:
    """攻击点：内部子串、额外后缀和其他长选项不能充当递归或强制标志。"""

    # 首个选项不含 r/f，避免旧正则掩盖错误的长选项匹配。
    decision = decide(s04, tmp_path, command)

    assert (decision.action, decision.rule_id) == (
        s04.PermissionAction.ASK,
        "bash.requires_approval",
    )


@pytest.mark.parametrize(
    "command",
    [
        r"r\m -r '-''f' notes.txt",
        '"r"m -r -""f notes.txt',
        'rm -r "" -f notes.txt',
        r"rm -r -\f notes.txt",
    ],
)
def test_shell_word_fragments_and_empty_arguments_keep_flag_meaning(
    s04, tmp_path: Path, command: str
) -> None:
    """攻击点：相邻引号片段和词内转义必须正确拼词，空参数不能终止解析。"""

    decision = decide(s04, tmp_path, command)

    assert (decision.action, decision.rule_id) == (
        s04.PermissionAction.DENY,
        "bash.hard_deny",
    )


@pytest.mark.parametrize(
    "command",
    [
        "rm -- rm -rf notes.txt",
        "rm -r -- rm --force notes.txt",
        "rm --force notes.txt",
    ],
)
def test_legacy_regex_denials_survive_option_terminator(
    s04, tmp_path: Path, command: str
) -> None:
    """攻击点：新解析器不命中时仍须保留旧正则拒绝，包括旧规则的保守误判。"""

    decision = decide(s04, tmp_path, command)

    assert (decision.action, decision.rule_id) == (
        s04.PermissionAction.DENY,
        "bash.hard_deny",
    )
