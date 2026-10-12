import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_mermaid_restyle", ROOT / "scripts" / "check_mermaid_restyle.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)  # type: ignore[union-attr]
TAIL = '    B -. "x" .-> output{"out?"}\n'
B = ('# T\n```mermaid\nflowchart LR\n    A["in"] --> B["loop"]\n' + TAIL + '```\ntext\n```mermaid\nflowchart LR\n    Z["other"] --> A\n```\n')
OK = ['    subgraph loop["主循环"]\n|    end\n', "\tsubgraph Input\n|  end\n", "|\n    classDef io fill:#e0f2fe,stroke:#0369a1,stroke-width:1.5px\n    class A io\n"]
BAD = ['    subgraph input["Input"];A-->output\n    end\n', '    subgraph loop["Agent Loop"]\n    end\n  end\n',
       '    subgraph output["Output"]\n    end\n', '    classDef io fill:#fff %%{init}%%\n', '    %% B --> output\n',
       '    subgraph loop["Tools"]\n    end\n', "    %%{init: {'theme':'dark'}}%%\n", '    linkStyle 0 stroke:#000\n',
       '    subgraph x["Input"]\n    end\n', '    classDef io fill:#fff;A-->Z\n', '    class A io;B-->output\n',
       '    A["in"] --> B["loop"]\n', '    subgraph 工具\n    end\n', '    style A fill:#fff\n', '    subgraph Input\n',
       '    class A io\t\n', '    direction TB\n', '    A --> output\n', '    class A io \n', '    class Z io\n',
       '    class Q io\n', '    D["new"]\n', '    end\n', '    Z\n', '    B\n']


@pytest.mark.parametrize("extra", OK)
def test_style_lines_and_indent_pass(extra: str) -> None:
    pre, extra = extra.split("|")  # added lines go before|after TAIL (wrapping it)
    new = B.replace(TAIL, pre + TAIL + extra).replace("    B -. ", "\t  B -. ")
    assert mod.check(B, new) == (pre + extra).count("\n")
    assert mod.check(B.replace("\n", "\r\n"), new.replace("\n", "\r\n")) > 0
    mixed = B.replace("text\n", "text\r\n").replace("# T\n", "# T\r\n")
    assert mod.check(mixed, mixed.replace(TAIL, pre + TAIL + extra)) > 0


@pytest.mark.parametrize("extra", BAD)
def test_smuggled_or_invalid_lines_fail(extra: str) -> None:
    with pytest.raises(mod.RestyleError):
        mod.check(B, B.replace(TAIL, TAIL + extra))


@pytest.mark.parametrize("old,new", [
    ('loop"]\n', 'loop"] \n'), ('loop"]\n', 'loop"]\t\n'), ("LR\n    A", "LR \n    A"), ('"x"', '"y"'),
    ('loop"]\n    B', 'loop"]; B'), ("--> B[", "-->\n    B["), ("text\n", "text!\n"), ("text\n", ""),
    ("text\n", "text\ntext\n"), ("text\n", "%% text\n"), (TAIL, ""), (TAIL, "    %%" + TAIL[4:]), ("```\n", "```"),
    ("# T\n", "\ufeff# T\n"), (TAIL, TAIL + TAIL), (TAIL, TAIL + '    A["in"] --> B["loop"]\n'),
    ('    A["in"] --> B["loop"]\n' + TAIL, TAIL + '    A["in"] --> B["loop"]\n'),
    (TAIL, TAIL + "    subgraph Output\n    end\n    output --> A\n"), (TAIL, TAIL + "    subgraph Output\n    B\n```\n```mermaid\n"),
    (TAIL, "    subgraph Output\n    end\n" + TAIL.replace('"x"', '"y"')),
])
def test_changed_original_lines_fail(old: str, new: str) -> None:
    with pytest.raises(mod.RestyleError):
        mod.check(B, B.replace(old, new, 1))


def test_line_endings_must_match_baseline() -> None:
    crlf, mixed = B.replace("\n", "\r\n"), B.replace("text\n", "text\r\n")
    for base, new in [(crlf, B), (B, crlf), (mixed, B), (mixed, mixed.replace("# T\n", "# T\r\n").replace(
            "text\r\n", "text\n")), (crlf, crlf.replace('out?"}\r\n', 'out?"}\r\n    class A io\n'))]:
        with pytest.raises(mod.RestyleError):
            mod.check(base, new)


def test_main_readmes_self_compare() -> None:
    for path in sorted(ROOT.glob("s[0-2][0-9]_*/README*.md")):
        assert mod.check(*[path.read_bytes().decode("utf-8")] * 2) == 0
    s10 = (ROOT / "s10_workspace_memory" / "README.md").read_bytes().decode("utf-8")
    with pytest.raises(mod.RestyleError):
        mod.check(s10, s10.replace("\r\n", "\n"))


B2 = ('```mermaid\nflowchart LR\n  subgraph S\n    A["a"] -- yes --> D["d"]\n  end\n'
      '  %% ghost\n  click A href "u"\n  click D call fn()\n  Input("i") --- Q\n```\n')
L2, C1, C2 = '  Input("i") --- Q\n', '```mermaid\nA["see B[1]"]\n```\n', "```mermaid\nC -->|x[y]| B\n```\n"
S10 = (ROOT / "s10_workspace_memory" / "README.en.md").read_bytes().decode("utf-8")
Y = next(x for x in S10.splitlines(True) if "D -- yes --> P" in x)


@pytest.mark.parametrize("base,new", [  # Reviewer #1-#3, s10 repro, Critic label / edge-text words
    *[(B2, B2.replace(L2, L2 + x)) for x in [
        "  yes\n", "  class yes io\n", "  class A,ghost io\n", "  class href io\n", "  class fn io\n",
        "  class Q io\n", "  class S io\n", "  class D io\n", '  subgraph input["Input"]\n  end\n',
        "  subgraph Input\n" + L2 + "  end\n"]],
    (B2, B2.replace("S\n", "S\n  subgraph Output\n").replace('d"]\n', 'd"]\n  end\n')),
    (B2, B2.replace("  subgraph S", "  subgraph Output\n  subgraph S").replace("end\n", "end\nend\n")),
    *[(S10, S10.replace(Y, Y + x + Y[len(Y.rstrip()):])) for x in ("    yes", "    no", "    class yes,no hot")],
    *[(c, c.replace("\n```\n", f"\nclass {x} k\n```\n")) for c in (C1, C2) for x in "Bx"],
])
def test_review_repros_fail(base: str, new: str) -> None:
    with pytest.raises(mod.RestyleError):
        mod.check(base, new)
    good = B2.replace(L2, "  subgraph Output\n" + L2 + "  end\n  class A,Input io\n")
    assert mod.check(B2, good) == 3 and mod.main(["--base", "no-such-ref", "x.md"]) == 1
