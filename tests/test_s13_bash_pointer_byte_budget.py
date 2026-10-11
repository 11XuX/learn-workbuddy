"""S13 bash pointers must honor HEAD_BYTES/TAIL_BYTES as a byte budget.

Docs and constants advertise a ~30KB context pointer (head 6KB + tail 24KB).
``make_pointer`` currently slices the Unicode string by character index, so:

1. Just above the bash char threshold, head and tail overlap and the same
   lines are copied into context twice (pointer larger than the output).
2. Multi-byte UTF-8 tool output inflates the pointer far past the advertised
   30KB byte budget (e.g. ~85KB for a 30001-character Chinese transcript).
"""

from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s13():
    name = "s13_bash_pointer_byte_budget_module"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "s13_output_externalization" / "code.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _externalize(s13, session: Path, output: str):
    externalizer = s13.ToolResultExternalizer(session)
    assert externalizer.should_externalize(output, "bash")
    with redirect_stdout(io.StringIO()):
        return externalizer.externalize(output, "bash")


def _numbered_lines(total_chars: int) -> str:
    lines = []
    n = 0
    while sum(len(x) for x in lines) < total_chars + 64:
        lines.append(f"line-{n:06d}\n")
        n += 1
    return "".join(lines)[:total_chars]


def test_bash_pointer_head_and_tail_must_not_overlap(s13, tmp_path: Path) -> None:
    """The ``[N omitted]`` marker means head and tail are disjoint slices.

    ``omitted = max(size - HEAD_BYTES - TAIL_BYTES, 0)`` clamps to 0 just above
    the bash threshold (30000 chars < 30720), but the slices still overlap, so
    the same lines are copied into context twice.
    """

    size = s13.BASH_MAX_OUTPUT_LENGTH + 1
    output = _numbered_lines(size)
    assert len(output) == size

    pointer = _externalize(s13, tmp_path / "session-ascii", output).context_text
    duplicated = [
        line for line in set(output.splitlines()[1:-1]) if pointer.count(line) > 1
    ]
    assert not duplicated, (
        "bash pointer head/tail must not repeat the same output lines; "
        f"{len(duplicated)} lines appear twice (pointer={len(pointer)} chars vs "
        f"output={len(output)} chars)"
    )


def test_control_large_ascii_pointer_is_bounded_and_keeps_both_ends(
    s13, tmp_path: Path
) -> None:
    """Control: ASCII well above threshold already behaves (passes on main)."""

    output = _numbered_lines(200_000)
    pointer = _externalize(s13, tmp_path / "session-big", output).context_text
    assert len(pointer.encode("utf-8")) <= s13.HEAD_BYTES + s13.TAIL_BYTES + 2048
    assert output[:200] in pointer
    assert output[-200:] in pointer
    assert pointer.count("line-000002\n") == 1  # line 0 is reused as summary


def test_bash_pointer_utf8_respects_advertised_byte_budget(s13, tmp_path: Path) -> None:
    """HEAD_BYTES/TAIL_BYTES name a byte budget; UTF-8 must not blow past ~30KB."""

    size = s13.BASH_MAX_OUTPUT_LENGTH + 1
    chunk = "待办事项一行中文内容。\n"
    output = (chunk * 5000)[:size]
    assert len(output) == size

    result = _externalize(s13, tmp_path / "session-utf8", output)
    pointer_bytes = len(result.context_text.encode("utf-8"))
    advertised_body = s13.HEAD_BYTES + s13.TAIL_BYTES
    # Allow a small header (source id / summary / paths); body must not be 3x.
    budget = advertised_body + 2048

    assert pointer_bytes <= budget, (
        "bash pointer must stay near the documented ~30KB byte budget; "
        f"got {pointer_bytes} bytes vs budget {budget} "
        f"(HEAD_BYTES+TAIL_BYTES={advertised_body}). "
        "Character-index slicing turns multi-byte UTF-8 into an ~85KB pointer."
    )
