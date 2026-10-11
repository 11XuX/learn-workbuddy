"""S13 bash pointers must honor HEAD_BYTES/TAIL_BYTES as a byte budget.

Docs and constants advertise a ~30KB context pointer (head 6KB + tail 24KB).
``make_pointer`` currently slices the Unicode string by character index, so:

1. Just above the bash char threshold, head and tail overlap and the pointer
   is *larger* than the original output (externalization makes context worse).
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


def test_bash_pointer_near_threshold_must_not_bloat_context(s13, tmp_path: Path) -> None:
    """Externalization must shrink context, even just above the bash threshold."""

    size = s13.BASH_MAX_OUTPUT_LENGTH + 1
    output = ("todo item here\n" * ((size // 15) + 10))[:size]
    assert len(output) == size

    result = _externalize(s13, tmp_path / "session-ascii", output)
    pointer = result.context_text

    assert len(pointer) < len(output), (
        "bash pointer must be smaller than the original tool output; "
        f"got pointer={len(pointer)} chars vs output={len(output)} chars "
        "(head/tail character slices overlap when size < HEAD_BYTES+TAIL_BYTES)"
    )


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
