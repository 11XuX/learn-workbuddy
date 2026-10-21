"""s08: memory candidate IDs must round-trip through the [id] allowlist wire format.

Contract (s08 README Memory Selector boundary + MemorySelectorRouter):
  - candidates supply stable IDs; selector returns allowlisted IDs only
  - ``_build_prompt`` encodes each candidate as ``[memory_id] content``
  - ``_allowlisted_ids`` recovers IDs with ``re.findall(r"\\[([^\\]\\n]+)\\]", ...)``

IDs that contain ``]``, ``[``, or a newline cannot be encoded/decoded by that
bracket protocol. Today ``MemoryCandidate`` only rejects empty/whitespace IDs,
so a retrieval-supplied ID like ``proj]auth`` is accepted, appears mangled in
the prompt as ``[proj]auth] ...``, and even a model that echoes ``[proj]auth]``
yields ``selected_ids=()`` — silent drop of a valid candidate.

Real entry: ``MemorySelectionRequest`` / ``MemorySelectorRouter.select``
(same surface as ``tests/test_model_routing_contracts.py``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def s08():
    name = "s08_memory_id_bracket_allowlist_test_module"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "s08_model_routing" / "code.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.DEMO_SLEEP_SCALE = 0
    yield module
    sys.modules.pop(name, None)


@pytest.mark.parametrize("bad_id", ["proj]auth", "a[b]", "mem\nid"])
def test_memory_candidate_rejects_ids_unsafe_for_bracket_wire(s08, bad_id: str) -> None:
    with pytest.raises(ValueError, match="memory candidate ID"):
        s08.MemoryCandidate(bad_id, "Login fix in auth")


def test_select_keep_allowlisted_id_when_model_echoes_bracket_form(
    s08, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Safe IDs still round-trip; documents the wire format under test."""
    router = s08.ModelRouter()
    selector = s08.MemorySelectorRouter(router)
    request = s08.MemorySelectionRequest(
        query="login",
        candidates=(
            s08.MemoryCandidate("mem_auth", "Login token expiry decision."),
            s08.MemoryCandidate("mem_ui", "Compact settings layout preference."),
        ),
        limit=1,
    )
    monkeypatch.setattr(
        s08, "mock_llm", lambda _m, _p, _t="": "[mem_auth]\n[unknown]"
    )
    result = selector.select(request, token_override=100)
    assert result.selected_ids == ("mem_auth",)
