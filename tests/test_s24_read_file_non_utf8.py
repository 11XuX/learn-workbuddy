"""Reproduce: s24 ``read_file`` on a non-UTF-8 workspace file crashes the turn.

``execute_tool`` turns handler failures (``OSError`` / ``PermissionError`` /
``KeyError``) into a ``Tool failed: ...`` tool_result, and
``tests/test_s24_repl_write_file_approval.py`` locks that prefix for a failing
handler. ``_read_file`` calls ``path.read_text()`` (strict UTF-8), so a GBK
note or a PNG raises ``UnicodeDecodeError`` (a ``ValueError``) straight out of
``execute_tool``; ``ComprehensiveAgent.run()`` has no other guard, so the REPL
exits and the audit log keeps ``tool_execute`` without a ``tool_result``.
The png bytes (0x81, 0xff) fail under UTF-8, ASCII, GBK and cp1252 locales.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAME = "s24_read_file_non_utf8_repro"


@pytest.fixture
def s24(tmp_path, monkeypatch):
    saved = sys.modules.pop("anthropic", None)
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "stubs"))
    monkeypatch.setenv("MODEL_ID", "offline-test-model")
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "home"))
    spec = importlib.util.spec_from_file_location(NAME, ROOT / "s24_comprehensive" / "code.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module
    try:
        spec.loader.exec_module(module)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        monkeypatch.chdir(workspace)
        yield module, workspace
    finally:
        sys.modules.pop(NAME, None)
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved


@pytest.mark.parametrize(
    "name, data",
    [
        ("notes-gbk.txt", "会议纪要：周五发布".encode("gbk")),
        ("logo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x81\xff\xfe"),
    ],
    ids=["gbk-text", "png"],
)
def test_read_file_non_utf8_returns_tool_result(s24, name, data):
    module, workspace = s24
    (workspace / name).write_bytes(data)
    try:
        out = module.execute_tool("read_file", {"path": name})
    except Exception as exc:  # what run() would let escape
        pytest.fail(f"read_file raised {type(exc).__name__}: {exc}")
    assert isinstance(out, str) and out


def test_read_file_directory_is_already_a_tool_result(s24):
    module, workspace = s24
    (workspace / "sub").mkdir()
    out = module.execute_tool("read_file", {"path": "sub"})
    assert out.startswith("Tool failed:")


# --- Verifier adversarial (#84) ---


def test_read_file_preserves_text_replaces_bytes_and_limits_output(s24):
    module, workspace = s24
    path = workspace / "mixed.txt"
    prefix = "会议记录\n"
    data = prefix.encode("utf-8") + b"\xff" + b"x" * 10_000
    path.write_bytes(data)

    out = module.execute_tool("read_file", {"path": path.name})

    expected = prefix + "\ufffd" + "x" * 10_000
    assert out == expected[:10_000]
    assert path.read_bytes() == data


def test_read_file_utf8_chinese_survives_non_utf8_locale(tmp_path):
    """Explicit encoding="utf-8": under a C/ASCII locale a UTF-8 note must read back intact."""
    import os
    import subprocess

    ws = tmp_path / "ws"
    ws.mkdir()
    text = "提交前运行 ruff\n"
    (ws / "zh.txt").write_bytes(text.encode("utf-8"))
    script = (
        "import importlib.util, locale, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'tests' / 'stubs')!r})\n"
        f"spec = importlib.util.spec_from_file_location('s24c', {str(ROOT / 's24_comprehensive' / 'code.py')!r})\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['s24c'] = m; spec.loader.exec_module(m)\n"
        "print(locale.getpreferredencoding(False)); print(ascii(m.execute_tool('read_file', {'path': 'zh.txt'})))\n"
    )
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0",
               MODEL_ID="offline-test-model", WORKBUDDY_HOME=str(tmp_path / "home"))
    proc = subprocess.run([sys.executable, "-c", script], cwd=ws, env=env, capture_output=True, text=True)
    lines = proc.stdout.splitlines()
    if lines and lines[0].lower().replace("-", "") == "utf8":
        pytest.skip("platform C locale is UTF-8; cannot simulate a non-UTF-8 locale")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert lines[-1] == ascii(text)
