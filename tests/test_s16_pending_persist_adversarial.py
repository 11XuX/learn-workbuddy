"""s16 pending persistence: public API adversarial contracts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

import pytest

from test_skill_create_audit import _load_s16

NET = {"tools": ["bash"], "network": True}
BODY = "curl https://example.com"


@pytest.fixture
def s16(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = _load_s16()
    saved_dir = m.PENDING_DIR
    pending = dict(m.pending_skills)
    index = list(m.skill_index)
    loaded = list(m.loaded_skills)
    seeds = dict(m.SEED_SKILLS)
    flags = [(s, s.loaded) for s in index]
    try:
        m.PENDING_DIR = None
        m.pending_skills.clear()
        m.loaded_skills.clear()
        yield m
    finally:
        m.PENDING_DIR = saved_dir
        m.pending_skills.clear()
        m.pending_skills.update(pending)
        m.skill_index[:] = index
        m.loaded_skills[:] = loaded
        m.SEED_SKILLS.clear()
        m.SEED_SKILLS.update(seeds)
        for skill, flag in flags:
            skill.loaded = flag


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def filename(title):
    slug = unicodedata.normalize("NFKC", title).lower()
    slug = re.sub(r"[^a-z0-9_-]", "-", slug)[:40] or "skill"
    return f"{slug}-{digest(title)[:16]}.json"


def create(m, title="candidate"):
    result = m.create_skill(title, "fetch", ["fetch"], BODY, NET)
    assert "待用户审批" in result
    return m.pending_skills[title]


def enable(m, tmp_path):
    return m.enable_pending_persistence(tmp_path / "pending")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data, rehash=True):
    if rehash:
        data["skill_md_sha256"] = digest(data["skill_md"])
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def state(m):
    return (
        dict(m.pending_skills),
        list(m.skill_index),
        dict(m.SEED_SKILLS),
        list(m.loaded_skills),
    )


def test_import_does_not_load_pending(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("S16_PENDING_DIR", str(tmp_path / "pending"))

    def forbidden(*args, **kwargs):
        raise AssertionError("import attempted pending disk access")

    # Source/module imports remain available; pending reads use these methods.
    with monkeypatch.context() as guard:
        guard.setattr(Path, "glob", forbidden)
        guard.setattr(Path, "read_bytes", forbidden)
        m = _load_s16()
    assert m.PENDING_DIR is None
    assert not m.pending_skills
    assert not (tmp_path / "pending").exists()


def test_default_disabled_has_no_pending_io(s16, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled persistence accessed disk")

    with monkeypatch.context() as guard:
        guard.setattr(Path, "glob", forbidden)
        guard.setattr(Path, "read_bytes", forbidden)
        guard.setattr(Path, "unlink", forbidden)
        guard.setattr("builtins.open", forbidden)
        assert s16.load_pending() == []
        assert create(s16).sha256 == ""
        assert "已经用户批准" in s16.approve_skill("candidate")
        create(s16, "rejected")
        assert "已被拒绝" in s16.reject_skill("rejected")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("action", ["approve_skill", "reject_skill"])
def test_roundtrip_and_resolution(s16, tmp_path, action):
    directory = enable(s16, tmp_path)
    original = create(s16)
    path = directory / filename("candidate")
    assert read(path)["skill_md_sha256"] == digest(original.skill_md)
    baseline_index = list(s16.skill_index)
    baseline_seeds = dict(s16.SEED_SKILLS)

    s16.pending_skills.clear()
    assert s16.load_pending() == []
    restored = s16.pending_skills["candidate"]
    assert restored.skill_md == original.skill_md
    assert restored.permissions == original.permissions
    assert s16.skill_index == baseline_index

    result = getattr(s16, action)("candidate")
    assert ("已经用户批准" if action == "approve_skill" else "已被拒绝") in result
    assert not path.exists()
    assert "candidate" not in s16.pending_skills
    assert any(s.title == "candidate" for s in s16.skill_index) == (
        action == "approve_skill"
    )

    # Simulate restart of all generated skill state, including seeds.
    s16.pending_skills.clear()
    s16.skill_index[:] = baseline_index
    s16.SEED_SKILLS.clear()
    s16.SEED_SKILLS.update(baseline_seeds)
    assert s16.load_pending() == []
    assert not s16.pending_skills
    assert all(s.title != "candidate" for s in s16.skill_index)
    create(s16)


@pytest.mark.parametrize(
    "damage",
    ["hash", "permissions", "title", "control", "marker", "missing", "rename"],
)
def test_bad_record_does_not_block_good_record(s16, tmp_path, damage):
    directory = enable(s16, tmp_path)
    create(s16, "a-bad")
    good = create(s16, "z-good")
    path = directory / filename("a-bad")
    data = read(path)
    if damage == "hash":
        data["skill_md_sha256"] = "0" * 64
    elif damage == "permissions":
        data["permissions"]["network"] = False
    elif damage == "title":
        data["title"] = "different"
    elif damage == "control":
        data["skill_md"] = data["skill_md"].replace(
            'summary: "fetch"', 'summary: "bad\\u001b"'
        )
    elif damage == "marker":
        data["skill_md"] = data["skill_md"].replace(
            "agent_created: true", "agent_created: false"
        )
    elif damage == "missing":
        del data["permissions"]
    write(path, data, rehash=damage != "hash")
    if damage == "rename":
        path.rename(directory / "renamed.json")

    s16.pending_skills.clear()
    before_index = list(s16.skill_index)
    warnings = s16.load_pending()
    assert warnings
    assert set(s16.pending_skills) == {"z-good"}
    assert s16.pending_skills["z-good"].permissions == good.permissions
    assert s16.skill_index == before_index


def test_p0_deleted_and_p2_still_requires_approval(s16, tmp_path):
    directory = enable(s16, tmp_path)
    create(s16, "a-danger")
    create(s16, "z-safe")
    danger = directory / filename("a-danger")
    data = read(danger)
    data["skill_md"] += "\nsudo whoami"
    write(danger, data)

    safe = directory / filename("z-safe")
    data = read(safe)
    data["skill_md"] = data["skill_md"].replace(BODY, "git status")
    data["skill_md"] = data["skill_md"].replace(
        '"network": true', '"network": false'
    )
    data["permissions"]["network"] = False
    write(safe, data)

    s16.pending_skills.clear()
    before_index = list(s16.skill_index)
    warnings = s16.load_pending()
    assert any("P0" in warning for warning in warnings)
    assert not danger.exists()
    assert set(s16.pending_skills) == {"z-safe"}
    assert s16.pending_skills["z-safe"].permissions.network is False
    assert s16.skill_index == before_index


def test_p0_unlink_failure_warns_and_continues(s16, tmp_path, monkeypatch):
    directory = enable(s16, tmp_path)
    create(s16, "a-danger")
    create(s16, "z-good")
    danger = directory / filename("a-danger")
    data = read(danger)
    data["skill_md"] += "\nsudo whoami"
    write(danger, data)
    s16.pending_skills.clear()
    before_index = list(s16.skill_index)
    real_unlink = Path.unlink

    def denied(path, *args, **kwargs):
        if path == danger:
            raise PermissionError("injected P0 deletion failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied)
    # Current implementation raises here instead of returning warnings.
    warnings = s16.load_pending()
    assert warnings
    assert danger.exists()
    assert set(s16.pending_skills) == {"z-good"}
    assert s16.skill_index == before_index


@pytest.mark.parametrize("action", ["approve_skill", "reject_skill"])
@pytest.mark.parametrize("damage", ["body", "permissions", "missing", "unlink"])
def test_resolution_checks_disk_and_preserves_memory(
    s16, tmp_path, monkeypatch, action, damage
):
    directory = enable(s16, tmp_path)
    create(s16)
    s16.pending_skills.clear()
    assert s16.load_pending() == []
    path = directory / filename("candidate")
    before = state(s16)
    if damage == "missing":
        path.unlink()
    elif damage == "unlink":
        real_unlink = Path.unlink

        def denied(target, *args, **kwargs):
            if target == path:
                raise PermissionError("injected resolution deletion failure")
            return real_unlink(target, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", denied)
    else:
        data = read(path)
        if damage == "body":
            data["skill_md"] += "\nchanged version"
        else:
            data["permissions"]["network"] = False
        write(path, data)
    disk_before = path.read_bytes() if path.exists() else None
    assert "核对失败" in getattr(s16, action)("candidate")
    assert state(s16) == before
    assert (path.read_bytes() if path.exists() else None) == disk_before


def test_write_failure_keeps_existing_state(s16, tmp_path, monkeypatch):
    directory = enable(s16, tmp_path)
    create(s16, "existing")
    before = state(s16)
    disk_before = {p.name: p.read_bytes() for p in directory.iterdir()}

    def fail_replace(*args, **kwargs):
        raise OSError("injected disk full")

    monkeypatch.setattr(s16.os, "replace", fail_replace)
    result = s16.create_skill("new", "fetch", ["fetch"], BODY, NET)
    assert "落盘失败" in result
    assert state(s16) == before
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == disk_before


def test_filename_rules_and_slug_collisions(s16, tmp_path):
    directory = enable(s16, tmp_path)
    titles = ("../x", "a.b", "a,b", "Ａ", "A", "中文")
    for title in titles:
        create(s16, title)
    paths = list(directory.glob("*.json"))
    assert {p.name for p in paths} == {filename(t) for t in titles}
    assert all(p.resolve().parent == directory.resolve() for p in paths)
    s16.pending_skills.clear()
    assert s16.load_pending() == []
    assert set(s16.pending_skills) == set(titles)
