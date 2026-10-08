"""复现测试：s15 的 glob 工具会列出工作区之外的文件名。

s15 的 "Tools (simplified)" 里，run_read() 对越界路径返回
"Error: path escapes workspace"，说明本章的文件工具只应访问工作区；
s02 / s04 的 run_glob() 也都用 is_relative_to(WORKDIR) 过滤结果。
但 s15 的 run_glob() 直接 glob(str(WORKDIR / pattern))，没有任何越界过滤，
模型传 "../*" 或绝对路径 pattern 就能拿到工作区外的文件名。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_s15(monkeypatch, tmp_path: Path):
    """使用离线 stub 导入教学模块，状态目录放进 tmp_path。"""

    stub_dir = ROOT / "tests" / "stubs"
    monkeypatch.syspath_prepend(str(stub_dir))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "state"))
    saved_anthropic = sys.modules.pop("anthropic", None)

    module_name = "s15_glob_traversal_test_module"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "s15_prompt_assembly" / "code.py")
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


def make_layout(monkeypatch, module, tmp_path: Path, workspace_name: str) -> Path:
    """工作区内放两个文件；工作区外放两个“机密”文件，并把 WORKDIR 指向工作区。"""

    root = tmp_path / "layout"
    workspace = root / workspace_name
    (workspace / "src").mkdir(parents=True)
    (workspace / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (workspace / "src" / "util.py").write_text("x = 1\n", encoding="utf-8")
    (root / "outside_secret.txt").write_text("secret", encoding="utf-8")
    (root / "outside_dir").mkdir()
    (root / "outside_dir" / "secret_key.pem").write_text("key", encoding="utf-8")
    monkeypatch.setattr(module, "WORKDIR", workspace.resolve())
    return workspace


OUTSIDE_NAMES = ("outside_secret.txt", "outside_dir", "secret_key.pem")


@pytest.mark.parametrize("pattern", ["../*", "../outside_dir/*", "{outside_abs}/*"])
def test_glob_does_not_list_files_outside_workspace(monkeypatch, tmp_path: Path, pattern: str) -> None:
    s15 = load_s15(monkeypatch, tmp_path)
    workspace = make_layout(monkeypatch, s15, tmp_path, "workspace")
    template = pattern
    pattern = template.format(outside_abs=(workspace.parent / "outside_dir").resolve())

    # 对照：同一章的 read_file 会拒绝越界路径。
    assert s15.run_read("../outside_secret.txt") == "Error: path escapes workspace"

    result = s15.run_glob(pattern)

    # 期望：越界结果被过滤（"../*" 命中的工作区根目录本身不算越界，与 s02 行为一致）；
    # 实际（bug）：列出了工作区外的文件名。
    leaked = [name for name in OUTSIDE_NAMES if name in result.splitlines()]
    assert not leaked, f"glob({template!r}) 泄露了工作区外的文件名 {leaked}，完整输出：{result!r}"


@pytest.mark.parametrize("workspace_name", ["workspace", "项目[2026]"])
def test_glob_inside_workspace_still_matches(monkeypatch, tmp_path: Path, workspace_name: str) -> None:
    """对照用例：修复后工作区内的正常 glob 不受影响，输出格式（只给文件名）保持不变。

    普通工作区在 main 上就应通过；带方括号的工作区在 main 上因工作区前缀未转义
    （与 #4 同一根因）而失败，按本卡设计修复后应通过。
    """

    s15 = load_s15(monkeypatch, tmp_path)
    make_layout(monkeypatch, s15, tmp_path, workspace_name)

    for pattern, expected in [("*.py", "main.py"), ("src/*.py", "util.py"), ("[ms]*", "main.py\nsrc")]:
        result = s15.run_glob(pattern)
        assert result == expected, (
            f"工作区 {workspace_name!r} 下 glob({pattern!r}) 应返回 {expected!r}，实际返回：{result!r}"
        )
