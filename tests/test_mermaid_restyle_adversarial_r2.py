"""Round-two adversarial tests; samples only, no extra dependencies."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_mermaid_restyle_adversarial_r2",
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "check_mermaid_restyle.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

BASE = [
    "flowchart LR",
    'A["Prompt"]',
    'B["Loop"]',
    'A -->|"call"| B',
]
CORE = "classDef core fill:#123456"


def test_node_text_binding_without_line_guard():
    swapped = [BASE[0], 'A["Loop"]', 'B["Prompt"]', BASE[3]]
    assert mod.parse(BASE)["nodes"] == {("A", "Prompt"), ("B", "Loop")}
    assert mod.parse(BASE)["nodes"] != mod.parse(swapped)["nodes"]


@pytest.mark.parametrize("edge", [
    'B -->|"call"| A',
    'A -->|"other"| B',
    'A ---|"call"| B',
    'A -.->|"call"| B',
])
def test_edge_attributes_without_line_guard(edge):
    assert mod.parse(BASE)["edges"] != mod.parse(BASE[:-1] + [edge])["edges"]


@pytest.mark.parametrize("extra, message", [
    (CORE + "; B --> A", "边不一致"),
    (CORE + '; A -->|"call"| B', "重复的边"),
    ('A["Prompt"]', "节点重复定义文字"),
    (CORE + "; " + CORE, "重复的 classDef"),
])
def test_injection_and_duplicates_reach_the_intended_check(extra, message):
    with pytest.raises(mod.RestyleError, match=message):
        mod.compare(BASE, BASE + [extra])


def test_original_lines_cannot_be_joined_with_semicolons():
    with pytest.raises(mod.RestyleError):
        mod.compare(BASE, ["; ".join(BASE)])


@pytest.mark.parametrize("attribute", [
    "color:2px",
    "fill:2px",
    "stroke-width:#123456",
])
def test_classdef_values_must_match_the_property(attribute):
    with pytest.raises(mod.RestyleError):
        mod.compare(BASE, BASE + [CORE + "," + attribute])


def test_comment_edges_are_ignored_but_directives_are_rejected():
    assert mod.compare(BASE, BASE + ['  %% A --> X; "unclosed']) == []
    with pytest.raises(mod.RestyleError):
        mod.compare(BASE, BASE + ['%%{init: {"theme": "dark"}}%%'])


def test_node_ids_are_case_sensitive():
    extra = ['a["Lower"] --> A']
    assert mod.parse(BASE + extra)["nodes"] == {
        ("A", "Prompt"), ("B", "Loop"), ("a", "Lower"),
    }
    with pytest.raises(mod.RestyleError, match="节点"):
        mod.compare(BASE, BASE + extra)


def test_indentation_and_valid_semicolon_styles_pass():
    new = ["    " + line for line in BASE]
    new += [CORE + ",stroke-width:2px; class A,B core"]
    assert mod.compare(BASE, new) == []
