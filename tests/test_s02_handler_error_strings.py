"""s02: handler failures reported as plain "Error: ..." text must still be is_error.

README (s02 "### 3" and the s01/s02 comparison table) says execution failures
get a stable ToolErrorCode plus ``is_error`` instead of a vague ``Error: ...``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_s02(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    saved = sys.modules.pop("anthropic", None)
    name = "s02_handler_error_strings_module"
    spec = importlib.util.spec_from_file_location(name, ROOT / "s02_tool_dispatch" / "code.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved
    return module


def test_edit_file_text_not_found_is_an_execution_error(monkeypatch, tmp_path: Path) -> None:
    s02 = load_s02(monkeypatch)
    monkeypatch.setattr(s02, "WORKDIR", tmp_path.resolve())
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    result = s02.TOOL_REGISTRY.dispatch(
        s02.ToolCall("call_1", "edit_file", {"path": "notes.txt", "old_text": "absent", "new_text": "x"})
    )

    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"
    assert result.error_code is s02.ToolErrorCode.EXECUTION_ERROR, result.content
    assert result.to_protocol_block().get("is_error") is True


def test_blocked_bash_command_is_an_execution_error(monkeypatch, tmp_path: Path) -> None:
    s02 = load_s02(monkeypatch)
    monkeypatch.setattr(s02, "WORKDIR", tmp_path.resolve())

    result = s02.TOOL_REGISTRY.dispatch(s02.ToolCall("call_2", "bash", {"command": "sudo true"}))

    assert "blocked" in result.content
    assert result.error_code is s02.ToolErrorCode.EXECUTION_ERROR, result.content
    assert result.to_protocol_block().get("is_error") is True
