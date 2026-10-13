"""Adversarial boundaries for s19 show_widget HTML fragment tag matching."""

from __future__ import annotations

import json

import pytest

from test_s19_widget_html_fragment_tags import renderer  # noqa: F401


def test_adversarial_fragment_boundaries(renderer):  # noqa: F811
    rejected = [
        ("<HtMl lang='zh'>", "<html"),
        ("<HeAd/>", "<head"),
        ("prefix<BoDy", "<body"),
        ("<!dOcTyPehtml>", "<!DOCTYPE"),
        ("<!doctype\x00html>", "<!DOCTYPE"),
        ("<!DOCTYPE", "<!DOCTYPE"),
    ]
    rejected += [
        ("<bOdY" + whitespace + "class=x>", "<body")
        for whitespace in ("\t", "\n", "\r", "\f", "\v", "\xa0")
    ]
    for code, forbidden in rejected:
        out = renderer.show_widget("boundary", code, ["x"])
        assert out == (
            f"Error: HTML must be a raw fragment. Found '{forbidden}'."
        ), (repr(code), out)

    for code in (
        "<HEADER>ok</HEADER>",
        "<head-bar/>",
        "<body-copy data-x='1'>",
        "<htmlx>",
        "<body:x>",
        "<body\x00>",
    ):
        out = renderer.show_widget("allowed", code, ["x"])
        assert json.loads(out)["status"] == "rendered", (repr(code), out)
