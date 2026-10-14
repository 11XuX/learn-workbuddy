"""Offline /retract-demo: retraction leaves the active view, not the audit stream.

Locks relations between lines, not concrete seed ids.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s12_cloud_memory" / "code.py"
MODULE = "s12_retract_demo_test_module"


@pytest.fixture
def s12(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
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


def _ids(line: str) -> list[str]:
    tail = line.split(": ", 1)[1]
    return [] if tail == "(none)" else tail.split(", ")


def _counts(line: str) -> tuple[int, int]:
    m = re.search(r"read_all=(\d+) active=(\d+)", line)
    assert m, line
    return int(m.group(1)), int(m.group(2))


def _check_demo(lines: list[str]) -> None:
    by_tag: dict[str, list[str]] = {}
    for line in lines:
        by_tag.setdefault(line.split("]", 1)[0] + "]", []).append(line)
    before = _ids(by_tag["[before]"][0])
    assert len(before) >= 2
    target = before[0]
    assert by_tag["[harness]"][0].startswith(f"[harness] retract {target} -> ")
    after = _ids(by_tag["[after]"][0])
    assert after == [i for i in before if i != target]
    assert _ids(by_tag["[restart]"][0]) == after
    read_before, active_before = _counts(by_tag["[before]"][1])
    read_after, active_after = _counts(by_tag["[after]"][1])
    assert (read_after, active_after) == (read_before + 1, active_before - 1)
    assert by_tag["[after]"][1].endswith(f"{target} still in read_all: True")
    rejects = by_tag["[reject]"]
    assert len(rejects) == 2
    assert all(line.endswith(": rejected") for line in rejects)


def _tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): (p.read_bytes() if p.is_file() else b"<dir>")
        for p in sorted(root.rglob("*"))
    }


def test_repl_retract_demo_offline_keeps_workbuddy_home_unchanged(tmp_path):
    home = tmp_path / "home"
    (home / "keep").mkdir(parents=True)
    (home / "keep" / "note.txt").write_bytes(b"unchanged")
    snapshot = _tree(home)
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env |= {"MODEL_ID": "offline", "WORKBUDDY_HOME": str(home)}
    proc = subprocess.run(
        [sys.executable, str(CODE)], input="/retract-demo\nq\n",
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "retract-demo failed" not in proc.stdout
    lines = [line.split("s12 >> ")[-1] for line in proc.stdout.splitlines()]
    _check_demo([line for line in lines if line.startswith("[")])
    assert _tree(home) == snapshot


def test_demo_never_touches_default_runtime(s12, monkeypatch):
    def boom():
        raise AssertionError("default_runtime must not be used")

    monkeypatch.setattr(s12, "default_runtime", boom)
    monkeypatch.setattr(s12, "create_default_store", boom)
    _check_demo(s12.run_retract_demo())


def test_demo_output_is_deterministic(s12):
    assert s12.run_retract_demo() == s12.run_retract_demo()


def test_demo_error_propagates_and_temp_dir_is_cleaned(s12, monkeypatch):
    seen: list[Path] = []

    def failing_retract(self, target_id, **kwargs):
        seen.append(self.path.parent)
        raise RuntimeError("injected retract failure")

    monkeypatch.setattr(s12.RemoteMemoryStore, "retract", failing_retract)
    with pytest.raises(RuntimeError, match="injected retract failure"):
        s12.run_retract_demo()
    assert len(seen) == 1
    assert not seen[0].exists()
