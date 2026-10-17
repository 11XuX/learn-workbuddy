"""s23: /verify must report a broken chain, not crash, on an unparsable line.

tests/test_chapter_security_parity.py keeps chapter audit code at parity with
mini_workbuddy, whose verify() returns False on a corrupt line (see
docs/code-quality-review.md); README s23 says someone "（或 agent 自己）" may
edit the log.  verify_chain() returns (ok, i): i is the first entry that is not
trusted (= entries verified before it; len(entries) for a bad head anchor).
So a bad line or anchor must give (False, i), not JSONDecodeError.
"""

from __future__ import annotations

import importlib.util
import json
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
    lines[1] = lines[1][:-1]  # hand edit slips: drop the closing brace
    s23.audit_log_path().write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert s23.verify_chain() == (False, 1)


def test_torn_final_write_reports_break(s23) -> None:
    lines = _three_entries(s23)
    torn = lines[2][: len(lines[2]) // 2]  # process died halfway through f.write
    s23.audit_log_path().write_text("\n".join(lines[:2] + [torn]), encoding="utf-8")
    head = {"count": 2, "head": json.loads(lines[1])["hash"]}  # head not rewritten yet
    s23.audit_head_path().write_text(json.dumps(head, sort_keys=True), encoding="utf-8")

    assert s23.verify_chain() == (False, 2)


def test_corrupt_head_anchor_reports_break(s23) -> None:
    _three_entries(s23)
    s23.audit_head_path().write_text('{"count": 3, "head": "', encoding="utf-8")

    assert s23.verify_chain() == (False, 3)


def test_tampered_entry_before_torn_tail_reports_first_break(s23) -> None:
    lines = _three_entries(s23)
    lines[0] = lines[0].replace('"ok"', '"OK"')  # README step 5: change a value
    torn = lines[2][: len(lines[2]) // 2]
    s23.audit_log_path().write_text("\n".join(lines[:2] + [torn]), encoding="utf-8")

    assert s23.verify_chain() == (False, 0)  # entry 0 is the first untrusted one
