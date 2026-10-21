"""s08: memory candidate IDs must round-trip through the [id] allowlist wire.

Contract: README Memory Selector returns stable IDs from the candidate set;
``_build_prompt`` encodes ``[memory_id] content``; ``_allowlisted_ids`` uses
``re.findall(r"\\[([^\\]\\n]+)\\]", ...)``. IDs with ``]``/``[``/``\\n`` cannot
round-trip. Today only empty IDs are rejected, so ``proj]auth`` constructs,
mangles the prompt, and even echo ``[proj]auth]`` yields ``selected_ids=()``.

Real entry: ``MemorySelectionRequest`` / ``MemorySelectorRouter.select``.
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


@pytest.mark.parametrize(
    "bad_id,echo",
    [("proj]auth", "[proj]auth]"), ("a[b]", "[a[b]]"), ("mem\nid", "[mem\nid]")],
)
def test_memory_candidate_rejects_ids_unsafe_for_bracket_wire(
    s08, bad_id: str, echo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject at construction. On unfixed main, prove silent-drop then fail."""
    try:
        cand = s08.MemoryCandidate(bad_id, "Login fix in auth")
    except ValueError as exc:
        assert "memory candidate ID" in str(exc)
        return
    selector = s08.MemorySelectorRouter(s08.ModelRouter())
    req = s08.MemorySelectionRequest(query="login", candidates=(cand,), limit=1)
    monkeypatch.setattr(s08, "mock_llm", lambda _m, _p, _t="": echo)
    assert selector.select(req, token_override=100).selected_ids == ()
    pytest.fail("DID NOT RAISE ValueError")


def test_select_keep_allowlisted_id_when_model_echoes_bracket_form(
    s08, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Safe IDs round-trip via select: model echo [id] → allowlisted selected_ids."""
    selector = s08.MemorySelectorRouter(s08.ModelRouter())
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
    assert selector.select(request, token_override=100).selected_ids == ("mem_auth",)
