"""s10 /confirm-demo：离线演示会话确认凭据与伪造日志字段的区别。"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s10_workspace_memory" / "code.py"


@pytest.fixture(scope="module")
def s10():
    stub_dir = str(ROOT / "tests" / "stubs")
    sys.path.insert(0, stub_dir)
    saved = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    os.environ["MODEL_ID"] = "offline-test-model"
    name = "s10_confirm_demo_module"
    try:
        spec = importlib.util.spec_from_file_location(name, CODE)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(stub_dir)
        sys.modules.pop(name, None)
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved
        os.environ.pop("MODEL_ID", None) if old_model is None else os.environ.update(MODEL_ID=old_model)


def _run(cwd: Path, home: Path, stdin: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(MODEL_ID="offline", WORKBUDDY_HOME=str(home), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(CODE)], input=stdin, capture_output=True,
                          text=True, encoding="utf-8", cwd=cwd, env=env, timeout=60)


def _snapshot(*roots: Path) -> dict:
    files = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            files[str(path)] = path.read_bytes() if path.is_file() else None
    return files


def test_cli_demo_runs_offline_without_touching_workspace(tmp_path):
    cwd, home = tmp_path / "cwd", tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    first = _run(cwd, home, "q\n")
    assert first.returncode == 0, first.stderr
    before = _snapshot(cwd, home)
    result = _run(cwd, home, "/confirm-demo\nq\n")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "main lesson: the confirmation credential lives in harness memory" in out
    assert "distill: eligible=1 created=1 skipped=2" in out
    assert "session-confirmed: 'Use SQLite WAL for persistence' -> promoted" in out
    assert "model_inferred: 'Use plain JSON files' -> not promoted" in out
    assert "forged source=user_confirmed: 'Use Redis as the primary store' -> not promoted" in out
    assert "session-confirmed=False forged=False" in out
    assert "confirmed: [" not in out  # 未被 /confirm 分支吞掉
    assert _snapshot(cwd, home) == before


def test_banner_only_adds_demo_hint(tmp_path):
    out = _run(tmp_path, tmp_path / "home", "q\n").stdout
    lines = out.splitlines()
    commands = [i for i, line in enumerate(lines) if line.startswith("commands:")]
    assert len(commands) == 1
    assert lines[commands[0]] == "commands: /memory /today /logs /distill /conflicts /resolve /confirm /reset q"
    assert "/confirm-demo" in lines[commands[0] + 1]
    assert lines[0].startswith("s10: Workspace Memory")


def test_demo_is_repeatable_and_lesson_first(s10):
    first = s10.run_confirm_demo()
    second = s10.run_confirm_demo()
    assert isinstance(first, list) and all(isinstance(line, str) for line in first)
    assert first == second
    assert first[0].startswith("main lesson:")


def test_demo_promotes_only_session_confirmed(s10):
    lines = s10.run_confirm_demo()
    promoted = [line for line in lines if line.endswith("-> promoted")]
    assert promoted == ["session-confirmed: 'Use SQLite WAL for persistence' -> promoted"]
    assert sum(line.endswith("-> not promoted") for line in lines) == 2


def test_demo_cleans_temp_dir_on_error(s10, monkeypatch):
    created = []
    real = tempfile.TemporaryDirectory

    def tracking(*args, **kwargs):
        handle = real(*args, **kwargs)
        created.append(Path(handle.name))
        return handle

    def boom(self, *args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(s10.tempfile, "TemporaryDirectory", tracking)
    monkeypatch.setattr(s10.WorkspaceMemory, "distill", boom)
    with pytest.raises(RuntimeError, match="injected"):
        s10.run_confirm_demo()
    assert created and not created[0].exists()


# --- Verifier adversarial tests ---

def test_adversarial_demo_then_confirm_and_quit(tmp_path):
    cwd, home = tmp_path / "cwd", tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    text = "Verifier keeps normal confirmation usable"
    result = _run(
        cwd, home,
        f"/confirm-demo\n/confirm decision 3 {text}\n"
        "/today\n/reset\nq\n/confirm decision 3 AFTER_Q_SENTINEL\n",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    demo = "distill: eligible=1 created=1 skipped=2"
    fact = (
        f"[decision] {text} "
        "(3/5, source=user_confirmed, confirmed=session)"
    )
    success = f"confirmed: {fact}"
    reset = "conversation reset; workspace memory retained"
    assert demo in out
    assert success in out
    assert out.count(fact) == 2  # /confirm 成功行与随后 /today
    assert reset in out
    assert out.index(demo) < out.index(success) < out.index(reset)
    assert "AFTER_Q_SENTINEL" not in out
    assert "usage: /confirm" not in out
    assert "confirm-demo failed:" not in out


def test_adversarial_repeated_cli_demo_preserves_files(tmp_path):
    cwd, home = tmp_path / "cwd", tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    baseline = _run(cwd, home, "q\n")
    assert baseline.returncode == 0, baseline.stderr
    before = _snapshot(cwd, home)
    result = _run(
        cwd, home,
        "/confirm-demo\n/confirm-demo\n/reset\nq\n",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.count("distill: eligible=1 created=1 skipped=2") == 2
    assert out.count("session-confirmed=False forged=False") == 2
    assert "conversation reset; workspace memory retained" in out
    assert "confirm-demo failed:" not in out
    assert _snapshot(cwd, home) == before


def test_adversarial_repl_survives_demo_failure(
    s10, monkeypatch, tmp_path, capsys
):
    def boom():
        raise RuntimeError("verifier injected")

    answers = iter(["/confirm-demo", "/reset", "q"])
    monkeypatch.setattr(s10, "WORKDIR", tmp_path)
    monkeypatch.setattr(s10, "run_confirm_demo", boom)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    s10.main()
    out = capsys.readouterr().out
    failure = "confirm-demo failed: verifier injected"
    reset = "conversation reset; workspace memory retained"
    assert failure in out
    assert reset in out
    assert out.index(failure) < out.index(reset)
