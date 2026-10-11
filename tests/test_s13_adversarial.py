"""Adversarial coverage for bash pointer byte and character boundaries."""

import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s13():
    name = "s13_adversarial_module"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "s13_output_externalization" / "code.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


def _prefix_within_bytes(text, budget):
    """Independent oracle: accumulate complete characters, never slice bytes."""
    used = 0
    chars = []
    for char in text:
        width = len(char.encode("utf-8"))
        if used + width > budget:
            break
        used += width
        chars.append(char)
    return "".join(chars)


@pytest.mark.parametrize("offset", range(4))
@pytest.mark.parametrize("size", [0, 6145, 30001, 30720, 30721, 100000])
def test_bash_pointer_exact_slices_and_omitted(s13, offset, size):
    output = ("a" * offset + "中😀xy" * (size // 4 + 1))[:size]
    artifact = SimpleNamespace(
        source=SimpleNamespace(source_id="adversarial"),
        summary="fixed summary",
        source_tool="bash",
        content_sha256="0" * 64,
        path=Path("/artifact.txt"),
    )
    # make_pointer needs no session directory or disk writes.
    externalizer = object.__new__(s13.ToolResultExternalizer)
    pointer = externalizer.make_pointer(output, artifact)
    header, body = pointer.split("\n\n", 1)
    match = re.fullmatch(
        r"(.*?)\n\n\.\.\. \[(\d+) characters omitted, "
        r"full output at: /artifact\.txt\] \.\.\.\n\n(.*)",
        body,
        re.DOTALL,
    )
    assert match is not None
    head, omitted, tail = match.groups()

    expected_head = _prefix_within_bytes(output, s13.HEAD_BYTES)
    head_chars = len(expected_head)
    # A character crossing the head boundary belongs to neither preview.
    if (
        head_chars < len(output)
        and len(expected_head.encode("utf-8")) < s13.HEAD_BYTES
    ):
        head_chars += 1
    remainder = output[head_chars:]
    expected_tail = _prefix_within_bytes(
        remainder[::-1], s13.TAIL_BYTES
    )[::-1]

    assert head == expected_head
    assert tail == expected_tail
    assert int(omitted) == len(output) - len(head) - len(tail)
    assert int(omitted) >= 0
    assert len(head.encode("utf-8")) <= s13.HEAD_BYTES
    assert len(tail.encode("utf-8")) <= s13.TAIL_BYTES
    assert "\ufffd" not in head + tail
    assert "SHA-256: " + artifact.content_sha256 in header


def test_bash_threshold_still_counts_characters(s13):
    externalizer = object.__new__(s13.ToolResultExternalizer)
    threshold = s13.BASH_MAX_OUTPUT_LENGTH
    assert not externalizer.should_externalize("😀" * threshold, "bash")
    assert externalizer.should_externalize("😀" * (threshold + 1), "bash")
