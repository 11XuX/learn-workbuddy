"""s04: a handler that fails must end as FAILED, not SUCCEEDED.

README s04 "ToolExecutionResult" / "失败路径": FAILED covers a handler that
fails, BLOCKED/FAILED become error tool_results (is_error), and the audit
keeps failures apart from successes. But run_read / run_write / run_edit
catch their own errors and return "Error: ..." text, so GovernedToolRunner
records SUCCEEDED, the tool_result has no is_error, and the audit trail says
outcome=succeeded. (run_bash is out of scope: test_permission_gates.py pins
its "Error: spawn failed" text.)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_s04(monkeypatch, workdir: Path):
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    saved = sys.modules.pop("anthropic", None)
    name = "s04_handler_error_strings_module"
    spec = importlib.util.spec_from_file_location(name, ROOT / "s04_permission_hooks" / "code.py")
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


CASES = [
    # (tool, arguments) that pass policy but fail inside the handler
    ("read_file", {"path": "missing.txt"}),
    ("edit_file", {"path": "notes.txt", "old_text": "absent", "new_text": "x"}),
    ("write_file", {"path": "folder", "content": "x"}),
]


@pytest.mark.parametrize(("tool", "arguments"), CASES, ids=[c[0] for c in CASES])
def test_failed_handler_is_recorded_as_failed(monkeypatch, tmp_path: Path, tool, arguments) -> None:
    s04 = load_s04(monkeypatch, tmp_path)
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    audit = s04.AuditTrail()
    runner = s04.GovernedToolRunner(
        policy=s04.PermissionPolicy(s04.WorkspaceScope(tmp_path)),
        handlers=s04.TOOL_HANDLERS,
        approver=lambda _decision: True,
        hooks=s04.build_hooks(audit),
    )

    result = runner.run(s04.ToolRequest("call_1", tool, arguments))

    assert result.permission.allowed is True  # policy let it through
    assert result.status is s04.ToolExecutionStatus.FAILED, result.content
    assert result.to_protocol_block().get("is_error") is True
    assert audit.records[-1].outcome == "failed"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"
