"""s16 待审批技能跨重启保留：默认关闭、load 时重审、权限以 frontmatter 为准（离线）。"""

from __future__ import annotations

import json
import os

import pytest

from test_skill_create_audit import _load_s16

NET = {"tools": ["bash"], "network": True}
P1_BODY = "# Fetch\n\ncurl https://example.com"


@pytest.fixture(scope="module")
def s16_module():
    return _load_s16()


@pytest.fixture
def s16(s16_module, tmp_path):
    m = s16_module
    index, seeds = list(m.skill_index), dict(m.SEED_SKILLS)
    m.pending_skills.clear()
    m.enable_pending_persistence(tmp_path / "pending")
    yield m
    m.PENDING_DIR = None
    m.pending_skills.clear()
    m.skill_index[:] = index
    m.SEED_SKILLS.clear()
    m.SEED_SKILLS.update(seeds)


def create(m, title="fetch", content=P1_BODY, permissions=NET):
    return m.create_skill(title, "抓取", ["抓取"], content, permissions)


def restart(m, keep_index=False):
    m.pending_skills.clear()
    if not keep_index:
        m.skill_index[:] = [s for s in m.skill_index if not s.agent_created]
    return m.load_pending()


files = lambda m: sorted(p.name for p in m.PENDING_DIR.iterdir())  # noqa: E731
indexed = lambda m, title: any(s.title == title for s in m.skill_index)  # noqa: E731


def test_default_off_writes_nothing(s16_module, tmp_path, monkeypatch):
    m = s16_module
    monkeypatch.chdir(tmp_path)
    assert m.PENDING_DIR is None and m.load_pending() == []
    assert "待用户审批" in create(m)
    assert list(tmp_path.iterdir()) == [] and m.pending_skills["fetch"].sha256 == ""
    assert "已经用户批准" in m.approve_skill("fetch")
    m.skill_index[:] = [s for s in m.skill_index if s.title != "fetch"]
    m.SEED_SKILLS.pop("fetch")


@pytest.mark.parametrize("bad", ["", " x", "x\n", 0, None, b"x", type("S", (str,), {})("x")])
def test_enable_rejects_bad_path(s16, bad):
    with pytest.raises((TypeError, ValueError)):
        s16.enable_pending_persistence(bad)


def test_restart_restores_queue_not_index(s16):
    assert "待用户审批" in create(s16)
    md, perms = s16.pending_skills["fetch"].skill_md, s16.pending_skills["fetch"].permissions
    assert restart(s16) == []
    assert s16.pending_skills["fetch"].skill_md == md
    assert s16.pending_skills["fetch"].permissions == perms and not indexed(s16, "fetch")


def test_approve_deletes_file_and_index_is_not_persisted(s16):
    create(s16)
    assert "已经用户批准" in s16.approve_skill("fetch")
    assert indexed(s16, "fetch") and files(s16) == []
    assert restart(s16) == [] and "fetch" not in s16.pending_skills
    assert not indexed(s16, "fetch")  # 边界：已批准技能仍是内存态


def test_reject_then_recreate(s16):
    create(s16)
    assert "已被拒绝" in s16.reject_skill("fetch")
    assert restart(s16) == [] and not s16.pending_skills and not indexed(s16, "fetch")
    assert "待用户审批" in create(s16) and len(files(s16)) == 1


def forge(s16, mutate, load_first=False):
    create(s16)
    if load_first:
        restart(s16)
    path = s16.PENDING_DIR / files(s16)[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    data["skill_md_sha256"] = s16._sha256(data["skill_md"])
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    return path


def test_forged_p0_is_deleted_on_load(s16):
    path = forge(s16, lambda d: d.update(
        skill_md=d["skill_md"].replace("curl https", "os.system(1) curl https")))
    warnings = restart(s16)
    assert "P0" in warnings[0] and not path.exists()
    assert not s16.pending_skills and not indexed(s16, "fetch")


def test_wider_permissions_field_is_skipped(s16):
    path = forge(s16, lambda d: d["permissions"].update(
        tools=["bash", "write_file"], paths={"read": ["**"], "write": ["**"]}))
    assert "permissions" in restart(s16)[0] and not s16.pending_skills and path.exists()


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(skill_md=d["skill_md"].replace('"抓取"', '"x---"', 1)),
    lambda d: d.update(skill_md=d["skill_md"].replace("agent_created: true", "agent_created: false")),
    lambda d: d.update(title="other"),
    lambda d: d.update(extra=1),
    lambda d: d.pop("permissions"),
])
def test_noncanonical_or_bad_fields_fail_closed(s16, mutate):
    forge(s16, mutate)
    assert restart(s16)[0].startswith("跳过") and not s16.pending_skills


@pytest.mark.parametrize("raw", [b"", b'{"title": "fetch"', b"{}\n{}\n", b"\xff\n", b"[]\n"])
def test_half_line_or_corrupt_file_fails_closed(s16, raw):
    create(s16)
    path = s16.PENDING_DIR / files(s16)[0]
    good = path.read_bytes()
    path.write_bytes(good.rstrip(b"\n") if raw == b"" else raw)
    assert restart(s16)[0].startswith("跳过") and not s16.pending_skills


def test_unicode_separator_is_not_a_line_break(s16):
    content = P1_BODY + "\u2028\u0085 tail"
    create(s16, content=content)
    assert restart(s16) == [] and s16.pending_skills["fetch"].skill_md.endswith(" tail")


def test_paths_collisions_and_renames(s16):
    for title in ("../x", "a.b", "a,b"):
        assert "待用户审批" in create(s16, title)
    names = files(s16)
    assert len(names) == 3 and all(n.endswith(".json") for n in names)
    assert all(p.parent == s16.PENDING_DIR for p in s16.PENDING_DIR.iterdir())
    src = s16.PENDING_DIR / names[0]
    src.rename(s16.PENDING_DIR / ("renamed-" + names[0]))
    warnings = restart(s16)
    assert len(warnings) == 1 and "title" in warnings[0] and len(s16.pending_skills) == 2


def test_tamper_after_load_blocks_approve_and_reject(s16):
    forge(s16, lambda d: d.update(
        skill_md=d["skill_md"].replace("example.com", "evil.example")), load_first=True)
    before = list(s16.skill_index)
    for action in (s16.approve_skill, s16.reject_skill):
        assert "核对失败" in action("fetch")
    assert s16.skill_index == before and "fetch" in s16.pending_skills


def test_write_failure_leaves_no_state(s16, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    assert "落盘失败" in create(s16)
    assert not s16.pending_skills and files(s16) == []
    monkeypatch.undo()
    assert "落盘失败" in create(s16, type("S", (str,), {})("sub"))  # str 子类不落盘
    assert not s16.pending_skills and files(s16) == []
