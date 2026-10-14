"""Reproduce: s22 pause+resume of a once task leaves next_run NULL.

update_status(PAUSED) clears next_run for every schedule type. Resume only
recomputes next_run for recurring tasks, so a once task that was paused
before it fired stays ACTIVE with next_run NULL and check_and_run skips it.
README exercise 2: pause sets next_run NULL, resume recalculates.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = "s22_once_pause_resume_next_run_repro"


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


def test_resume_once_restores_scheduled_next_run(s22):
    db = s22.init_db()
    manager = s22.AutomationManager(db)
    scheduled = (datetime.now() - timedelta(seconds=5)).isoformat()
    aid = manager.create("once", "p", "once", scheduled_at=scheduled)
    manager.update_status(aid, "PAUSED")
    manager.update_status(aid, "ACTIVE")
    row = dict(db.execute(
        "SELECT next_run FROM automation_runtime_state WHERE automation_id=?",
        (aid,),
    ).fetchone())
    auto = manager.view(aid)
    assert auto["status"] == "ACTIVE"
    assert row["next_run"] == scheduled


def test_control_finished_once_stays_unscheduled_after_resume(s22):
    db = s22.init_db()
    manager = s22.AutomationManager(db)
    scheduled = (datetime.now() - timedelta(seconds=5)).isoformat()
    aid = manager.create("once", "p", "once", scheduled_at=scheduled)
    s22.Scheduler(db, manager).check_and_run()  # runs once, then auto-PAUSED
    manager.update_status(aid, "ACTIVE")
    sql = "SELECT next_run FROM automation_runtime_state WHERE automation_id=?"
    assert db.execute(sql, (aid,)).fetchone()["next_run"] is None


def test_adversarial_resumed_once_fires_exactly_once(s22):
    # End to end: pause -> resume -> scheduler fires it once; a second
    # resume after the run must not schedule it again.
    db = s22.init_db()
    manager = s22.AutomationManager(db)
    scheduled = (datetime.now() - timedelta(seconds=5)).isoformat()
    aid = manager.create("once", "p", "once", scheduled_at=scheduled)
    scheduler = s22.Scheduler(db, manager)
    manager.update_status(aid, "PAUSED")
    assert scheduler.check_and_run() == 0
    manager.update_status(aid, "ACTIVE")
    assert scheduler.check_and_run() == 1
    manager.update_status(aid, "ACTIVE")
    assert scheduler.check_and_run() == 0
