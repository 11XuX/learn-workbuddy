"""Reproduce: s06 without MODEL_ID crashes with TypeError, the hint is lost.

``tests/test_chapter_imports.py`` states the intent: running a chapter
without MODEL_ID must exit with the quick-start hint. s06 only checks
MODEL_ID inside ``SidecarServer._run_agent_loop``, which runs on the sidecar
thread; ``SystemExit`` there silently ends the thread, the socket closes, and
``main()`` gets ``None`` back from ``client.call`` (``"error" in None``).

Isolation from a developer's real ``.env``: s06 and ``chapter_demo`` call
``load_dotenv(override=True)``, which searches upward from the *script's*
directory (not cwd), so a repo-root ``.env`` would put MODEL_ID back. The
child gets a no-op ``dotenv`` module first on PYTHONPATH instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "s06_sidecar_server" / "code.py"
HINT = "MODEL_ID is not set. Copy .env.example to .env"


def test_missing_model_id_gives_quickstart_hint(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "dotenv.py").write_text(
        "def load_dotenv(*args, **kwargs):\n    return False\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(shadow), str(ROOT / "tests" / "stubs"), str(ROOT)]
    )
    env["HOME"] = str(tmp_path)
    env["WORKBUDDY_HOME"] = str(tmp_path / "wb")
    for key in ("MODEL_ID", "PROVIDER", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, str(CODE)], cwd=tmp_path, env=env, input="hello\nq\n",
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
    )
    out = result.stdout
    # The hint itself must reach the user, not just "no crash".
    assert HINT in out, out[-1500:]
    assert "Traceback" not in out, out[-1500:]
    # And it must stop the program like every other chapter, before the
    # REPL / sidecar ever start (so swallowing the exit in the thread fails).
    assert result.returncode != 0, out[-1500:]
    assert "Sidecar started" not in out and "Goodbye" not in out, out[-1500:]
