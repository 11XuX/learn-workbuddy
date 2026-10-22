"""S14: a large read_file result must never be cut silently.

``run_read`` caps reads at 20000 characters with no note, and 20000 chars is
exactly ``MAX_TOOL_RESULT_TOKENS`` (5000 tokens via ``len // 4``), so Layer 1
(``> 5000``) never marks it either.  Layer 1 also only runs inside
``compact_context`` once the view reaches ``TOKEN_THRESHOLD``.  Contract: what
the model sees reveals the original length (in normal turns *and* after
Layer 1) and stays within the README's 5000-token per-result budget.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s14():
    stub_dir = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    name = "s14_read_layer1_test_module"
    try:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "s14_context_compact" / "code.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(stub_dir))
        sys.modules.pop(name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic


def _seen(s14, tmp_path, monkeypatch, size: int, *, layer1: bool) -> str:
    monkeypatch.setattr(s14, "WORKDIR", tmp_path.resolve())
    (tmp_path / "big.txt").write_text("x" * size)
    use = {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "big.txt"}}
    result = {"type": "tool_result", "tool_use_id": "t1", "_read_path": "big.txt",
              "content": s14.TOOL_HANDLERS["read_file"](path="big.txt")}
    view = [{"role": "user", "content": "read big.txt"},
            {"role": "assistant", "content": [use]},
            {"role": "user", "content": [result]}]
    if layer1:
        view, _ = s14.truncate_tool_results(view)
    else:  # a normal turn: far below TOKEN_THRESHOLD, so no layer runs
        view = s14.compact_context(view, verbose=False).messages
    return str(view[2]["content"][0]["content"])


@pytest.mark.parametrize("layer1", [False, True], ids=["normal_turn", "after_layer1"])
def test_large_read_is_never_cut_silently(s14, tmp_path, monkeypatch, layer1):
    seen = _seen(s14, tmp_path, monkeypatch, 30000, layer1=layer1)
    assert "30000" in seen, "file was cut but the model cannot tell it was"
    assert seen.startswith("x" * 1000)


def test_large_read_stays_within_per_result_budget(s14, tmp_path, monkeypatch):
    seen = _seen(s14, tmp_path, monkeypatch, 30000, layer1=False)
    assert len(seen) <= s14.MAX_TOOL_RESULT_TOKENS * 4 + 200


@pytest.mark.parametrize("size", [100, 20000])
def test_reads_within_budget_are_untouched(s14, tmp_path, monkeypatch, size):
    assert _seen(s14, tmp_path, monkeypatch, size, layer1=True) == "x" * size
