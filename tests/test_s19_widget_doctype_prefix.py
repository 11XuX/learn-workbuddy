"""s19 show_widget: `<!DOCTYPE` stays a case-insensitive prefix match.

Any character after `<!DOCTYPE` starts a doctype in HTML (`<!DOCTYPEhtml>` is a
doctype named html), so the tag-name boundary applies only to html/head/body.
"""

from __future__ import annotations

import pytest

from test_s19_widget_html_fragment_tags import renderer  # noqa: F401


@pytest.mark.parametrize("code", ["<!DOCTYPEhtml><p>hi</p>", "<!doctypehtml><p>hi</p>"])
def test_doctype_without_space_rejected(renderer, code):  # noqa: F811
    out = renderer.show_widget("doc", code, ["x"])
    assert out == "Error: HTML must be a raw fragment. Found '<!DOCTYPE'.", out
