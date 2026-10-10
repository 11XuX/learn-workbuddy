"""s10 对抗测试：确认必须匹配整条记录，加成只能影响首次晋升。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s10_adversarial():
    """沿用离线加载方式，并恢复导入前的环境与模块状态。"""
    stub_dir = str(ROOT / "tests" / "stubs")
    saved_path = sys.path[:]
    saved_env = os.environ.copy()
    saved_anthropic = sys.modules.pop("anthropic", None)
    name = "s10_confirmed_weight_adversarial_module"
    sys.path.insert(0, stub_dir)
    os.environ["MODEL_ID"] = "offline-test-model"
    try:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "s10_workspace_memory" / "code.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = saved_path
        sys.modules.pop(name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        os.environ.clear()
        os.environ.update(saved_env)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "LegacyTool!"),
        ("evidence", {"actor": "other"}),
        ("recorded_at", "2000-01-01T00:00:00Z"),
        ("memory_key", "formatter"),
        ("schema_version", 1),
    ],
)
def test_same_id_metadata_changes_do_not_inherit_confirmation(
    s10_adversarial, tmp_path: Path, field, value
):
    """保持 ID 和内容，只改元数据，捕捉只比 ID 或内容的错误实现。"""
    memory = s10_adversarial.WorkspaceMemory(tmp_path)
    real = memory.confirm_fact(
        "提交前运行检查", kind="convention", importance=3
    )
    log = memory.today_log_path()
    payload = json.loads(log.read_text(encoding="utf-8"))
    payload[field] = value
    log.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    (changed,) = memory.read_all_facts()
    assert changed.fact_id == real.fact_id
    assert changed.content == real.content
    assert not memory.is_session_confirmed(changed)

    later = datetime.now(timezone.utc) + timedelta(days=40)
    report = memory.distill(as_of=later)
    assert report.created == 0
    assert report.skipped == 1
    assert memory._load_curated() == []


@pytest.mark.parametrize(
    ("bonus", "importance", "expected_created"),
    [(0, 3, 0), (1, 2, 0), (2, 2, 1)],
)
def test_configured_bonus_controls_first_promotion(
    s10_adversarial, tmp_path: Path, bonus, importance, expected_created
):
    """用固定期望捕捉硬编码加成或忽略自定义策略的错误实现。"""
    memory = s10_adversarial.WorkspaceMemory(tmp_path)
    memory.confirm_fact(
        "提交前运行检查", kind="convention", importance=importance
    )
    policy = s10_adversarial.DistillPolicy(
        confirmed_importance_bonus=bonus
    )
    later = datetime.now(timezone.utc) + timedelta(days=40)
    report = memory.distill(as_of=later, policy=policy)
    assert report.created == expected_created
    assert [entry.content for entry in memory._load_curated()] == (
        ["提交前运行检查"] if expected_created else []
    )


def test_confirmation_bonus_does_not_help_two_challengers_supersede(
    s10_adversarial, tmp_path: Path, monkeypatch
):
    """让替换重复门槛不再提前拦截，检查确认加成是否误入替换路径。"""
    memory = s10_adversarial.WorkspaceMemory(tmp_path)
    append = memory.append_daily_log
    append(
        "使用原格式器",
        kind="convention",
        importance=5,
        memory_key="formatter",
        recorded_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        fact_id="original",
    )
    later = datetime.now(timezone.utc) + timedelta(days=40)
    assert memory.distill(as_of=later).created == 1

    # 注入带 key 的写入接缝；会话凭据仍由真实 confirm_fact 建立。
    def append_keyed(content, **kwargs):
        return append(content, memory_key="formatter", **kwargs)

    monkeypatch.setattr(memory, "append_daily_log", append_keyed)
    confirmed = memory.confirm_fact(
        "使用新格式器", kind="convention", importance=3
    )
    assert memory.is_session_confirmed(confirmed)
    append(
        "使用新格式器",
        kind="convention",
        importance=3,
        memory_key="formatter",
        fact_id="second",
    )

    # 两条证据满足替换门槛 2，但不满足普通重复门槛 3。
    policy = s10_adversarial.DistillPolicy(repeat_threshold=3)
    report = memory.distill(as_of=later, policy=policy)
    assert report.created == 0
    assert report.superseded == 0
    assert report.skipped == 3
    assert [entry.content for entry in memory._load_curated()] == [
        "使用原格式器"
    ]
