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


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeMessages:
    def __init__(self):
        call = _Block(type="tool_use", id="t1", name="bash",
                      input={"command": "cat /etc/hostname"})
        done = _Block(type="text", text="done")
        self.replies = [_Block(stop_reason="tool_use", content=[call]),
                        _Block(stop_reason="end_turn", content=[done])]

    def create(self, **kwargs):
        return self.replies.pop(0)


def test_model_tool_call_on_blocked_path_is_not_executed(s23, monkeypatch):
    monkeypatch.setattr(s23, "client", _Block(messages=_FakeMessages()))
    messages = [{"role": "user", "content": "show hostname"}]
    s23.agent_loop(messages)
    out = messages[2]["content"][0]["content"]
    assert out.startswith("Error:"), out
    actions = [e["action"] for e in s23.read_today_entries()]
    assert actions == ["sandbox_block"], actions


def test_ordinary_read_still_allowed(s23):
    assert s23.check_sandbox("cat README.md")[0]


@pytest.mark.parametrize("command", [
    "cat</etc/hostname",
    "ls /etc|wc -l",
])
def test_verifier_shell_boundary_tool_call_is_blocked(
    s23, monkeypatch, command
):
    def unexpected_execution(*args, **kwargs):
        pytest.fail("Blocked command reached subprocess.run")

    monkeypatch.setattr(s23.subprocess, "run", unexpected_execution)
    replies = _FakeMessages()
    replies.replies[0].content[0].input = {"command": command}
    monkeypatch.setattr(s23, "client", _Block(messages=replies))

    messages = [{"role": "user", "content": "inspect system path"}]
    s23.agent_loop(messages)

    result = messages[2]["content"][0]["content"]
    assert result.startswith("Error: BLOCKED: Touches blocked path /etc")
    entries = s23.read_today_entries()
    assert [entry["action"] for entry in entries] == ["sandbox_block"]
    assert entries[0]["params"]["command"] == command


@pytest.mark.parametrize("command", [
    "cat /tmp/usrdata.txt",
    "ls ~/variables",
    "cat ./etc/x",
    "cat /etcetera.txt",
])
def test_verifier_unrelated_paths_remain_allowed(s23, command):
    allowed, reason = s23.check_sandbox(command)
    assert allowed, f"{command!r} unexpectedly blocked: {reason}"
