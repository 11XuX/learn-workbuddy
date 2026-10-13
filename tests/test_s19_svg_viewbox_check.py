"""Reproduce: s19 show_widget's SVG viewBox check is a raw substring test.

README (s19 L167-168) / SYSTEM / tool schema: SVG must start with `<svg>` and
its viewBox must start with `0 0 680` (e.g. `0 0 680 400`). The code checks
`'viewBox="0 0 680' in widget_code`:
- `viewBox='0 0 680 400'` (single-quoted attribute, valid SVG) is rejected;
- `viewBox="0 0 6800 400"` (width 6800, not 680) is accepted.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "s19_svg_viewbox_check_repro"


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


def test_single_quoted_viewbox_is_accepted(renderer):
    code = "<svg viewBox='0 0 680 400' xmlns='http://www.w3.org/2000/svg'></svg>"
    out = renderer.show_widget("chart", code, ["x"])
    assert '"status": "rendered"' in out, out


def test_comma_separated_viewbox_is_accepted(renderer):
    code = '<svg viewBox="0,0,680,400" xmlns="http://www.w3.org/2000/svg"></svg>'
    out = renderer.show_widget("chart", code, ["x"])
    assert '"status": "rendered"' in out, out


@pytest.mark.parametrize(
    "code",
    [
        '<svg viewBox="0 0 6800 400" xmlns="http://www.w3.org/2000/svg"></svg>',
        '<svg viewBox="0 0 680e1 400" xmlns="http://www.w3.org/2000/svg"></svg>',
        '<svg data-viewBox="0 0 680 400" viewBox="0 0 400 300"></svg>',
    ],
)
def test_wrong_width_viewbox_is_rejected(renderer, code):
    out = renderer.show_widget("chart", code, ["x"])
    assert out.startswith("Error: SVG viewBox must start with '0 0 680'."), out


def test_control_double_quoted_viewbox_is_accepted(renderer):
    code = '<svg viewBox="0 0 680 400" xmlns="http://www.w3.org/2000/svg"></svg>'
    out = renderer.show_widget("chart", code, ["x"])
    assert '"status": "rendered"' in out, out


def test_control_other_width_is_rejected(renderer):
    code = '<svg viewBox="0 0 400 300" xmlns="http://www.w3.org/2000/svg"></svg>'
    out = renderer.show_widget("chart", code, ["x"])
    assert out.startswith("Error: SVG viewBox must start with '0 0 680'."), out
