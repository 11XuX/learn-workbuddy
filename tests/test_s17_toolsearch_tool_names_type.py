"""Reproduce: s17 ToolSearch aborts the agent loop when tool_names is not a list.

input_schema declares tool_names as a required array of strings. Sibling s03
ToolSearch returns an error string when tool_names is missing/None. s17 instead
does `for tn in tool_names`, so null/int raise TypeError and kill MCPAgent.run;
a bare string is iterated as characters and yields bogus per-character denials.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_s17():
    stub_dir = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    os.environ["MODEL_ID"] = "offline-test-model"
    module_name = "s17_toolsearch_tool_names_type_repro"
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, ROOT / "s17_mcp_connectors" / "code.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(stub_dir))
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        if old_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = old_model


@pytest.fixture
def s17():
    module = _load_s17()
    try:
        yield module
    finally:
        sys.modules.pop("s17_toolsearch_tool_names_type_repro", None)


def _agent(s17):
    manager = s17.ConnectorManager(s17.MCP_CONFIG, s17.DEMO_MCP_PERMISSIONS)
    manager.trust_all()
    return s17.MCPAgent(manager)


@pytest.mark.parametrize("tool_names", [None, 1, "mcp__github__list_issues"])
def test_toolsearch_non_list_tool_names_returns_error_not_typeerror(s17, tool_names):
    """ToolSearch must fail closed with an error string, not raise TypeError."""
    agent = _agent(s17)
    block = SimpleNamespace(
        name="ToolSearch",
        input={"tool_names": tool_names},
    )

    # On upstream/main this raises TypeError for None/int (agent loop abort),
    # and for a bare string yields one permission_denied entry per character.
    output = agent.handle_tool_call(block)

    assert isinstance(output, str)
    assert "TypeError" not in output
    # Must not pretend each character of a string is a tool name.
    if isinstance(tool_names, str):
        assert '"tool": "m"' not in output
        assert "mcp__github__list_issues" in output or "tool_names" in output.lower()
    else:
        assert "error" in output.lower() or "tool_names" in output.lower()


def test_toolsearch_list_tool_names_still_loads_schema_control(s17):
    """Control: a proper list keeps working (passes on main and after fix)."""
    agent = _agent(s17)
    block = SimpleNamespace(
        name="ToolSearch",
        input={"tool_names": ["mcp__github__list_issues"]},
    )
    output = agent.handle_tool_call(block)
    assert '"input_schema"' in output
    assert "permission_denied" not in output


def test_toolsearch_missing_tool_names_does_not_raise_control(s17):
    """Control: an absent key already defaults to [] and must not raise."""
    agent = _agent(s17)
    output = agent.handle_tool_call(SimpleNamespace(name="ToolSearch", input={}))
    assert isinstance(output, str)
