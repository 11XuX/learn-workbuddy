"""复现测试：s02 的 glob 工具在工作区路径含通配元字符时找不到任何文件。

run_glob() 把工作区绝对路径和模型给的 pattern 直接拼成一个 glob 表达式，
工作区路径里的 "[" "]" 会被当成字符类解释。于是在 "项目[2026]" 这样的
目录里，glob("*.py") 永远返回 "(no matches)"，而同一工作区里的 read_file
却能正常读到文件。glob 的契约是“在工作区里按 pattern 找文件”，元字符
只应该来自 pattern，不应该来自工作区路径本身。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_s02(monkeypatch):
    """使用离线 Anthropic stub 导入独立的教学模块（与 test_s02_tool_dispatch 一致）。"""

    stub_dir = ROOT / "tests" / "stubs"
    monkeypatch.syspath_prepend(str(stub_dir))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    saved_anthropic = sys.modules.pop("anthropic", None)

    module_name = "s02_glob_metachar_test_module"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "s02_tool_dispatch" / "code.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
    return module


@pytest.mark.parametrize("workspace_name", ["项目[2026]", "release[v1]"])
def test_glob_finds_files_when_workspace_path_contains_brackets(
    monkeypatch,
    tmp_path: Path,
    workspace_name: str,
) -> None:
    s02 = load_s02(monkeypatch)
    workspace = tmp_path / workspace_name
    (workspace / "src").mkdir(parents=True)
    (workspace / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (workspace / "src" / "工具.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(s02, "WORKDIR", workspace.resolve())

    # 对照组：同一个工作区里 read_file 正常，说明路径边界本身没有问题。
    read = s02.TOOL_REGISTRY.dispatch(s02.ToolCall("call_1", "read_file", {"path": "main.py"}))
    assert read.ok and read.content == "print('hi')"

    top_level = s02.TOOL_REGISTRY.dispatch(s02.ToolCall("call_2", "glob", {"pattern": "*.py"}))
    recursive = s02.TOOL_REGISTRY.dispatch(s02.ToolCall("call_3", "glob", {"pattern": "**/*.py"}))

    # 期望：按 pattern 在工作区内匹配；实际（bug）：返回 "(no matches)"。
    assert top_level.ok
    assert top_level.content.splitlines() == ["main.py"], (
        f"工作区 {workspace_name!r} 下 glob('*.py') 应找到 main.py，实际返回：{top_level.content!r}"
    )
    assert recursive.content.splitlines() == ["main.py", "src/工具.py"], (
        f"工作区 {workspace_name!r} 下 glob('**/*.py') 应递归找到两个文件，实际返回：{recursive.content!r}"
    )
