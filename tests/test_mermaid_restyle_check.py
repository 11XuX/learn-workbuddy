"""Unit tests for scripts/check_mermaid_restyle.py using in-test samples only."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_mermaid_restyle", Path(__file__).resolve().parents[1] / "scripts" / "check_mermaid_restyle.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

BASE = """flowchart LR
    A["Prompt"] --> B["Loop"]
    B -->|"tool"| C["Tool"]
    C -. "error" .-> D["Result"]
    B --- E["Spec"]"""
STYLE = """
    subgraph loop["Agent Loop"]
        B
        C
    end
    classDef core fill:#ede9fe,stroke:#6d28d9,color:#2e1065
    class B,C core
    class loop core"""


def lines(text):
    return text.splitlines()


def test_style_only_lines_pass():
    assert mod.compare(lines(BASE), lines(BASE + STYLE)) == [("loop", "Agent Loop")]
    assert mod.compare(lines(BASE), lines(BASE.replace("    ", "  "))) == []


@pytest.mark.parametrize(
    "new",
    [
        BASE + '\n    D --> F["Extra"]',  # 增加节点
        BASE.replace('\n    B --- E["Spec"]', ""),  # 删除节点
        BASE.replace('"Loop"', '"Agent"'),  # 改节点文字
        BASE.replace('"tool"', '"call"'),  # 改边文字
        BASE.replace('B --- E', "B --> E"),  # 改线型
        BASE.replace('C -. "error" .-> D', 'C -->|"error"| D'),  # 虚线改实线
        BASE.replace("flowchart LR", "flowchart TD"),  # 改方向
        BASE + STYLE.replace('loop["Agent Loop"]', 'loop["Core Engine"]'),  # 词表外标题
        BASE + STYLE.replace('loop["Agent Loop"]', 'brain["Agent Loop"]'),  # 词表外 id
        BASE + '\n    A --> B',  # 重复边
        BASE + '\n    A["Prompt"] --> D',  # 重复定义节点文字
        BASE + '\n    A ==> D',  # 无法识别的语法
        BASE + '\n    click A "https://example.com"',  # 禁止语法
        BASE + '\n    %%{init: {"theme": "dark"}}%%',  # 主题覆盖
        BASE + "\n    B",  # subgraph 外的成员行
        BASE + '\n    subgraph loop["Agent Loop"]\n    Z\n    end',  # 未知成员
        BASE + STYLE.replace("class B,C core", "class B,C hot"),  # 未定义 classDef
    ],
)
def test_semantic_or_syntax_changes_fail(new):
    with pytest.raises(mod.RestyleError):
        mod.compare(lines(BASE), lines(new))


def test_extract_blocks_rejects_unclosed_fence():
    assert mod.extract_blocks("x\n```mermaid\nflowchart LR\n```\n") == [["flowchart LR"]]
    with pytest.raises(mod.RestyleError):
        mod.extract_blocks("```mermaid\nflowchart LR\n")
