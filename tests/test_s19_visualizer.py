"""s19_visualizer tests: WidgetRenderer.show_widget HTML fragment and SVG viewBox checks.

Merged from six files (#57). Each section below starts with a comment naming its
original file and origin (repro / Builder edge cases / Verifier adversarial).
Test functions, parameters, assertions and comments are moved unchanged.
"""

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

MODULE_NAME = "s19_visualizer_tests"
ERR = "Error: SVG viewBox must start with '0 0 680'."


@pytest.fixture
def renderer(tmp_path, monkeypatch, load_chapter):
    s19 = load_chapter("s19_visualizer", MODULE_NAME, tmp_path)
    monkeypatch.setattr(s19.time, "sleep", lambda _s: None)
    monkeypatch.setattr(s19.webbrowser, "open", lambda _url: None)
    try:
        yield s19.WidgetRenderer(s19.DesignGuide())
    finally:
        sys.modules.pop(MODULE_NAME, None)


@pytest.fixture
def harness(tmp_path, monkeypatch, load_chapter):
    # 完整仓库沿用已有加载器；审查材料目录则导入原样代码片段。
    root = Path(__file__).resolve().parents[1]
    if (root / "s19_visualizer" / "code.py").is_file():
        module = load_chapter("s19_visualizer", MODULE_NAME, tmp_path)
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


# ============================================================================
# 原文件：widget_html_fragment_tags（即 tests/test_s19_<名字>.py 中的 <名字>）
# 来源：复现用例（PR 48，HTML 片段标签）
# 原模块说明：
#   Reproduce: s19 show_widget's HTML-fragment check is a case-sensitive substring test.
#
#   README / SYSTEM / tool schema say an HTML widget must be a raw fragment with no
#   `<!DOCTYPE>`, `<html>`, `<head>`, `<body>` tags. `WidgetRenderer.show_widget`
#   checks `forbidden in widget_code` for "<!DOCTYPE", "<html", "<head", "<body":
#   - `<header>` (a normal fragment element, e.g. mockup navigation) contains
#     "<head" and is wrongly rejected;
#   - `<!doctype html>` / `<HTML>` / `<HEAD>` / `<BODY>` (HTML tags are
#     case-insensitive) pass the check and get nested inside the preview document.
# ============================================================================


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


# ============================================================================
# 原文件：widget_doctype_prefix（即 tests/test_s19_<名字>.py 中的 <名字>）
# 来源：Builder 边界（PR 48，<!DOCTYPE 前缀）
# 原模块说明：
#   s19 show_widget: `<!DOCTYPE` stays a case-insensitive prefix match.
#
#   Any character after `<!DOCTYPE` starts a doctype in HTML (`<!DOCTYPEhtml>` is a
#   doctype named html), so the tag-name boundary applies only to html/head/body.
# ============================================================================


@pytest.mark.parametrize("code", ["<!DOCTYPEhtml><p>hi</p>", "<!doctypehtml><p>hi</p>"])
def test_doctype_without_space_rejected(renderer, code):
    out = renderer.show_widget("doc", code, ["x"])
    assert out == "Error: HTML must be a raw fragment. Found '<!DOCTYPE'.", out


# ============================================================================
# 原文件：widget_html_fragment_adversarial（即 tests/test_s19_<名字>.py 中的 <名字>）
# 来源：Verifier 对抗测试（PR 48，HTML 片段标签边界）
# 原模块说明：
#   Adversarial boundaries for s19 show_widget HTML fragment tag matching.
# ============================================================================


def test_adversarial_fragment_boundaries(renderer):
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


# ============================================================================
# 原文件：svg_viewbox_check（即 tests/test_s19_<名字>.py 中的 <名字>）
# 来源：复现用例（PR 49，SVG viewBox）
# 原模块说明：
#   Reproduce: s19 show_widget's SVG viewBox check is a raw substring test.
#
#   README (s19 L167-168) / SYSTEM / tool schema: SVG must start with `<svg>` and
#   its viewBox must start with `0 0 680` (e.g. `0 0 680 400`). The code checks
#   `'viewBox="0 0 680' in widget_code`:
#   - `viewBox='0 0 680 400'` (single-quoted attribute, valid SVG) is rejected;
#   - `viewBox="0 0 6800 400"` (width 6800, not 680) is accepted.
# ============================================================================


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


# ============================================================================
# 原文件：svg_viewbox_edges（即 tests/test_s19_<名字>.py 中的 <名字>）
# 来源：Builder 边界（PR 49，SVG viewBox，#28）
# 原模块说明：
#   Builder edge cases for the s19 show_widget SVG viewBox check (#28).
# ============================================================================


@pytest.mark.parametrize(
    "code",
    [
        '<svg viewBox = " 0  0  680 400" ></svg>',  # spaces around = and inside value
        '<svg viewBox="0\t0\n680 400"></svg>',  # tab / newline separators
        '<svg viewBox="0, 0, 680, 400"></svg>',  # comma plus space
        '<svg viewBox="0 0 680"></svg>',  # value ends right after 680
        '<svg viewBox="0 ,0, 680 400"></svg>',  # 空白与单个逗号混用
        '<svg viewBox="0\r\n0\r\n680 400"></svg>',  # CRLF 属于 XML 空白
        '<svg viewBox="0 0 680,400"></svg>',  # 680 后接逗号
        '<svg\tviewBox="0 0 680 400"></svg>',  # 属性名前是 tab
        '<svg\r\nviewBox="0 0 680 400"></svg>',  # 属性名前是 CRLF
        '<svg id="a" viewBox="0 0 680 400"></svg>',  # 前面还有别的属性
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
        '<svg viewBox="0\xa00\xa0680 1"></svg>',  # NBSP 不是 XML 空白
        '<svg viewBox="0\u20280\u2028680"></svg>',  # U+2028
        '<svg viewBox\xa0=\xa0"0 0 680"></svg>',  # = 两侧 NBSP
        '<svg viewBox="\u30000 0 680"></svg>',  # 全角空格开头
        '<svg viewBox="0\u20090 680"></svg>',  # U+2009 细空格
        '<svg viewBox="0,,0,680"></svg>',  # 最多一个逗号
        '<svg viewBox="0 , , 0 680"></svg>',
        '<svg viewBox=",0 0 680 400"></svg>',  # 前导逗号
        '<svg viewBox="0 0 680\u0660 400"></svg>',  # 680 后接阿拉伯-印度数字
        '<svg viewBox="\uff10 \uff10 \uff16\uff18\uff10"></svg>',  # 全角数字
        '<svg viewBox="0 0 \u0666\u0668\u0660"></svg>',  # 阿拉伯-印度数字 680
        '<svg foo.viewBox="0 0 680 400" viewBox="0 0 400 300"></svg>',  # 属性名前是 .
        '<svg data.viewBox="0 0 680 400"></svg>',
        '<svg a\u00b7viewBox="0 0 680 400"></svg>',  # 属性名前是中点
        '<svg\xa0viewBox="0 0 680 400"></svg>',  # NBSP 不是 XML 空白
        '<svg/viewBox="0 0 680 400"></svg>',
        '<svg a="x"viewBox="0 0 680 400"></svg>',  # 属性之间缺空白，不是合法 XML
        '<svg\u2028viewBox="0 0 680 400"></svg>',
    ],
)
def test_viewbox_edges_rejected(renderer, code):
    out = renderer.show_widget("chart", code, ["x"])
    assert out.startswith(ERR), out


# 已知限制（卡面「范围外」，Critic 裁定）：viewBox 文本藏在其他属性值里，
# 例如 <svg title="a viewBox='0 0 680'" viewBox="0 0 1 1">，仍会被误收。
# 要堵需解析属性，超出 L336 一行范围，故此处不写「应拒绝」断言。


# ============================================================================
# 原文件：svg_viewbox_adversarial_verifier（即 tests/test_s19_<名字>.py 中的 <名字>）
# 来源：Verifier 对抗测试（PR 49，SVG viewBox，#28）
# 原模块说明：
#   独立验证 #28：属性名边界、合法输入及拒绝时的副作用。
# ============================================================================


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


# r2（65869f9）：属性名前必须是 XML 空白的新边界
@pytest.mark.parametrize(
    "prefix",
    [
        " ",
        "\t",
        "\r",
        "\n",
        " \t\r\n ",
        ' xmlns="http://www.w3.org/2000/svg"\n',
        ' id="x"\t',
        ' xmlns:p="urn:p" p:flag="yes"\r\n',
    ],
)
def test_r2_xml_attribute_boundary_accepts_valid_prefixes(harness, prefix):
    renderer, storage, browser = harness
    code = f"<svg{prefix}viewBox='0 0 680 400'></svg>"
    assert ET.fromstring(code).attrib["viewBox"] == "0 0 680 400"

    result = json.loads(renderer.show_widget("boundary", code, ["x"]))

    assert result["status"] == "rendered"
    assert result["type"] == "svg"
    assert renderer.widget_count == 1
    storage.__truediv__.return_value.write_text.assert_called_once_with(code)
    browser.assert_called_once()


@pytest.mark.parametrize(
    "prefix",
    ["", "\f", "\v", "\u00a0", "\u2028", "/", ' id="x"'],
)
def test_r2_invalid_attribute_separation_has_no_save_side_effects(harness, prefix):
    renderer, storage, browser = harness
    code = f"<svg{prefix}viewBox='0 0 680 400'></svg>"
    with pytest.raises(ET.ParseError):
        ET.fromstring(code)
    assert 'viewBox="0 0 680' not in code

    result = renderer.show_widget("boundary", code, ["x"])

    assert result.startswith(ERR), result
    assert renderer.widget_count == 0
    storage.__truediv__.assert_not_called()
    browser.assert_not_called()


@pytest.mark.parametrize("attribute", ["p:viewBox", "a\u00b7viewBox"])
def test_r2_other_xml_attribute_names_cannot_supply_viewbox(harness, attribute):
    renderer, storage, browser = harness
    code = (
        f"<svg xmlns:p='urn:p' {attribute}='0 0 680 400' "
        "viewBox='0 0 400 300'></svg>"
    )
    attributes = ET.fromstring(code).attrib
    key = "{urn:p}viewBox" if attribute == "p:viewBox" else attribute
    assert attributes[key] == "0 0 680 400"
    assert attributes["viewBox"] == "0 0 400 300"
    assert 'viewBox="0 0 680' not in code

    result = renderer.show_widget("boundary", code, ["x"])

    assert result.startswith(ERR), result
    assert renderer.widget_count == 0
    storage.__truediv__.assert_not_called()
    browser.assert_not_called()
