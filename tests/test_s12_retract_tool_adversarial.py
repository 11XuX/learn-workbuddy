"""Offline adversarial contracts for the s12 retraction tool."""

import json
from types import SimpleNamespace

import pytest
from test_remote_memory import s12  # noqa: F401


@pytest.fixture
def store(s12, tmp_path, monkeypatch):
    st = s12.RemoteMemoryStore(tmp_path / "memory.jsonl", user_id="u")
    source = s12.MemorySource(
        source_id="adversarial-seed",
        source_type="transcript",
        title="seed",
        captured_at=s12._iso(),
    )
    for memory_id in ("target", "keep"):
        st.append(
            kind=s12.MemoryKind.CONVERSATION,
            memory_id=memory_id,
            content="quartz deployment notes",
            summary="quartz deployment notes",
            source=source,
        )
    monkeypatch.setattr(s12, "DEFAULT_STORE", st)
    monkeypatch.setattr(s12, "DEFAULT_RECALL", None)
    monkeypatch.setattr(s12, "SYSTEM", None)
    return st


def call(s12, args):
    return json.loads(s12.dispatch_tool("retract_memory", args))


class StrSubclass(str):
    pass


@pytest.mark.parametrize(
    "args",
    [
        None,
        [],
        "target",
        {},
        {"memory_id": "target"},
        {"reason": "forget"},
        {"memory_id": "target", "reason": "forget", "source": {}},
        {"memory_id": "target", "reason": "forget", "source_type": "user"},
        {"memory_id": True, "reason": "forget"},
        {"memory_id": ["target"], "reason": "forget"},
        {"memory_id": StrSubclass("target"), "reason": "forget"},
        {"memory_id": "target", "reason": None},
        {"memory_id": "target", "reason": StrSubclass("forget")},
        {"memory_id": "target", "reason": ""},
        {"memory_id": "target", "reason": "\t\n "},
        {"memory_id": " target", "reason": "forget"},
        {"memory_id": "TARGET", "reason": "forget"},
        {"memory_id": "x" * 201, "reason": "forget"},
    ],
)
def test_rejects_bad_arguments_without_writing(s12, store, args):
    before = store.path.read_bytes()
    result = call(s12, args)
    assert result["ok"] is False
    assert result["error"]
    assert store.path.read_bytes() == before


def test_tool_recall_transition_and_restart_idempotency(s12, store, monkeypatch):
    def recalled_ids():
        result = json.loads(
            s12.dispatch_tool("recall_history", {"query": "quartz deployment"})
        )
        return {hit["memory_id"] for hit in result["hits"]}

    assert recalled_ids() == {"target", "keep"}
    original = store.read_all()[0]
    first = call(s12, {"memory_id": "target", "reason": "original reason"})
    assert first["ok"] is True
    assert first["retracts"] == "target"
    assert first["reason"] == "original reason"
    assert recalled_ids() == {"keep"}
    records = store.read_all()
    assert len(records) == 3
    assert records[0] == original
    event = records[-1]
    assert event.memory_id == first["memory_id"]
    assert event.kind is s12.MemoryKind.RETRACTION
    assert event.retracts == "target"
    assert event.source.source_type == "model_tool"

    before = store.path.read_bytes()
    reopened = s12.RemoteMemoryStore(store.path, user_id="u")
    monkeypatch.setattr(s12, "DEFAULT_STORE", reopened)
    assert recalled_ids() == {"keep"}
    assert call(
        s12, {"memory_id": "target", "reason": "replacement reason"}
    ) == first
    assert call(
        s12, {"memory_id": first["memory_id"], "reason": "forget event"}
    )["ok"] is False
    assert store.path.read_bytes() == before


def test_duplicate_id_collision_is_tool_error(s12, store):
    source = store.read_all()[0].source
    store.append(
        kind=s12.MemoryKind.CONVERSATION,
        memory_id=s12._retraction_id("target"),
        content="existing unrelated record",
        summary="existing unrelated record",
        source=source,
    )
    before = store.path.read_bytes()
    result = call(s12, {"memory_id": "target", "reason": "forget"})
    assert result["ok"] is False
    assert result["error"]
    assert store.path.read_bytes() == before
    assert "target" in {r.memory_id for r in store.active_records()}


def test_agent_loop_delivers_errors_and_success(s12, store, monkeypatch):
    inputs = [
        {"memory_id": "target"},
        {"memory_id": "target", "reason": "r", "source": "forged"},
        {"memory_id": "target", "reason": False},
        {"memory_id": "target", "reason": "original"},
        {"memory_id": "target", "reason": "replacement"},
    ]
    blocks = [
        SimpleNamespace(
            type="tool_use",
            id=f"tool-{index}",
            name="retract_memory",
            input=args,
        )
        for index, args in enumerate(inputs)
    ]
    responses = iter(
        [
            SimpleNamespace(stop_reason="tool_use", content=blocks),
            SimpleNamespace(stop_reason="end_turn", content=[]),
        ]
    )
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return next(responses)

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(s12, "runtime_client", lambda: (client, "offline"))
    messages = [{"role": "user", "content": "Forget target."}]
    s12.agent_loop(messages)

    assert len(requests) == 2
    results = messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == [
        block.id for block in blocks
    ]
    outputs = [json.loads(r["content"]) for r in results]
    assert [out["ok"] for out in outputs] == [
        False, False, False, True, True
    ]
    assert outputs[3] == outputs[4]
    assert outputs[4]["reason"] == "original"
    assert len(store.read_all()) == 3
