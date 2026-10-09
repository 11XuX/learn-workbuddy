"""s06 RingBuffer 回绕后读出的近期日志不应损坏多字节字符。

README 说 RingBuffer「满了就覆盖最旧的数据——保留最近的日志」，
`read_all()` 的 docstring 也写明按「最旧到最新」的顺序读出全部有效数据。

当前实现在缓冲区写满后，把 `buffer[write_pos:]` 和 `buffer[:write_pos]`
分两段各自 UTF-8 解码再拼接。物理回绕点 `write_pos` 可能正好落在一个中文字符
的字节中间，这个字符就会被拆成两半、各自变成替换字符，而它可能是最新写入的内容。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s06():
    """用离线 anthropic stub 导入 s06 章节代码。"""

    stub_dir = ROOT / "tests" / "stubs"
    sys.path.insert(0, str(stub_dir))
    saved_anthropic = sys.modules.pop("anthropic", None)
    old_model = os.environ.get("MODEL_ID")
    os.environ["MODEL_ID"] = "offline-test-model"
    module_name = "s06_ring_buffer_utf8_wrap_test_module"
    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            ROOT / "s06_sidecar_server" / "code.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(stub_dir))
        sys.modules.pop(module_name, None)
        sys.modules.pop("anthropic", None)
        if saved_anthropic is not None:
            sys.modules["anthropic"] = saved_anthropic
        if old_model is None:
            os.environ.pop("MODEL_ID", None)
        else:
            os.environ["MODEL_ID"] = old_model


def test_multibyte_char_across_physical_wrap_survives(s06) -> None:
    # "ab" + "中文" 共 8 字节，写进 6 字节的缓冲区后 write_pos == 2：
    # 缓冲区里保留的正好是完整的「中文」6 个字节，但「文」的 3 个字节跨在物理回绕点上。
    ring = s06.RingBuffer(size=6)
    ring.write("ab")
    ring.write("中文")

    assert ring.is_full
    assert ring.write_pos == 2
    assert ring.read_all() == "中文", f"缓冲区里完整保留的最近数据被读坏：{ring.read_all()!r}"


def test_newest_char_is_intact_when_oldest_char_is_cut(s06) -> None:
    # 最旧的「中」被覆盖掉 1 个字节，开头出现替换字符可以接受；
    # 但最新写入、完整保存在缓冲区里的「文」必须原样读出。
    ring = s06.RingBuffer(size=5)
    ring.write("中文")

    assert ring.read_all().endswith("文"), f"最新写入的字符被读坏：{ring.read_all()!r}"


def test_unwrapped_multibyte_text_reads_back(s06) -> None:
    # 对照：没写满时只有一段解码，main 上就正常。
    ring = s06.RingBuffer(size=16)
    ring.write("日志：中文")

    assert ring.read_all() == "日志：中文"


def test_ascii_wrap_keeps_newest_bytes_in_order(s06) -> None:
    # 对照：单字节内容回绕后按「最旧到最新」读出，main 上就正常，修复后也不能变。
    ring = s06.RingBuffer(size=4)
    ring.write("abcdef")

    assert ring.read_all() == "cdef"
