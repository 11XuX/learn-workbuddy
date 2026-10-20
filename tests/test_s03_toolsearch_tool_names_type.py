"""Reproduce: ToolSearch iterates string tool_names; non-list raises TypeError."""

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


def test_string_tool_names_not_iterated_as_characters() -> None:
    s03 = load_s03()
    reg = s03.build_registry()
    out = s03.handle_tool_search(reg, tool_names="image_gen")
    assert "✗ i:" not in out
    assert "image_gen" in reg.get_loaded_names() or out.startswith("Error")


def test_non_list_tool_names_return_error_string() -> None:
    s03 = load_s03()
    out = s03.handle_tool_search(s03.build_registry(), tool_names=123)
    assert isinstance(out, str) and out.startswith("Error")


def test_agent_loop_survives_non_list_tool_names() -> None:
    s03 = load_s03()
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
