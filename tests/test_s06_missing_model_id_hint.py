"""Reproduce: s06 without MODEL_ID crashes with TypeError, the hint is lost.

``tests/test_chapter_imports.py`` states the intent: running a chapter
without MODEL_ID must exit with the quick-start hint. s06 only checks
MODEL_ID inside ``SidecarServer._run_agent_loop``, which runs on the sidecar
thread; ``SystemExit`` there silently ends the thread, the socket closes, and
``main()`` gets ``None`` back from ``client.call`` (``"error" in None``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s06_sidecar_server" / "code.py"


def _run_repl(tmp_path: Path, stdin: str, model_id: str | None) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "tests" / "stubs"), str(ROOT)])
    env["HOME"] = str(tmp_path)
    env["WORKBUDDY_HOME"] = str(tmp_path / "wb")
    for key in ("MODEL_ID", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        env.pop(key, None)
    if model_id is not None:
        env["MODEL_ID"] = model_id
    result = subprocess.run(
        [sys.executable, str(CODE)], cwd=tmp_path, env=env, input=stdin,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
    )
    return result.stdout


def test_missing_model_id_gives_quickstart_hint(tmp_path):
    out = _run_repl(tmp_path, "hello\nq\n", model_id=None)
    assert "MODEL_ID is not set" in out
    assert "Traceback" not in out


def test_model_error_is_reported_and_repl_continues(tmp_path):
    # Guard: with MODEL_ID set, a failing model call (stub raises) comes back
    # as a JSON-RPC error and the REPL keeps serving commands.
    out = _run_repl(tmp_path, "hello\n/status\nq\n", model_id="offline-test-model")
    assert "Traceback" not in out
    assert "sessions: 1" in out and "Sidecar shut down" in out
