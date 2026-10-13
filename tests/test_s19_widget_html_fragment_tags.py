"""Reproduce: s19 show_widget's HTML-fragment check is a case-sensitive substring test.

README / SYSTEM / tool schema say an HTML widget must be a raw fragment with no
`<!DOCTYPE>`, `<html>`, `<head>`, `<body>` tags. `WidgetRenderer.show_widget`
checks `forbidden in widget_code` for "<!DOCTYPE", "<html", "<head", "<body":
- `<header>` (a normal fragment element, e.g. mockup navigation) contains
  "<head" and is wrongly rejected;
- `<!doctype html>` / `<HTML>` / `<HEAD>` / `<BODY>` (HTML tags are
  case-insensitive) pass the check and get nested inside the preview document.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "s19_widget_html_fragment_tags_repro"


def _load_s19(workdir: Path):
    stub_dir = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    old_cwd = os.getcwd()
    os.environ["MODEL_ID"] = "offline-test-model"
    os.chdir(workdir)  # WIDGET_DIR = Path.cwd() / ".widgets" at import time
    try:
        spec = importlib.util.spec_from_file_location(
            MODULE_NAME, ROOT / "s19_visualizer" / "code.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[MODULE_NAME] = module
        spec.loader.exec_module(module)
        return module
    finally:
        os.chdir(old_cwd)
        sys.path.remove(str(stub_dir))
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        if old_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = old_model


@pytest.fixture
def renderer(tmp_path, monkeypatch):
    s19 = _load_s19(tmp_path)
    monkeypatch.setattr(s19.time, "sleep", lambda _s: None)
    monkeypatch.setattr(s19.webbrowser, "open", lambda _url: None)
    try:
        yield s19.WidgetRenderer(s19.DesignGuide())
    finally:
        sys.modules.pop(MODULE_NAME, None)


def test_header_element_is_a_valid_fragment(renderer):
    out = renderer.show_widget("nav", "<header><nav>Home</nav></header>", ["x"])
    assert not out.startswith("Error"), out


@pytest.mark.parametrize(
    "code",
    [
        "<!doctype html><p>hi</p>",
        "<BODY><p>hi</p></BODY>",
    ],
)
def test_full_document_tags_rejected_case_insensitively(renderer, code):
    out = renderer.show_widget("doc", code, ["x"])
    assert out.startswith("Error: HTML must be a raw fragment."), out


def test_hyphenated_custom_elements_are_valid_fragments(renderer):
    code = "<head-bar>x</head-bar><body-copy>y</body-copy>"
    out = renderer.show_widget("custom", code, ["x"])
    assert not out.startswith("Error"), out


def test_control_plain_fragment_renders(renderer):
    out = renderer.show_widget("ok", "<div><p>hi</p></div>", ["x"])
    assert '"status": "rendered"' in out


def test_control_lowercase_full_document_rejected(renderer):
    out = renderer.show_widget("doc", "<html><body>hi</body></html>", ["x"])
    assert out.startswith("Error: HTML must be a raw fragment."), out
