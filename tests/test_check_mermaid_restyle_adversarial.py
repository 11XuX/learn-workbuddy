import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_mermaid_restyle_adversarial",
    ROOT / "scripts" / "check_mermaid_restyle.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)  # type: ignore[union-attr]

BASE = '```mermaid\nflowchart LR\nA["a"] --> B["b"]\n```\n'


def reject(base, new):
    with pytest.raises(mod.RestyleError):
        mod.check(base, new)


def insert(base, extra):
    return base.replace("\n```\n", "\n" + extra + "```\n", 1)


@pytest.mark.parametrize("space", [" ", "\t", "\u00a0", "\u3000"])
def test_original_trailing_whitespace_is_not_indent(space):
    reject(BASE, BASE.replace('B["b"]\n', 'B["b"]' + space + "\n"))


@pytest.mark.parametrize("prefix", ["\u00a0", "\u3000", "\ufeff"])
def test_non_ascii_indent_and_bom_are_not_ignored(prefix):
    reject(BASE, BASE.replace('A["a"]', prefix + 'A["a"]'))


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85"])
def test_unicode_line_separator_cannot_smuggle_statement(separator):
    reject(BASE, insert(
        BASE, "classDef io fill:#fff" + separator + "A-->X\n"
    ))


@pytest.mark.parametrize("extra", [
    "classDef io fill:#fff;A-->X\n",
    "classDef io fill:#fff %%{init: {}}%%\n",
    "classDef io fill:#fff/*A-->X*/\n",
    "classDef io fill:#fff\t\n",
    "class A io %% hidden\n",
    "class A io;A-->X\n",
    "%% class A io\n",
])
def test_classdef_and_comment_payloads_fail(extra):
    reject(BASE, insert(BASE, extra))


@pytest.mark.parametrize("opening", ["subgraph\tS", "subgraph  S"])
@pytest.mark.parametrize("closing", ["end ", "end\t"])
def test_added_end_cannot_take_over_existing_subgraph(opening, closing):
    base = (
        "```mermaid\nflowchart LR\n"
        + opening + "\nA[a]\n" + closing + "\n```\n"
    )
    new = base.replace("A[a]\n", "subgraph Output\nA[a]\n")
    new = new.replace(closing + "\n", closing + "\nend\n")
    reject(base, new)


def test_multiline_label_text_is_not_a_node_id():
    base = '```mermaid\nflowchart LR\nA["label\nGhost[x]\n"]\n```\n'
    reject(base, insert(base, "class Ghost io\n"))


@pytest.mark.parametrize("outer", ["````", "~~~"])
@pytest.mark.parametrize("extra", ["", "classDef io fill:#fff\n"])
def test_mermaid_example_inside_outer_fence_is_outside_diagram(outer, extra):
    base = outer + "text\n```mermaid\nexample\n```\n" + outer + "\n"
    new = base.replace("example\n", "  example\n" + extra)
    reject(base, new)


@pytest.mark.parametrize("fence", ["````", "~~~", "    ```"])
def test_original_closing_fence_cannot_change(fence):
    reject(BASE, BASE.replace("\n```\n", "\n" + fence + "\n"))


def test_original_edge_cannot_change_after_added_end():
    new = BASE.replace(
        'A["a"] --> B["b"]\n',
        'subgraph Output\nend\nA["a"] --> B["changed"]\n',
    )
    reject(BASE, new)
