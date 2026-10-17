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


def test_torn_write_inside_multibyte_char_reports_break(s23) -> None:
    for _ in range(3):
        s23.append_audit_entry("bash", {"command": "echo 你好世界"}, "完成")
    assert s23.verify_chain() == (True, 3)
    raw = s23.audit_log_path().read_bytes().split(b"\n")[:3]
    cut = raw[2].index("你".encode("utf-8")) + 1  # died after the first byte of 你
    s23.audit_log_path().write_bytes(b"\n".join(raw[:2] + [raw[2][:cut]]))
    head = {"count": 2, "head": json.loads(raw[1].decode("utf-8"))["hash"]}
    s23.audit_head_path().write_text(json.dumps(head, sort_keys=True), encoding="utf-8")

    assert s23.verify_chain() == (False, 2)


def test_first_hash_break_precedes_invalid_utf8_tail(s23) -> None:
    lines = _three_entries(s23)
    lines[0] = lines[0].replace('"ok"', '"OK"')
    raw = b"\n".join(line.encode("utf-8") for line in lines[:2])
    s23.audit_log_path().write_bytes(raw + b'\n{"result":"\xe4')

    assert s23.verify_chain() == (False, 0)


def test_valid_chain_with_crlf_then_bad_anchor(s23) -> None:
    lines = _three_entries(s23)
    path = s23.audit_log_path()
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    assert s23.verify_chain() == (True, 3)

    head = s23.audit_head_path()
    head.write_text('{"count": 3, "head": "', encoding="utf-8")
    before = (path.read_bytes(), head.read_bytes())

    assert s23.verify_chain() == (False, 3)
    assert (path.read_bytes(), head.read_bytes()) == before


def test_empty_log_preserves_anchor_rules(s23) -> None:
    _three_entries(s23)
    path = s23.audit_log_path()
    head = s23.audit_head_path()
    path.write_bytes(b"")

    assert s23.verify_chain() == (False, 0)

    head.write_text('{"count":', encoding="utf-8")
    assert s23.verify_chain() == (False, 0)

    head.write_text(
        json.dumps({"count": 0, "head": s23.GENESIS_HASH}),
        encoding="utf-8",
    )
    assert s23.verify_chain() == (True, 0)

    head.unlink()
    assert s23.verify_chain() == (True, 0)
