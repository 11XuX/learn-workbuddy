"""Reproduce: s22 last_status stays 'success' after a failed automation run.

README runtime_state tracks last_status; _execute writes runs.status='failed'
on exceptions, but _update_state_after_run hard-codes last_status='success'.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = "s22_last_status_ignores_failure_repro"


def _load_s22(home: Path):
    stub = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub))
    saved = sys.modules.pop("anthropic", None)
    prev = {k: os.environ.get(k) for k in ("MODEL_ID", "WORKBUDDY_HOME")}
    os.environ.update({"MODEL_ID": "offline-test-model", "WORKBUDDY_HOME": str(home)})
    try:
        spec = importlib.util.spec_from_file_location(
            MODULE, ROOT / "s22_automation_scheduler" / "code.py"
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[MODULE] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(stub))
        sys.modules.pop("anthropic", None)
        if saved is not None:
            sys.modules["anthropic"] = saved
        for k, v in prev.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture
def s22(tmp_path):
    mod = _load_s22(tmp_path)
    try:
        yield mod
    finally:
        sys.modules.pop(MODULE, None)


def test_last_status_reflects_failed_run(s22):
    db = s22.init_db()
    manager = s22.AutomationManager(db)
    scheduler = s22.Scheduler(db, manager)
    aid = manager.create("t", "p", "recurring", rrule="FREQ=DAILY;INTERVAL=1")
    past = (datetime.now() - timedelta(minutes=1)).isoformat()
    db.execute(
        "UPDATE automation_runtime_state SET next_run=? WHERE automation_id=?", (past, aid)
    )
    db.commit()
    scheduler.check_and_run()
    run = dict(db.execute(
        "SELECT status FROM automation_runs WHERE automation_id=? ORDER BY id DESC LIMIT 1",
        (aid,),
    ).fetchone())
    state = dict(db.execute(
        "SELECT last_status FROM automation_runtime_state WHERE automation_id=?", (aid,)
    ).fetchone())
    assert run["status"] == "failed"
    assert state["last_status"] == "failed"


def test_last_status_reflects_successful_run(s22, monkeypatch):
    text = type("Block", (), {"type": "text", "text": "ok"})()
    response = type("Response", (), {"content": [text]})()
    monkeypatch.setattr(s22.client.messages, "create", lambda **kwargs: response)
    db = s22.init_db()
    manager = s22.AutomationManager(db)
    scheduler = s22.Scheduler(db, manager)
    aid = manager.create("t", "p", "recurring", rrule="FREQ=DAILY;INTERVAL=1")
    past = (datetime.now() - timedelta(minutes=1)).isoformat()
    db.execute(
        "UPDATE automation_runtime_state SET next_run=? WHERE automation_id=?", (past, aid)
    )
    db.commit()
    scheduler.check_and_run()
    run = dict(db.execute(
        "SELECT status FROM automation_runs WHERE automation_id=? ORDER BY id DESC LIMIT 1",
        (aid,),
    ).fetchone())
    state = dict(db.execute(
        "SELECT last_status FROM automation_runtime_state WHERE automation_id=?", (aid,)
    ).fetchone())
    assert run["status"] == "success"
    assert state["last_status"] == "success"
