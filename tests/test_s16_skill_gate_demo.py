"""Offline /skill-gate-demo: P1 pending skills cannot trigger until the user approves."""

from __future__ import annotations

import builtins
import importlib.util
import os
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s16_skills_system" / "code.py"
MODULE = "s16_skill_gate_demo_test_module"


@pytest.fixture
def s16(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    spec = importlib.util.spec_from_file_location(MODULE, CODE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[MODULE] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(MODULE, None)
        sys.modules.pop("anthropic", None)


def _state(s16):
    return (
        [(s.title, s.loaded) for s in s16.skill_index],
        [s.title for s in s16.loaded_skills],
        dict(s16.pending_skills),
        dict(s16.SEED_SKILLS),
        (id(s16.skill_index), id(s16.loaded_skills), id(s16.pending_skills), id(s16.SEED_SKILLS)),
    )


def _check_lines(lines):
    text = "\n".join(lines)
    assert "[pending] match_skill('拉取接口') -> None" in lines
    assert "[approved] match_skill('拉取接口') -> gate-demo-fetch" in lines
    assert lines.index("[pending] match_skill('拉取接口') -> None") < lines.index(
        "[approved] match_skill('拉取接口') -> gate-demo-fetch")
    assert "技能 'gate-demo-notes' 已创建。" in lines
    assert "拒绝创建技能 'gate-demo-wipe'" in text
    assert "[P0] gate-demo-wipe in pending_skills: False" in lines


def test_repl_demo_runs_without_key_and_leaves_no_gate_demo_skills():
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"} | {"MODEL_ID": "offline"}
    proc = subprocess.run(
        [sys.executable, str(CODE)], input="/skill-gate-demo\nskills\nq\n",
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    lines = [line.split("\x1b[0m")[-1] for line in out.splitlines()]
    _check_lines(lines)
    after_skills = out.split("技能名", 1)[1]
    assert "gate-demo-" not in after_skills


def test_demo_restores_module_state(s16):
    s16.load_skill("git-commit")
    before = _state(s16)
    _check_lines(s16.run_skill_gate_demo())
    assert _state(s16) == before


def test_demo_restores_state_when_approve_raises(s16, monkeypatch):
    before = _state(s16)

    def boom(title):
        raise RuntimeError("injected approve failure")

    monkeypatch.setattr(s16, "approve_skill", boom)
    with pytest.raises(RuntimeError, match="injected approve failure"):
        s16.run_skill_gate_demo()
    assert _state(s16) == before


def test_demo_never_writes_files_or_calls_the_model(s16, monkeypatch):
    def no_write(*args, **kwargs):
        raise AssertionError("demo must not write files")

    real_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            raise AssertionError("demo must not open files for writing")
        return real_open(file, mode, *args, **kwargs)

    class NoModel:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise AssertionError("demo must not call the model")

    monkeypatch.setattr(pathlib.Path, "write_text", no_write)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(s16, "client", NoModel())
    _check_lines(s16.run_skill_gate_demo())
