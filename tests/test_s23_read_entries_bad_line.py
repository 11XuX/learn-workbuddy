"""s23: /audit and /chain must not crash on, or silently drop, a bad log line.

README s23 step 5 asks readers to hand-edit today's .jsonl and keep using the
REPL; a torn final write is the other path upstream `PR 67` already tests.
verify_chain() treats an unreadable line as tampering evidence and counts it:
(False, i) -> print_verify says entry i+1 was tampered.  read_today_entries()
(used by /audit and /chain) still calls json.loads on every line, so it
crashes; and if it just skipped the line, /audit's numbering would no longer
match /verify's.  So: no crash, one dict per non-empty line, order kept.
Out of scope: non-object JSON lines, and append_audit_entry after a
half-character line (its fail-open vs fail-closed choice is a separate card).
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
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    name = "hunt_s23_read_entries_bad_line"
    spec = importlib.util.spec_from_file_location(name, ROOT / "s23_audit_sandbox" / "code.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


BAD_LINES = {
    "truncated_json": b'{"action": "tool_call", "par',  # hand edit slips
    "half_chinese_char": '{"action": "中'.encode("utf-8")[:-1],  # torn write
}


@pytest.mark.parametrize("bad", list(BAD_LINES.values()), ids=list(BAD_LINES))
def test_audit_and_chain_show_bad_line_without_crash(s23, capsys, bad: bytes) -> None:
    s23.append_audit_entry("first", {"i": 1}, "ok")
    s23.append_audit_entry("second", {"i": 2}, "ok")
    path = s23.audit_log_path()
    lines = path.read_bytes().strip().split(b"\n")
    path.write_bytes(lines[0] + b"\n" + bad + b"\n" + lines[1] + b"\n")
    assert s23.verify_chain() == (False, 1)  # /verify: entry 2 tampered

    entries = s23.read_today_entries()
    assert all(isinstance(e, dict) for e in entries)
    actions = [e.get("action") for e in entries]
    assert len(actions) == 3, "bad line must keep its slot, not vanish"
    assert actions[0] == "first" and actions[2] == "second"

    s23.print_audit()
    s23.print_chain_detail()
    out = capsys.readouterr().out
    assert "first" in out and "second" in out and "共 3 条记录" in out
