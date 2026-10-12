import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_mermaid_restyle", ROOT / "scripts" / "check_mermaid_restyle.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)  # type: ignore[union-attr]
B = ('# T\n```mermaid\nflowchart LR\n    A["in"] --> B["loop"]\n    B -. "x" .-> output{"out?"}\n'
     '```\ntext\n```mermaid\nflowchart LR\n    Z["other"] --> A\n```\n')
TAIL = '    B -. "x" .-> output{"out?"}\n'
OK = ['    subgraph loop["主循环"]\n        B\n    end\n', "\tsubgraph Input\n  A\n  end\n",
      "\n    classDef io fill:#e0f2fe,stroke:#0369a1,stroke-width:1.5px\n    class A,output io\n"]
BAD = ['    classDef io fill:#fff;A-->Z\n', '    classDef io fill:#fff %%{init}%%\n',
       "    %%{init: {'theme':'dark'}}%%\n", '    class A io \n', '    class A io\t\n',
       '    style A fill:#fff\n', '    linkStyle 0 stroke:#000\n', '    direction TB\n',
       '    A --> output\n', '    D["new"]\n', '    class Z io\n', '    Z\n', '    class Q io\n',
       '    subgraph 工具\n    end\n', '    subgraph loop["Tools"]\n    end\n',
       '    subgraph x["Input"]\n    end\n', '    subgraph Input\n', '    end\n',
       '    %% B --> output\n', '    subgraph output["Output"]\n    end\n',
       '    class A io;B-->output\n', '    subgraph input["Input"];A-->output\n    end\n',
       '    A["in"] --> B["loop"]\n', '    subgraph loop["Agent Loop"]\n    end\n  end\n']


@pytest.mark.parametrize("extra", OK)
def test_style_lines_and_indent_pass(extra: str) -> None:
    new = B.replace(TAIL, TAIL + extra).replace("    B -. ", "\t  B -. ")
    assert mod.check(B, new) == extra.count("\n")
    assert mod.check(B.replace("\n", "\r\n"), new.replace("\n", "\r\n")) > 0
    mixed = B.replace("text\n", "text\r\n").replace("# T\n", "# T\r\n")
    assert mod.check(mixed, mixed.replace(TAIL, TAIL + extra)) > 0


@pytest.mark.parametrize("extra", BAD)
def test_smuggled_or_invalid_lines_fail(extra: str) -> None:
    with pytest.raises(mod.RestyleError):
        mod.check(B, B.replace(TAIL, TAIL + extra))


@pytest.mark.parametrize("old,new", [
    ('loop"]\n', 'loop"] \n'), ('loop"]\n', 'loop"]\t\n'), ("LR\n    A", "LR \n    A"),
    ('loop"]\n    B', 'loop"]; B'), ("--> B[", "-->\n    B["), ('"x"', '"y"'),
    ("text\n", "text!\n"), ("text\n", ""), ("text\n", "text\ntext\n"), ("text\n", "%% text\n"),
    (TAIL, ""), (TAIL, "    %%" + TAIL[4:]), ("```\n", "```"), ("# T\n", "\ufeff# T\n"),
    (TAIL, TAIL + TAIL), (TAIL, TAIL + '    A["in"] --> B["loop"]\n'),
    ('    A["in"] --> B["loop"]\n' + TAIL, TAIL + '    A["in"] --> B["loop"]\n'),
    (TAIL, TAIL + "    subgraph Output\n    end\n    output --> A\n"),
    (TAIL, TAIL + "    subgraph Output\n    B\n```\n```mermaid\n"),
    (TAIL, "    subgraph Output\n    end\n" + TAIL.replace('"x"', '"y"')),
])
def test_changed_original_lines_fail(old: str, new: str) -> None:
    with pytest.raises(mod.RestyleError):
        mod.check(B, B.replace(old, new, 1))


def test_line_endings_must_match_baseline() -> None:
    crlf, mixed = B.replace("\n", "\r\n"), B.replace("text\n", "text\r\n")
    for base, new in [(crlf, B), (B, crlf), (mixed, B), (mixed, mixed.replace(
            "# T\n", "# T\r\n").replace("text\r\n", "text\n")),
            (crlf, crlf.replace('out?"}\r\n', 'out?"}\r\n    class A io\n'))]:
        with pytest.raises(mod.RestyleError):
            mod.check(base, new)


def test_main_readmes_self_compare() -> None:
    for path in sorted(ROOT.glob("s[0-2][0-9]_*/README*.md")):
        assert mod.check(*[path.read_bytes().decode("utf-8")] * 2) == 0
    s10 = (ROOT / "s10_workspace_memory" / "README.md").read_bytes().decode("utf-8")
    with pytest.raises(mod.RestyleError):
        mod.check(s10, s10.replace("\r\n", "\n"))
