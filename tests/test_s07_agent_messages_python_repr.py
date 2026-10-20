"""Reproduce: /agent/messages stringifies list content with Python repr."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load_s07(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    saved = sys.modules.pop("anthropic", None)
    name = "s07_agent_messages_repr_module"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "s07_session_management" / "code.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved

    class FakeHTTP:
        n = 42_000

        def __init__(self, _addr, session):
            type(self).n += 1
            self.session = session
            self.server_address = ("127.0.0.1", type(self).n)

        def serve_forever(self): return None
        def shutdown(self): return None
        def server_close(self): return None

    monkeypatch.setattr(mod, "SessionHTTPServer", FakeHTTP)
    return mod

def test_transport_messages_not_python_repr(monkeypatch, tmp_path: Path) -> None:
    """List content from the agent loop must stay a JSON-safe transcript view."""

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.type, self.text = "text", text

        def __repr__(self) -> str:
            return f"TextBlock(type='text', text={self.text!r})"

    s07 = load_s07(monkeypatch)
    mgr = s07.SessionManager()
    rt = mgr.get_session(mgr.create_session(str(tmp_path)))
    assert rt is not None
    # Mirrors _call_provider: response.content list + tool_results list.
    rt.record.messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [TextBlock("hello from model")]},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
            ],
        },
    ]
    view = rt.transport_messages()
    assert "TextBlock" not in view[1]["content"]
    tool = view[2]["content"]
    if tool.lstrip().startswith("["):
        json.loads(tool)
    mgr.shutdown_all()
