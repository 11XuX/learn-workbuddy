"""Repro: mini_workbuddy externalized preview is sliced by characters.

README says externalized output leaves only a summary and a pointer in
history.  ``ToolRegistry._maybe_externalize`` decides by UTF-8 bytes
(``tool_result_threshold`` = 50 KiB) but builds the preview with
``content[:6_000] + content[-24_000:]`` (characters).  For a CJK file just
over the threshold the head and tail overlap, so the "preview" repeats the
text and is larger than the full output it is supposed to replace.
s13 fixed the same pattern with a UTF-8 byte budget.
"""

from __future__ import annotations

from pathlib import Path

from mini_workbuddy.agent import MiniAgent
from mini_workbuddy.config import HarnessConfig
from mini_workbuddy.events import EventBus
from mini_workbuddy.storage import Storage
from mini_workbuddy.tools import ToolRegistry


def test_cjk_read_preview_is_smaller_than_full_output(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    chars = 20_000
    text = "汉" * chars  # 60_000 UTF-8 bytes > default 50 KiB threshold
    (workdir / "notes.md").write_text(text, encoding="utf-8")

    config = HarnessConfig(root_dir=tmp_path / "home")
    storage = Storage(config)
    agent = MiniAgent(storage, ToolRegistry(config, storage), EventBus())
    session = storage.create_session(str(workdir), "cjk")

    result = agent.prompt(session, "read notes.md")["toolResults"][0]

    assert result["externalized_path"] is not None
    assert Path(result["externalized_path"]).read_text(encoding="utf-8") == text
    preview = result["content"]
    repeated = preview.count("汉")
    preview_bytes = len(preview.encode("utf-8"))
    # The preview must not repeat the original text ...
    assert repeated <= chars, f"preview holds {repeated} of {chars} chars"
    # ... and must be smaller than the output it stands in for.
    assert preview_bytes < 60_000, f"preview {preview_bytes} B >= full 60000 B"


def test_read_preview_ignores_partial_utf8_at_both_boundaries(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    text = "a" * 5999 + "汉" * 18000 + "z"
    assert len(text.encode("utf-8")) == 60000
    (workdir / "boundary.md").write_text(text, encoding="utf-8")

    config = HarnessConfig(root_dir=tmp_path / "home")
    storage = Storage(config)
    agent = MiniAgent(storage, ToolRegistry(config, storage), EventBus())
    session = storage.create_session(str(workdir), "utf8-boundary")

    result = agent.prompt(session, "read boundary.md")["toolResults"][0]

    assert result["externalized_path"] is not None
    path = Path(result["externalized_path"])
    assert path.read_text(encoding="utf-8") == text
    pointer = f"\n\nFull output written to: {path}"
    assert result["content"].endswith(pointer)
    preview = result["content"][:-len(pointer)]
    head, tail = preview.split("\n\n...[externalized output]...\n\n")

    assert "\ufffd" not in preview
    assert len(head.encode("utf-8")) <= 6000
    assert len(tail.encode("utf-8")) <= 24000
    assert head == "a" * 5999
    assert tail == "汉" * 7999 + "z"
    assert head.count("汉") + tail.count("汉") <= text.count("汉")


def test_public_read_preview_tail_starts_after_head_budget(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    text = "a" * 5999 + "汉" * 4000 + "z"
    assert len(text.encode("utf-8")) == 18000
    (workdir / "short.md").write_text(text, encoding="utf-8")

    config = HarnessConfig(
        root_dir=tmp_path / "home",
        tool_result_threshold=10 * 1024,
    )
    storage = Storage(config)
    tools = ToolRegistry(config, storage)
    session = storage.create_session(str(workdir), "tail-floor")

    result = tools.run("read_file", "short.md", session)

    assert result.externalized_path is not None
    path = Path(result.externalized_path)
    assert path.read_text(encoding="utf-8") == text
    pointer = f"\n\nFull output written to: {path}"
    assert result.content.endswith(pointer)
    preview = result.content[:-len(pointer)]
    head, tail = preview.split("\n\n...[externalized output]...\n\n")

    assert "\ufffd" not in preview
    assert len(head.encode("utf-8")) <= 6000
    assert len(tail.encode("utf-8")) <= 24000
    assert head == "a" * 5999
    assert tail == "汉" * 3999 + "z"
    assert head.count("a") + tail.count("a") == text.count("a")
    assert head.count("汉") + tail.count("汉") <= text.count("汉")
