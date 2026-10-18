"""s04: a handler that fails must end as FAILED, not SUCCEEDED.

README s04 :43 / :107-110 / :247: handler failures stay apart from successes,
become is_error tool_results and audit outcome=failed, with the reason kept for
the model. A normal result that starts with "Error" stays SUCCEEDED (no prefix
sniffing). run_bash is out of scope (test_permission_gates.py pins its text).
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
    (workdir / "notes.txt").write_text("Error: hello\n", encoding="utf-8")
    (workdir / "folder").mkdir()
    audit = module.AuditTrail()
    runner = module.GovernedToolRunner(
        policy=module.PermissionPolicy(module.WorkspaceScope(workdir)),
        handlers=module.TOOL_HANDLERS,
        approver=lambda _decision: True,
        hooks=module.build_hooks(audit),
    )
    return module, runner, audit


CASES = [
    # (tool, arguments that pass policy but fail in the handler, reason, approval)
    ("read_file", {"path": "missing.txt"}, "No such file", "allowed"),
    ("edit_file", {"path": "notes.txt", "old_text": "absent", "new_text": "x"},
     "text not found in notes.txt", "approved"),
    ("write_file", {"path": "folder", "content": "x"}, "Is a directory", "approved"),
]


@pytest.mark.parametrize(("tool", "arguments", "reason", "approval"), CASES, ids=[c[0] for c in CASES])
def test_failed_handler_is_recorded_as_failed(monkeypatch, tmp_path: Path, tool, arguments, reason, approval) -> None:
    s04, runner, audit = load_s04(monkeypatch, tmp_path)
    result = runner.run(s04.ToolRequest("call_1", tool, arguments))

    assert [r.outcome for r in audit.records if r.event == "permission"] == [approval]
    assert result.status is s04.ToolExecutionStatus.FAILED, result.content
    assert result.to_protocol_block().get("is_error") is True
    assert audit.records[-1].event == "result" and audit.records[-1].outcome == "failed"
    assert result.content.startswith(f"Execution failed for {tool}: "), result.content
    assert reason in result.content  # the model still learns why it failed
    if tool == "edit_file":  # a failed edit must not touch the file
        assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "Error: hello\n"


def test_normal_text_starting_with_error_is_still_succeeded(monkeypatch, tmp_path: Path) -> None:
    s04, runner, audit = load_s04(monkeypatch, tmp_path)
    result = runner.run(s04.ToolRequest("call_1", "read_file", {"path": "notes.txt"}))

    assert result.status is s04.ToolExecutionStatus.SUCCEEDED, result.content
    assert result.content == "Error: hello"
    assert "is_error" not in result.to_protocol_block()
    assert audit.records[-1].outcome == "succeeded"


def test_invalid_utf8_is_failed_and_post_hook_runs(monkeypatch, tmp_path: Path) -> None:
    s04, runner, audit = load_s04(monkeypatch, tmp_path)
    target = tmp_path / "binary.txt"
    original = b"\xff\xfe\x00"
    target.write_bytes(original)
    seen = []
    runner.hooks.register("PostToolUse", seen.append)

    result = runner.run(s04.ToolRequest(
        "binary_read", "read_file", {"path": "binary.txt"}
    ))

    assert result.status is s04.ToolExecutionStatus.FAILED
    assert result.to_protocol_block()["is_error"] is True
    assert result.content.startswith("Execution failed for read_file: ")
    assert "utf-8" in result.content and "decode" in result.content
    assert seen == [result]
    assert audit.records[-1].event == "result"
    assert audit.records[-1].outcome == "failed"
    assert target.read_bytes() == original


def test_success_text_limit_and_first_edit_are_preserved(monkeypatch, tmp_path: Path) -> None:
    s04, runner, audit = load_s04(monkeypatch, tmp_path)

    def run(name, arguments):
        result = runner.run(s04.ToolRequest("success", name, arguments))
        assert result.status is s04.ToolExecutionStatus.SUCCEEDED, result.content
        assert "is_error" not in result.to_protocol_block()
        assert audit.records[-1].event == "result"
        assert audit.records[-1].outcome == "succeeded"
        return result.content

    content = "one\none\nthree\n"
    assert run("write_file", {
        "path": "new/lines.txt", "content": content
    }) == f"Wrote {len(content)} chars to new/lines.txt"
    assert run("read_file", {
        "path": "new/lines.txt", "limit": 1
    }) == "one\n... (2 more lines)"
    assert run("read_file", {
        "path": "new/lines.txt", "limit": 0
    }) == "one\none\nthree"
    assert run("edit_file", {
        "path": "new/lines.txt", "old_text": "one", "new_text": "changed"
    }) == "Edited new/lines.txt"
    assert (tmp_path / "new/lines.txt").read_text(
        encoding="utf-8"
    ) == "changed\none\nthree\n"


def test_outside_write_is_blocked_before_handler_or_approval(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    s04, runner, audit = load_s04(monkeypatch, workspace)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")

    def unexpected(*_args, **_kwargs):
        pytest.fail("outside request reached approval or handler")

    runner.approver = unexpected
    runner.handlers["write_file"] = unexpected
    result = runner.run(s04.ToolRequest(
        "outside_write", "write_file",
        {"path": "../outside.txt", "content": "destroy"},
    ))

    assert result.status is s04.ToolExecutionStatus.BLOCKED
    assert result.to_protocol_block()["is_error"] is True
    assert result.permission.decision.rule_id == "path.outside_workspace"
    assert [r.outcome for r in audit.records if r.event == "permission"] == ["denied"]
    assert audit.records[-1].event == "result"
    assert audit.records[-1].outcome == "blocked"
    assert outside.read_text(encoding="utf-8") == "keep"
