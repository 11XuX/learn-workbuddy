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


CORE = "classDef core fill:#ede9fe"
EXTRA = [  # 追加到 BASE 末尾的行
    f"{CORE} extra",  # 行尾内容
    f"{CORE},stroke:#ffff;",  # 非法属性值（#rgb 现为合法，改用 4 位）
    "class B core x",
    'subgraph loop["Agent Loop"] B\n    end',
    "classDef  core fill:#ede9fe",  # 关键字间多余空格
    "ClassDef core fill:#ede9fe",  # 大小写
    'Subgraph loop["Agent Loop"]\n    B\n    end',
    'subgraph loop["Agent Loop"]\n    B\n    END',
    'subgraph loop["Agent Loop"]\n    End\n    end',  # 关键字当 id
    'subgraph loop["Agent Loop"]\n    end',  # 空 subgraph
    'subgraph loop["Agent Loop"]\n    B',  # 未闭合 subgraph
    'subgraph loop["Agent Loop"]\n    subgraph tools["Tool Execution"]\n    C\n    end\n    end',  # 嵌套
    'A["Prompt\n    "]',  # 引号未闭合
    "Flowchart LR",
    f"{CORE}\n    class B core\n    class B core",  # 重复 class
    *[f"classDef core stroke:#123456,{p}" for p in ("color:2px", "stroke-width:#fff", "stroke-width:0px", "stroke-width:-1px",
                                                  "stroke-width:1.5px", "fill:red", "fill:#ggg", "stroke:#111", "shape:x", "fill:")],
]
L = lines(BASE)
PHYSICAL = [  # 原行只允许改缩进
    L[:2] + [L[2] + "; " + L[3]] + L[4:],  # 两行合并
    L[:1] + ['    A["Prompt"]', '    A --> B["Loop"]'] + L[2:],  # 一行拆成多行
    L[:2] + [L[3], L[2]] + L[4:],  # 重排
    L[:1] + [L[1] + ";"] + L[2:],  # 行尾加分号
    L[:1] + [L[1].replace("-->", "-->  ")] + L[2:],  # 行内多余空格
    L + [L[4]],  # 重复带一个原行
]


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
    ]
    + [BASE + "\n    " + e for e in EXTRA]
    + ["\n".join(n) for n in PHYSICAL],
)
def test_semantic_or_syntax_changes_fail(new):
    with pytest.raises(mod.RestyleError):
        mod.compare(lines(BASE), lines(new))


def test_extract_blocks_rejects_unclosed_fence():
    assert mod.extract_blocks("x\n```mermaid\nflowchart LR\n```\n") == [["flowchart LR"]]
    with pytest.raises(mod.RestyleError):
        mod.extract_blocks("```mermaid\nflowchart LR\n")


def test_semicolons_split_statements_outside_quotes_only():
    base = BASE + '\n    E -->|"a;b"| F["x; y"]'
    assert ("F", "x; y") in mod.parse(lines(base))["nodes"]
    assert mod.compare(lines(base), lines(base + f"\n    {CORE}; class B core")) == []
    with pytest.raises(mod.RestyleError):
        mod.compare(lines(base), lines(base + f"\n    {CORE}; A --> E"))  # 分号夹带边


def test_indentation_and_comments_are_presentation():
    assert mod.compare(L, ["\t" + ln for ln in L]) == []  # tab 缩进
    new = "\n".join("  " + ln for ln in lines(BASE)) + "\n    %% 样式\n    " + CORE + "\n    class B core"
    assert mod.compare(lines(BASE), lines(new)) == []
    with pytest.raises(mod.RestyleError):
        mod.compare(lines(BASE), lines(BASE + '\n    %%{init: {"theme": "dark"}}%%'))
