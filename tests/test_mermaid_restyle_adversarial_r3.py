"""Round-three adversarial tests; samples only, no extra dependencies."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_mermaid_restyle_adversarial_r3",
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


def test_line_start_theme_directive_still_rejected():
    with pytest.raises(mod.RestyleError, match="禁止的主题覆盖"):
        mod.compare(BASE, BASE + ['%%{init: {"theme": "dark"}}%%'])


@pytest.mark.parametrize("comment", [
    '%% comment %%{init: {"theme": "dark"}}%%',
    '%% %%{init: {"themeVariables": {"primaryColor": "#ff0000"}}}%%',
    '%%foo %%{init: {"theme": "forest"}}%% bar',
    '%% keep text %%{init: {"theme": "dark"}}%%',
])
def test_theme_directive_hidden_inside_comment_is_rejected(comment):
    """Mermaid detects %%{ anywhere; leading %% must not hide init directives."""
    with pytest.raises(mod.RestyleError, match="禁止的主题覆盖"):
        mod.compare(BASE, BASE + [comment])


def test_plain_comments_without_directive_still_allowed():
    assert mod.compare(BASE, BASE + ["%% presentation note only"]) == []


def test_physical_line_guard_still_blocks_rewrites():
    with pytest.raises(mod.RestyleError):
        mod.compare(BASE, ["; ".join(BASE), CORE])


@pytest.mark.parametrize("bad", [
    CORE + ",fill:#1234567",  # 7-digit
    CORE + ",fill:#12345",    # 5-digit
    CORE + ",fill:#123456ff",  # alpha
    CORE + ",stroke-width:01px",
])
def test_strict_hex_and_stroke_width_still_enforced(bad):
    with pytest.raises(mod.RestyleError):
        mod.compare(BASE, BASE + [bad])


def test_valid_six_digit_style_still_passes():
    assert mod.compare(BASE, BASE + [CORE + ",stroke:#abcdef,color:#000000", "class A,B core"]) == []
