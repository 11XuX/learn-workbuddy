"""复现测试：s03 延迟工具的 schema 与 handler 必须是同一份契约。

README「设计不变量 1」承诺 ToolEntry 同时持有 schema 和 handler，用来避免
“schema 已更新，handler 仍按旧参数运行”。模型通过 ToolSearch 拿到完整
input_schema 后，只要按 schema 提交参数（包括 schema 声明的可选参数），
DeferExecuteTool 就不应该因为 handler 签名不认识这些参数而报错。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_s03():
    """导入离线教学模块，但不运行它的 CLI。"""

    module_name = "s03_deferred_schema_parity_test_module"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "s03_deferred_loading" / "code.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def sample_value(rule: dict[str, Any]) -> Any:
    """按 schema 规则构造一个合法取值：有 enum 取第一个，否则按 type 给示例。"""

    if "enum" in rule:
        return rule["enum"][0]
    samples = {
        "string": "示例",
        "integer": 1,
        "number": 1.5,
        "boolean": True,
        "array": [1, 2],
        "object": {},
    }
    return samples[rule["type"]]


def schema_complete_params(schema: dict[str, Any]) -> dict[str, Any]:
    """把 input_schema 里声明的全部参数（必填 + 可选）都填上合法值。"""

    properties = schema["input_schema"].get("properties", {})
    return {name: sample_value(rule) for name, rule in properties.items()}


S03 = load_s03()
DEFERRED_NAMES = S03.build_registry().get_deferred_names()


@pytest.mark.parametrize("tool_name", DEFERRED_NAMES)
def test_loaded_schema_params_are_accepted_by_deferred_handler(tool_name: str) -> None:
    registry = S03.build_registry()
    discovery = registry.load_by_name([tool_name])
    assert discovery.matches, f"{tool_name} 应该能通过 ToolSearch 精确加载"
    schema = dict(discovery.matches[0].schema)
    params = schema_complete_params(schema)

    output = S03.handle_defer_execute(registry, tool_name, params)

    # 期望：模型严格按已加载 schema 提交的参数能被 handler 接受。
    # 实际（bug）：部分 mock handler 的签名缺少 schema 声明的可选参数，
    # 返回 "Error executing ...: got an unexpected keyword argument ..."。
    assert not output.startswith("Error"), (
        f"{tool_name} 的 schema 声明了参数 {sorted(params)}，"
        f"但按 schema 调用 DeferExecuteTool 失败：{output}"
    )
