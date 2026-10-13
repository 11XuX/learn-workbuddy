"""独立验证 #28：属性名边界、合法输入及拒绝时的副作用。"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock
import xml.etree.ElementTree as ET

import pytest

ERR = "Error: SVG viewBox must start with '0 0 680'."


@pytest.fixture
def harness(tmp_path, monkeypatch):
    # 完整仓库沿用已有加载器；审查材料目录则导入原样代码片段。
    root = Path(__file__).resolve().parents[1]
    if (root / "s19_visualizer" / "code.py").is_file():
        from test_s19_svg_viewbox_check import MODULE_NAME, _load_s19

        module = _load_s19(tmp_path)
        monkeypatch.setattr(module.time, "sleep", lambda _: None)
        cleanup_name = MODULE_NAME
    else:
        candidates = [
            Path(__file__).resolve().parent / "code_snippet.py",
            root / "code_snippet.py",
        ]
        source = next(path for path in candidates if path.is_file())
        spec = importlib.util.spec_from_file_location(
            "s19_adversarial_snapshot", source
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        module.__dict__.update(
            DesignGuide=type("DesignGuide", (), {}),
            re=re,
            json=json,
            time=SimpleNamespace(sleep=lambda _: None),
            webbrowser=SimpleNamespace(open=lambda _: None),
        )
        spec.loader.exec_module(module)
        cleanup_name = None

    storage = MagicMock()
    browser = Mock()
    monkeypatch.setattr(module, "WIDGET_DIR", storage, raising=False)
    monkeypatch.setattr(module.webbrowser, "open", browser)
    renderer = module.WidgetRenderer(
        SimpleNamespace(colors={"bg": "white", "fg": "black"})
    )
    try:
        yield renderer, storage, browser
    finally:
        if cleanup_name is not None:
            sys.modules.pop(cleanup_name, None)


@pytest.mark.parametrize("attribute", ["foo.viewBox", "data.viewBox"])
def test_dotted_attribute_cannot_supply_required_viewbox(harness, attribute):
    renderer, storage, browser = harness
    code = (
        f"<svg {attribute}='0 0 680 400' "
        "viewBox='0 0 400 300'></svg>"
    )
    # 这是合法 XML；伪属性与真正的 viewBox 属于同一根元素。
    attributes = ET.fromstring(code).attrib
    assert attributes[attribute] == "0 0 680 400"
    assert attributes["viewBox"] == "0 0 400 300"
    # 证明该输入是新增误收，而非既有子串判断的已知漏收。
    assert 'viewBox="0 0 680' not in code

    result = renderer.show_widget("adversarial", code, ["x"])

    assert result.startswith(ERR), result
    assert renderer.widget_count == 0
    storage.__truediv__.assert_not_called()
    browser.assert_not_called()


@pytest.mark.parametrize("quote", ["'", '"'])
def test_real_viewbox_with_mixed_xml_separators_renders(harness, quote):
    renderer, storage, browser = harness
    code = (
        f"<svg viewBox\t=\n{quote} 0 \t,0,\r\n680 400{quote}></svg>"
    )
    result = json.loads(renderer.show_widget("valid", code, ["x"]))
    assert result["status"] == "rendered"
    assert result["type"] == "svg"
    assert renderer.widget_count == 1
    storage.__truediv__.return_value.write_text.assert_called_once_with(code)
    browser.assert_called_once()


@pytest.mark.parametrize(
    "value",
    ["0 0 6800 400", "0 0 680e1 400", "0 0 680.5 400",
     "0,,0,680 400", "0\u00a00\u00a0680 400"],
)
def test_rejection_precedes_save_and_browser_open(harness, value):
    renderer, storage, browser = harness
    code = f"<svg viewBox='{value}'></svg>"
    result = renderer.show_widget("invalid", code, ["x"])
    assert result.startswith(ERR), result
    assert renderer.widget_count == 0
    storage.__truediv__.assert_not_called()
    browser.assert_not_called()
