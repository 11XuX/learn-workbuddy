"""s02: handler failures must reach the model as execution_error + is_error.

README s02 "### 3" / "### 7" / "### 5" and the s01->s02 table: once a call has
entered the handler, a failure (including the handler's own guard, like
safe_path) becomes ``Error [execution_error]: ...`` with ``is_error``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_s02(monkeypatch, workdir: Path):
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
    monkeypatch.setattr(module, "WORKDIR", workdir.resolve())
    return module


def dispatch(s02, name: str, arguments: dict):
    return s02.TOOL_REGISTRY.dispatch(s02.ToolCall("call_1", name, arguments))


def assert_execution_error(s02, result, detail: str) -> None:
    assert result.error_code is s02.ToolErrorCode.EXECUTION_ERROR, result.content
    assert result.content.startswith("Error [execution_error]: "), result.content
    assert detail in result.content
    assert result.to_protocol_block().get("is_error") is True


def test_edit_file_text_not_found(monkeypatch, tmp_path: Path) -> None:
    s02 = load_s02(monkeypatch, tmp_path)
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    result = dispatch(s02, "edit_file", {"path": "notes.txt", "old_text": "absent", "new_text": "x"})
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"
    assert_execution_error(s02, result, "not found")


def test_blocked_bash_command(monkeypatch, tmp_path: Path) -> None:
    s02 = load_s02(monkeypatch, tmp_path)
    assert_execution_error(s02, dispatch(s02, "bash", {"command": "sudo true"}), "blocked")


def test_bash_timeout(monkeypatch, tmp_path: Path) -> None:
    s02 = load_s02(monkeypatch, tmp_path)

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="sleep 999", timeout=120)

    monkeypatch.setattr(s02.subprocess, "run", fake_run)
    assert_execution_error(s02, dispatch(s02, "bash", {"command": "sleep 999"}), "120")


def test_bash_os_error(monkeypatch, tmp_path: Path) -> None:
    s02 = load_s02(monkeypatch, tmp_path / "missing-workdir")  # cwd missing -> OSError
    assert_execution_error(s02, dispatch(s02, "bash", {"command": "true"}), "missing-workdir")


def test_command_output_that_starts_with_error_is_still_success(monkeypatch, tmp_path: Path) -> None:
    s02 = load_s02(monkeypatch, tmp_path)
    result = dispatch(s02, "bash", {"command": "echo 'Error: just text'"})
    assert result.ok and result.content == "Error: just text"
    assert "is_error" not in result.to_protocol_block()


def test_blocked_bash_never_executes_command(monkeypatch, tmp_path: Path) -> None:
    s02 = load_s02(monkeypatch, tmp_path)
    marker = tmp_path / "executed.txt"
    result = dispatch(
        s02, "bash",
        {"command": "printf executed > executed.txt # sudo"},
    )
    assert not marker.exists()
    assert_execution_error(s02, result, "blocked")


def test_bash_exit_status_preserves_output_contract(monkeypatch, tmp_path: Path) -> None:
    s02 = load_s02(monkeypatch, tmp_path)
    cases = [
        ("printf ' out '; printf ' err ' >&2; exit 7", "out  err"),
        ("exit 7", "(no output)"),
        ("true", "(no output)"),
    ]
    for command, expected in cases:
        result = dispatch(s02, "bash", {"command": command})
        assert result.ok, result.content
        assert result.content == expected
        assert "is_error" not in result.to_protocol_block()


def test_missing_edit_never_writes_and_success_edits_once(monkeypatch, tmp_path: Path) -> None:
    s02 = load_s02(monkeypatch, tmp_path)
    target = tmp_path / "notes.txt"
    original = b"hello\nhello\n"
    target.write_bytes(original)
    writes = []
    real_write_text = Path.write_text

    def track_write(path, *args, **kwargs):
        if path == target:
            writes.append(path)
        return real_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", track_write)
    result = dispatch(
        s02, "edit_file",
        {"path": "notes.txt", "old_text": "absent", "new_text": "x"},
    )
    assert writes == []
    assert target.read_bytes() == original
    assert_execution_error(s02, result, "not found")

    result = dispatch(
        s02, "edit_file",
        {"path": "notes.txt", "old_text": "hello", "new_text": "changed"},
    )
    assert result.ok, result.content
    assert "is_error" not in result.to_protocol_block()
    assert target.read_bytes() == b"changed\nhello\n"
