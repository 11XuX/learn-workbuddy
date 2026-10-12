"""Round-four adversarial tests for physical-line preservation."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_mermaid_restyle_adversarial_r4",
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "check_mermaid_restyle.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

BASE = [
    "flowchart LR",
    'A["Prompt"] --> B["Loop"]',
]
CORE = "classDef core fill:#123456"


@pytest.mark.parametrize("suffix", [" ", "\t", "  \t"])
def test_original_line_cannot_gain_trailing_whitespace(suffix):
    new = BASE.copy()
    new[1] += suffix
    with pytest.raises(mod.RestyleError):
        mod.compare(BASE, new)


@pytest.mark.parametrize("suffix", [" ", "\t", "  \t"])
def test_original_line_cannot_lose_trailing_whitespace(suffix):
    old = BASE.copy()
    old[1] += suffix
    with pytest.raises(mod.RestyleError):
        mod.compare(old, BASE)


def test_indentation_change_preserves_existing_trailing_whitespace():
    old = [BASE[0], BASE[1] + " \t"]
    new = ["\t  " + line for line in old]
    new += [CORE, "class A,B core"]
    assert mod.compare(old, new) == []


@pytest.mark.parametrize("line", [
    '%% note %%{init: {"theme": "dark"}}%%',
    '%% note %% \t{init: {"theme": "dark"}}%%',
    CORE + '; %%{init: {"theme": "dark"}}%%',
])
def test_theme_directives_still_rejected_before_comment_handling(line):
    with pytest.raises(mod.RestyleError, match="禁止的主题覆盖"):
        mod.compare(BASE, BASE + [line])


def test_plain_comment_with_fake_edge_remains_ignored():
    comment = '%% ordinary note; A --> X; "unclosed'
    assert mod.compare(BASE, BASE + [comment]) == []


def test_semicolon_join_still_rejected():
    with pytest.raises(mod.RestyleError):
        mod.compare(BASE, ["; ".join(BASE)])


def test_case_distinct_node_cannot_hide_as_existing_node():
    new = BASE + ['a["Lowercase"] --> A']
    assert ("a", "Lowercase") in mod.parse(new)["nodes"]
    with pytest.raises(mod.RestyleError, match="节点"):
        mod.compare(BASE, new)
