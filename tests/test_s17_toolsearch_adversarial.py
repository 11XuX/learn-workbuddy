"""Adversarial coverage for s17's top-level ToolSearch type guard."""

import json
from types import SimpleNamespace

import pytest

# Reuse the existing offline importlib loader and its cleanup fixture.
from test_s17_toolsearch_tool_names_type import s17, _agent


ERROR = "Error: tool_names must be an array of tool name strings."
TOOL = "mcp__github__list_issues"


@pytest.mark.parametrize("value", [None, False, "", (), {}, [TOOL][0]])
def test_invalid_shape_never_reaches_search(s17, monkeypatch, value):
    agent = _agent(s17)
    before = dict(agent.manager._deferred_schemas)

    def forbidden_search(_):
        pytest.fail("Invalid tool_names reached permission/schema lookup")

    monkeypatch.setattr(agent.manager, "tool_search", forbidden_search)
    result = agent.handle_tool_call(
        SimpleNamespace(name="ToolSearch", input={"tool_names": value})
    )
    assert result == ERROR
    assert agent.manager._deferred_schemas == before


@pytest.mark.parametrize("params", [{}, {"tool_names": []}])
def test_empty_request_does_not_query_manager(s17, monkeypatch, params):
    agent = _agent(s17)

    def forbidden_search(_):
        pytest.fail("Empty request performed a lookup")

    monkeypatch.setattr(agent.manager, "tool_search", forbidden_search)
    assert json.loads(
        agent.handle_tool_call(SimpleNamespace(name="ToolSearch", input=params))
    ) == []


def test_run_returns_errors_then_accepts_corrected_request(s17, monkeypatch):
    agent = _agent(s17)
    calls = []

    def search_block(identifier, value):
        return SimpleNamespace(
            type="tool_use",
            id=identifier,
            name="ToolSearch",
            input={"tool_names": value},
        )

    responses = iter([
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                search_block("bad-null", None),
                search_block("bad-string", TOOL),
            ],
        ),
        SimpleNamespace(
            stop_reason="tool_use",
            content=[search_block("corrected", [TOOL])],
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="done")],
        ),
    ])

    def create(**kwargs):
        calls.append(len(kwargs["messages"]))
        return next(responses)

    monkeypatch.setattr(
        s17, "client", SimpleNamespace(messages=SimpleNamespace(create=create))
    )
    agent.run("Load the tool schema.")

    assert len(calls) == 3
    errors = agent.messages[2]
    assert errors == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "bad-null",
                "content": ERROR,
            },
            {
                "type": "tool_result",
                "tool_use_id": "bad-string",
                "content": ERROR,
            },
        ],
    }

    corrected = agent.messages[4]["content"]
    assert len(corrected) == 1
    assert corrected[0]["tool_use_id"] == "corrected"
    schema = json.loads(corrected[0]["content"])
    assert len(schema) == 1
    assert schema[0]["name"] == TOOL
    assert "input_schema" in schema[0]
    assert TOOL in agent.manager._deferred_schemas
    assert agent.messages[-1]["role"] == "assistant"
