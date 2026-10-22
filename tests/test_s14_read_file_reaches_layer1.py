"""S14: a large read_file result must be able to reach Layer 1 truncation.

``run_read`` says "Allow large reads to test truncation", and the README says
Layer 1 truncates tool results above ``MAX_TOOL_RESULT_TOKENS`` (5000 tokens,
estimated as ``len // 4``).  A read that is cut at exactly 20000 characters is
5000 tokens, never *above* the limit, so Layer 1 can never fire for read_file.
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
            name, ROOT / "s14_context_compact" / "code.py"
        )
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


def _read_view(s14, output: str) -> list:
    use = {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "big.txt"}}
    result = {"type": "tool_result", "tool_use_id": "t1", "content": output,
              "_read_path": "big.txt"}
    return [{"role": "user", "content": "read big.txt"},
            {"role": "assistant", "content": [use]},
            {"role": "user", "content": [result]}]


def test_layer1_limit_matches_readme(s14):
    assert s14.MAX_TOOL_RESULT_TOKENS == 5000


def test_large_read_file_result_is_truncated_by_layer1(s14, tmp_path, monkeypatch):
    monkeypatch.setattr(s14, "WORKDIR", tmp_path.resolve())
    (tmp_path / "big.txt").write_text("x" * 30000)

    output = s14.run_read("big.txt")
    assert len(output) == 30000, "read_file cut the file before Layer 1 saw it"

    messages, saved = s14.truncate_tool_results(_read_view(s14, output))
    content = messages[2]["content"][0]["content"]
    assert saved > 0
    assert content.startswith("x" * 20000)
    assert "原始 30000 字符" in content


def test_small_read_file_result_is_untouched(s14, tmp_path, monkeypatch):
    monkeypatch.setattr(s14, "WORKDIR", tmp_path.resolve())
    (tmp_path / "big.txt").write_text("y" * 100)

    messages, saved = s14.truncate_tool_results(_read_view(s14, s14.run_read("big.txt")))
    assert saved == 0
    assert messages[2]["content"][0]["content"] == "y" * 100
