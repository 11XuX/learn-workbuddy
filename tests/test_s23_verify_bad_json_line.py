"""s23: /verify must report a broken chain, not crash, on an unparsable line.

README s23 step 5 tells the reader to hand-edit today's audit JSONL and run
/verify to "观察链断裂"; README also says the log must stay trustworthy when
someone "（或 agent 自己）" edits it.  verify_chain() returns (ok, index) and
already maps a corrupt head anchor to (False, 0) on the empty-log path, so a
corrupt line or a torn final write must be (False, i), not JSONDecodeError.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def s23(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setattr(sys, "argv", ["s23_audit_sandbox/code.py"])
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    name = "hunt_s23_verify_bad_json_line"
    spec = importlib.util.spec_from_file_location(name, ROOT / "s23_audit_sandbox" / "code.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _three_entries(s23) -> list[str]:
    for i in range(3):
        s23.append_audit_entry("tool_call", {"i": i}, "ok")
    assert s23.verify_chain() == (True, 3)
    return s23.audit_log_path().read_text(encoding="utf-8").split("\n")[:3]


def test_hand_edited_middle_line_reports_break(s23) -> None:
    lines = _three_entries(s23)
    lines[1] = lines[1][:-1]  # README step 5: edit a record, drop the closing brace
    s23.audit_log_path().write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert s23.verify_chain() == (False, 1)


def test_torn_final_write_reports_break(s23) -> None:
    lines = _three_entries(s23)
    torn = lines[2][: len(lines[2]) // 2]  # process died halfway through f.write
    s23.audit_log_path().write_text("\n".join(lines[:2] + [torn]), encoding="utf-8")

    assert s23.verify_chain() == (False, 2)


def test_corrupt_head_anchor_reports_break(s23) -> None:
    _three_entries(s23)
    s23.audit_head_path().write_text('{"count": 3, "head": "', encoding="utf-8")

    assert s23.verify_chain() == (False, 3)
