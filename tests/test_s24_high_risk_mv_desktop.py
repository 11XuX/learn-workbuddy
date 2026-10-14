"""Reproduce: s24 HIGH_RISK zones only gate ``rm``, not ``mv``.

s23 classifies ``mv … Desktop …`` as HIGH_RISK and blocks it. s24's
``classify_command`` (labeled ``s23:``) only pairs zone names with ``rm``,
so the same command falls through as DESTRUCTIVE and ``run_bash`` executes it.
System prompt still says Desktop/Downloads/Documents are HIGH-RISK zones.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = "s24_high_risk_mv_desktop_repro"


def _load(name: str, home: Path):
    stub = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub))
    saved = sys.modules.pop("anthropic", None)
    prev = {k: os.environ.get(k) for k in ("MODEL_ID", "WORKBUDDY_HOME")}
    os.environ.update({"MODEL_ID": "offline-test-model", "WORKBUDDY_HOME": str(home)})
    mod_name = f"{MODULE}_{name}"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, ROOT / name / "code.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod, mod_name
    finally:
        sys.path.remove(str(stub))
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved
        for k, v in prev.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture
def chapters(tmp_path):
    s23, n23 = _load("s23_audit_sandbox", tmp_path / "s23")
    s24, n24 = _load("s24_comprehensive", tmp_path / "s24")
    try:
        yield s23, s24
    finally:
        sys.modules.pop(n23, None)
        sys.modules.pop(n24, None)


def test_mv_desktop_is_high_risk_blocked_like_s23(chapters, tmp_path, monkeypatch):
    s23, s24 = chapters
    calls = []
    monkeypatch.setattr(s24.subprocess, "run", lambda *a, **k: calls.append(a))
    cmd = f"mv {tmp_path / 'Desktop' / 'a.txt'} /tmp/a.txt"
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / "a.txt").write_text("x", encoding="utf-8")
    assert s23.classify_safety(cmd) == "HIGH_RISK"
    assert s23.check_sandbox(cmd)[0] is False
    assert s24.classify_command(cmd) == "HIGH_RISK"
    out = s24.run_bash(cmd)
    assert out.startswith("Error: HIGH_RISK"), out
    assert calls == []

@pytest.mark.parametrize("zone", ["Desktop", "Downloads", "Documents"])
@pytest.mark.parametrize("operation", ["mv", "delete"])
def test_adversarial_personal_zone_operations(chapters, tmp_path, monkeypatch,
                                             zone, operation):
    s23, s24 = chapters
    calls = []
    monkeypatch.setattr(s24.subprocess, "run", lambda *a, **k: calls.append(a))
    path = tmp_path / zone
    cmd = (f"mv {path / 'a.txt'} /tmp/a.txt" if operation == "mv"
           else f"find {path} -type f -delete")
    out = s24.run_bash(cmd)
    assert calls == []
    assert out.startswith("Error: HIGH_RISK"), out
    assert s23.classify_safety(cmd) == s24.classify_command(cmd) == "HIGH_RISK"
    assert s23.check_sandbox(cmd)[0] is False
