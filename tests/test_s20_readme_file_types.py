"""S20: every extension the README lists as supported gets its row's type.

The READMEs' "supported file types" table puts ``.avi`` next to ``.mp4`` /
``.mov`` / ``.webm`` as a playable video, but ``FILE_TYPE_CONFIG`` has no
``.avi`` entry, so ``present_files(["clip.avi"])`` produces a generic "other"
card.  Contract: all extensions in one table row share one category, and none
of them falls back to "other".
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CH = ROOT / "s20_result_presentation"


@pytest.fixture()
def s20(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.chdir(tmp_path)
    sys.path.insert(0, str(ROOT / "tests" / "stubs"))
    saved = sys.modules.pop("anthropic", None)
    name = "s20_readme_file_types_module"
    try:
        spec = importlib.util.spec_from_file_location(name, CH / "code.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(ROOT / "tests" / "stubs"))
        sys.modules.pop(name, None)
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved


def _rows(readme: str):
    """Yield (row label, extensions) from the supported-types table."""
    text = (CH / readme).read_text(encoding="utf-8")
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        exts = re.findall(r"\.[a-z0-9]+\b", cells[1]) if len(cells) > 1 else []
        if len(exts) >= 2 and line.startswith("|"):
            yield cells[0], exts


@pytest.mark.parametrize("readme", ["README.md", "README.en.md"])
def test_readme_extensions_share_their_rows_category(s20, readme):
    rows = list(_rows(readme))
    assert len(rows) >= 5
    for label, exts in rows:
        cats = {e: s20.create_artifact_card(f"f{e}").category for e in exts}
        assert "other" not in cats.values(), (label, cats)
        assert len(set(cats.values())) == 1, (label, cats)


def test_present_files_tool_call_cards_follow_readme_rows(s20, monkeypatch):
    monkeypatch.setattr(s20.webbrowser, "open", lambda *a, **k: True)
    agent = s20.DeliveryAgent(s20.ResultPresenter())
    for label, exts in _rows("README.md"):
        names = [f"x{e}" for e in exts]
        for n in names:
            (s20.OUTPUT_DIR / n).write_bytes(b"x")
        block = SimpleNamespace(name="present_files", input={"files": names})
        agent.handle_tool_call(block)
        cats = [f["category"] for f in agent.presenter.delivery_history[-1]["files"]]
        assert len(set(cats)) == 1 and cats[0] != "other", (label, cats)
