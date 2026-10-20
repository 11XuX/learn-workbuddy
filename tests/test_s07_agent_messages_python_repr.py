"""Reproduce: /agent/messages stringifies list content with Python repr.

HTTP GET /agent/messages only wraps transport_messages() (code.py :266-267);
this card locks that view. Acceptance (b): list/SDK content -> json.dumps str.
"""

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
    """List content must be JSON text in the view, not Python repr."""

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.type, self.text = "text", text

        def __repr__(self) -> str:
            return f"TextBlock(type='text', text={self.text!r})"

    s07 = load_s07(monkeypatch)
    mgr = s07.SessionManager()
    rt = mgr.get_session(mgr.create_session(str(tmp_path)))
    assert rt is not None
    # Same shapes _call_provider writes: TextBlock list + tool_results list.
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
    assert view[0]["content"] == "hi"
    assert "TextBlock" not in view[1]["content"]
    assert json.loads(view[1]["content"])[0]["text"] == "hello from model"
    tool = json.loads(view[2]["content"])
    assert tool[0]["tool_use_id"] == "tu_1" and tool[0]["content"] == "ok"
    mgr.shutdown_all()


def test_transport_messages_preserves_blocks_and_tool_fields(
    monkeypatch, tmp_path: Path
) -> None:
    class TextBlock:
        def __init__(self, text: str) -> None:
            self.type = "text"
            self.text = text

    s07 = load_s07(monkeypatch)
    mgr = s07.SessionManager()
    try:
        rt = mgr.get_session(mgr.create_session(str(tmp_path)))
        assert rt is not None
        tool = {
            "type": "tool_result",
            "tool_use_id": "tu_中文",
            "content": "完成\n含有 '单引号' 和 \"双引号\"",
            "is_error": False,
        }
        messages = [
            {"role": "assistant", "content": [TextBlock("你好"), TextBlock("世界")]},
            {"role": "user", "content": [tool]},
        ]
        rt.record.messages = messages
        view = rt.transport_messages()
        assert isinstance(view, list)
        assert all(isinstance(row, dict) for row in view)
        assert all(isinstance(value, str) for row in view for value in row.values())
        assert json.loads(view[0]["content"]) == [
            {"type": "text", "text": "你好"},
            {"type": "text", "text": "世界"},
        ]
        assert json.loads(view[1]["content"]) == [tool]
        assert rt.record.messages is messages
        assert messages[0]["content"][0].text == "你好"
        assert messages[1]["content"][0] == tool
    finally:
        mgr.shutdown_all()


def test_transport_messages_preserves_strings_roles_and_empty_list(
    monkeypatch, tmp_path: Path
) -> None:
    s07 = load_s07(monkeypatch)
    mgr = s07.SessionManager()
    try:
        rt = mgr.get_session(mgr.create_session(str(tmp_path)))
        assert rt is not None
        contents = ["hi", '[{"text":"中文"}]', "", []]
        rt.record.messages = [
            {"role": role, "content": content}
            for role, content in zip(["user", "assistant", None, 7], contents)
        ] + [{}]
        assert rt.transport_messages() == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": contents[1]},
            {"role": "None", "content": ""},
            {"role": "7", "content": "[]"},
            {"role": "", "content": ""},
        ]
    finally:
        mgr.shutdown_all()
