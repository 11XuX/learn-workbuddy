"""Builder edge cases for the s19 show_widget SVG viewBox check (#28)."""

from __future__ import annotations

import sys

import pytest

from test_s19_svg_viewbox_check import MODULE_NAME, _load_s19

ERR = "Error: SVG viewBox must start with '0 0 680'."


@pytest.fixture
def renderer(tmp_path, monkeypatch):
    s19 = _load_s19(tmp_path)
    monkeypatch.setattr(s19.time, "sleep", lambda _s: None)
    monkeypatch.setattr(s19.webbrowser, "open", lambda _url: None)
    try:
        yield s19.WidgetRenderer(s19.DesignGuide())
    finally:
        sys.modules.pop(MODULE_NAME, None)


@pytest.mark.parametrize(
    "code",
    [
        '<svg viewBox = " 0  0  680 400" ></svg>',  # spaces around = and inside value
        '<svg viewBox="0\t0\n680 400"></svg>',  # tab / newline separators
        '<svg viewBox="0, 0, 680, 400"></svg>',  # comma plus space
        '<svg viewBox="0 0 680"></svg>',  # value ends right after 680
        '<svg\nviewBox="0 0 680 400"></svg>',  # attribute on its own line
        '<svg viewBox="0 0 680 -40.5"></svg>',  # negative/decimal height is fine
        '  <svg viewBox="0 0 680 400"></svg>',  # leading whitespace before <svg
        '<svg viewBox="0 0 400 300"><svg viewBox="0 0 680 1"></svg></svg>',  # known limit: any element
    ],
)
def test_viewbox_edges_accepted(renderer, code):
    out = renderer.show_widget("chart", code, ["x"])
    assert '"status": "rendered"' in out, out


@pytest.mark.parametrize(
    "code",
    [
        '<svg viewBox="0 0 680.5 400"></svg>',  # decimal width
        '<svg viewBox="0 0 680_1 400"></svg>',
        '<svg viewBox="0 0 6801"></svg>',
        '<svg viewBox="-0 0 680 400"></svg>',
        '<svg viewBox="10 0 680 400"></svg>',  # min-x not 0
        '<svg viewBox="0 0 68 400"></svg>',
        '<svg viewbox="0 0 680 400"></svg>',  # SVG attribute names are case-sensitive
        '<svg xlink:viewBox="0 0 680 400" viewBox="0 0 1 1"></svg>',
        '<svg my_viewBox="0 0 680 400"></svg>',
        '<svg viewBox="0 0 400 300">viewBox 0 0 680</svg>',  # unquoted text, not an attribute
        '<svg viewBox="0 0 400 300"></svg>',
    ],
)
def test_viewbox_edges_rejected(renderer, code):
    out = renderer.show_widget("chart", code, ["x"])
    assert out.startswith(ERR), out
