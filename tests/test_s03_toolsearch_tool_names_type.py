"""Reproduce: ToolSearch non-list tool_names → char-split or TypeError."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_s03():
    name = "s03_toolsearch_type_module"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "s03_deferred_loading" / "code.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_string_tool_names_return_error_not_char_split() -> None:
    """Schema wants array; string must be Error observation, not char missing."""
    s03 = load_s03()
    reg = s03.build_registry()
    out = s03.handle_tool_search(reg, tool_names="image_gen")
    assert out.startswith("Error")
    assert "✗ i:" not in out
    assert "image_gen" not in reg.get_loaded_names()


def test_non_list_tool_names_return_error_string() -> None:
    s03 = load_s03()
    out = s03.handle_tool_search(s03.build_registry(), tool_names=123)
    assert isinstance(out, str) and out.startswith("Error")


def test_string_queries_return_error_not_char_iterated() -> None:
    """Same handler boundary: queries must be array, not char-split fuzzy."""
    s03 = load_s03()
    out = s03.handle_tool_search(s03.build_registry(), queries="image")
    assert isinstance(out, str) and out.startswith("Error")


def test_agent_loop_surfaces_error_for_non_list_tool_names() -> None:
    """Bare except that only survives is insufficient; model must see Error."""
    s03 = load_s03()
    captured: list[str] = []
    real = s03.handle_tool_search

    def spy(registry, tool_names=None, queries=None, top_k=3):
        out = real(registry, tool_names=tool_names, queries=queries, top_k=top_k)
        captured.append(out)
        return out

    s03.handle_tool_search = spy
    llm = s03.MockLLM(
        [
            s03.MockResponse(
                tool_calls=[{"name": "ToolSearch", "input": {"tool_names": 123}}]
            ),
            s03.MockResponse(text="done", stop_reason="end_turn"),
        ]
    )
    result = s03.agent_loop(s03.build_registry(), llm, "hi")
    assert result["toolsearch_calls"] == 1
    assert result["turns"] == 2
    assert len(captured) == 1
    assert captured[0].startswith("Error")
