"""OpenRouter vision provider + the metered-spend pre-flight gate.

The load-bearing property here is that spending is NOT authorised by constructing
the provider - authorisation happens only in ingest_pdf.py's pre-flight gate,
after a printed estimate. A regression to constructor-authorisation would silently
unlock every metered path (the flag is process-wide), so it is tested directly.
"""

from unittest.mock import patch

import pytest

import anomalica_common.llm.transport as transport
from anomalica_common.llm import spend_confirmed

from extraction.openrouter_vision import OpenRouterVisionProvider
from ingest_pdf import _estimate_vision_cost, _resolve_provider_kind


@pytest.fixture(autouse=True)
def _key_and_reset(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    transport._metered_spend_authorised = False
    yield
    transport._metered_spend_authorised = False


def test_constructing_the_provider_does_not_authorise_spend():
    assert transport._metered_spend_authorised is False
    OpenRouterVisionProvider("openai/gpt-5.6-luna")
    assert transport._metered_spend_authorised is False


def test_provider_requires_the_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        OpenRouterVisionProvider("openai/gpt-5.6-luna")


def test_extract_routes_through_the_gateway(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    f = tmp_path / "one.pdf"
    doc.save(f)

    provider = OpenRouterVisionProvider("openai/gpt-5.6-luna")
    with patch(
        "extraction.openrouter_vision.call_with_pages",
        return_value=("```\n---\nschema: test\n---\n\nBody.\n```", {"cost_usd": 0.01}),
    ) as gw:
        content, meta = provider.extract(f)
    gw.assert_called_once()
    # pages passed as image data URLs, one per page
    pages_arg = gw.call_args[0][3]
    assert len(pages_arg) == 1 and pages_arg[0].startswith("data:image/png;base64,")
    assert content.strip() == "---\nschema: test\n---\n\nBody."  # fences stripped


def test_estimate_is_page_based_and_errs_high():
    small = _estimate_vision_cost(10, "openai/gpt-5.6-luna")
    big = _estimate_vision_cost(400, "openai/gpt-5.6-luna")
    assert small["pages"] == 10
    assert big["usd"] > small["usd"]
    # errs high: the band's upper bound exceeds the point estimate
    assert big["usd_high"] > big["usd"] > big["usd_low"]


def test_large_run_refuses_without_confirmation():
    # A 400-page run is above the auto-approve ceiling; ungated it must refuse and
    # authorise nothing, exactly as the gate in ingest_pdf.py drives it.
    est = _estimate_vision_cost(400, "openai/gpt-5.6-luna")
    out = []
    ok = spend_confirmed(est, "openai/gpt-5.6-luna", confirm=False, echo=out.append)
    assert ok is False
    assert transport._metered_spend_authorised is False
    assert any("REFUSING" in line for line in out)


def test_small_run_authorises_on_confirmation():
    est = _estimate_vision_cost(5, "openai/gpt-5.6-luna")
    ok = spend_confirmed(est, "openai/gpt-5.6-luna", confirm=True, echo=lambda _: None)
    assert ok is True
    assert transport._metered_spend_authorised is True


# --- provider routing: INGEST_USE_API must govern the vision path -------------


def test_luna_default_routes_to_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("INGEST_USE_API", raising=False)
    assert _resolve_provider_kind("openai/gpt-5.6-luna") == "openrouter"


def test_use_api_zero_forces_subscription_even_with_luna_default(monkeypatch):
    # The load-bearing fix: "0" is a real off-switch over the path that spends,
    # not a no-op. Previously the Luna path ignored it entirely.
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("INGEST_USE_API", "0")
    assert _resolve_provider_kind("openai/gpt-5.6-luna") == "subscription"


def test_missing_openrouter_key_downgrades_to_subscription(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("INGEST_USE_API", raising=False)
    assert _resolve_provider_kind("openai/gpt-5.6-luna") == "subscription"


def test_plain_model_uses_subscription_unless_use_api_1(monkeypatch):
    monkeypatch.delenv("INGEST_USE_API", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _resolve_provider_kind("sonnet") == "subscription"
    monkeypatch.setenv("INGEST_USE_API", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert _resolve_provider_kind("sonnet") == "api"
