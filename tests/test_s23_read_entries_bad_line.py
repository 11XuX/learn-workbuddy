"""s23: /audit and /chain must not crash on a bad audit log line.

README s23 step 5 asks readers to hand-edit today's .jsonl and run /verify;
README :52 says someone "（或 agent 自己）" may edit the log, and the harness's
own append can leave a half line if killed.  verify_chain() already treats such
lines as tampering (fork #50 / upstream `PR 67`), but read_today_entries(),
used by /audit and /chain, still calls json.loads on every line.  A bad line
must not hide the readable entries or crash the REPL.
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
    "truncated_json": b'{"action": "tool_call", "par',
    "half_chinese_char": '{"action": "中'.encode("utf-8")[:-1],
    "non_object_number": b"123",
    "non_object_list": b"[1]",
}


@pytest.mark.parametrize("bad", list(BAD_LINES.values()), ids=list(BAD_LINES))
def test_audit_and_chain_survive_bad_line(s23, capsys, bad: bytes) -> None:
    s23.append_audit_entry("first", {"i": 1}, "ok")
    s23.append_audit_entry("second", {"i": 2}, "ok")
    path = s23.audit_log_path()
    lines = path.read_bytes().strip().split(b"\n")
    path.write_bytes(lines[0] + b"\n" + bad + b"\n" + lines[1] + b"\n")

    entries = s23.read_today_entries()
    assert all(isinstance(e, dict) for e in entries)
    actions = [e.get("action") for e in entries]
    assert "first" in actions and "second" in actions
    assert actions.index("first") < actions.index("second")

    s23.print_audit()
    s23.print_chain_detail()
    out = capsys.readouterr().out
    assert "first" in out and "second" in out
