import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_mermaid_restyle_adversarial_r2",
    ROOT / "scripts" / "check_mermaid_restyle.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def reject(base, new):
    with pytest.raises(mod.RestyleError):
        mod.check(base, new)


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "extra",
    ["classDef k fill:#fff\n", "class A k\n", "\n"],
)
@pytest.mark.parametrize(
    "open_close",
    [("[", "]"), ("(", ")"), ("{", "}")],
)
def test_style_line_inside_unquoted_multiline_label_fails(eol, extra, open_close):
    """Unquoted multiline labels must freeze the block: a whitelist line inserted
    between the opening bracket and its closer becomes label text, changing content."""
    op, cl = open_close
    base = (
        "```mermaid\n"
        "flowchart LR\n"
        f"A{op}hello\n"
        f"world{cl}\n"
        "```\n"
    )
    new = base.replace(f"world{cl}\n", extra + f"world{cl}\n")
    base = base.replace("\n", eol)
    new = new.replace("\n", eol)
    assert mod.check(base, base) == 0
    reject(base, new)


def test_continuation_line_node_shape_is_not_a_baseline_id():
    """A line that looks like ID[x] inside an unquoted multiline label must not
    enlarge the baseline id set (else class on that ghost id is wrongly allowed)."""
    base = (
        "```mermaid\n"
        "flowchart LR\n"
        "A[hello\n"
        "Ghost[x]\n"
        "world]\n"
        "```\n"
    )
    new = base.replace("```\n", "class Ghost k\n```\n", 1)
    assert mod.check(base, base) == 0
    reject(base, new)
