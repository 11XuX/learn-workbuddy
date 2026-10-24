"""s23: SANDBOX_CONFIG["blocked_paths"] is declared but never checked.

README s23 「沙盒检查流程」 step 2 ("检查路径访问") rejects any command that
mentions a blocked path, and the walkthrough says code.py does
「沙盒配置解析 — 路径/命令/环境变量检查」. code.py declares
``blocked_paths = ["/etc", "/var", "/System", "/usr"]`` but
classify_safety()/check_sandbox() never read it, so a model tool call
``bash {"command": "cat /etc/hostname"}`` is classified SAFE and executed.
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
    name = "hunt_s23_sandbox_blocked_paths"
    spec = importlib.util.spec_from_file_location(name, ROOT / "s23_audit_sandbox" / "code.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


@pytest.mark.parametrize("command", ["cat /etc/hostname", "ls /var/log", "ls /usr/bin"])
def test_blocked_path_is_rejected(s23, command):
    allowed, reason = s23.check_sandbox(command)
    assert not allowed, f"{command!r} allowed: {reason}"


def test_model_tool_call_on_blocked_path_is_not_executed(s23):
    out = s23.run_with_sandbox("cat /etc/hostname")
    assert out.startswith("Error:"), out
    actions = [e["action"] for e in s23.read_today_entries()]
    assert actions == ["sandbox_block"], actions


def test_ordinary_read_still_allowed(s23):
    assert s23.check_sandbox("cat README.md")[0]
