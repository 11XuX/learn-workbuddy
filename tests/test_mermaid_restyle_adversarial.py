"""Adversarial tests for the presentation-only Mermaid checker."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_mermaid_restyle_adversarial",
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "check_mermaid_restyle.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

BASE = [
    "flowchart LR",
    'A["Prompt"] --> B["Loop"]',
    'B -->|"call"| C["Tool"]',
    'C -. "error" .-> D["Result"]',
]


def test_parser_preserves_node_text_binding_and_edge_attributes():
    parsed = mod.parse(BASE)
    assert parsed["direction"] == "flowchart LR"
    assert parsed["nodes"] == {
        ("A", "Prompt"), ("B", "Loop"), ("C", "Tool"), ("D", "Result")
    }
    assert dict(parsed["edges"]) == {
        ("A", "B", None, "solid"): 1,
        ("B", "C", "call", "solid"): 1,
        ("C", "D", "error", "dotted"): 1,
    }
    swapped = mod.parse([
        line.replace('A["Prompt"]', 'A["Loop"]')
        .replace('B["Loop"]', 'B["Prompt"]')
        for line in BASE
    ])
    assert parsed["nodes"] != swapped["nodes"]


@pytest.mark.parametrize("edge", [
    'C -->|"error"| D',
    'C ---|"error"| D',
    'D -. "error" .-> C',
    'C -. "other" .-> D',
])
def test_parser_distinguishes_edge_semantics_without_line_guard(edge):
    # Define each node's text once; both final-edge endpoints already exist.
    prefix = BASE[:3] + ['A --> D["Result"]']
    original = prefix + ['C -. "error" .-> D']
    altered = prefix + [edge]
    assert mod.parse(original)["edges"] != mod.parse(altered)["edges"]


@pytest.mark.parametrize("extra", [
    ["classDef core fill:#fff;A-->X"],
    ['classDef core fill:#fff;A-->|"extra"|B'],
    ["classDef core fill:#fff", "classDef core fill:#fff"],
    [
        'subgraph loop["Agent Loop"]', "A", "B", "end",
        'subgraph loop["Agent Loop"]', "C", "D", "end",
    ],
])
def test_injection_and_duplicate_definitions_are_rejected(extra):
    with pytest.raises(mod.RestyleError):
        mod.compare(BASE, BASE + extra)


def test_subgraph_id_cannot_redefine_a_node():
    base = ['flowchart LR', 'loop["Existing node"] --> B["Output"]']
    extra = ['subgraph loop["Agent Loop"]', "loop", "B", "end"]
    with pytest.raises(mod.RestyleError):
        mod.compare(base, base + extra)


def test_valid_style_and_grouping_remain_accepted():
    extra = [
        'subgraph loop["Agent Loop"]', "B", "C", "end",
        "classDef core fill:#ede9fe,stroke:#6d28d9,color:#2e1065",
        "class A,B,C,D core",
    ]
    assert mod.compare(BASE, BASE + extra) == [("loop", "Agent Loop")]


def test_equivalent_dotted_syntax_still_counts_as_duplicate():
    with pytest.raises(mod.RestyleError):
        mod.compare(BASE, BASE + ['C -.->|"error"| D'])
