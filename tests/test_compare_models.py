"""Offline tests for the model check: catalog parsing, cost estimate and recommendation rules.

No request reaches OpenRouter; catalog responses are faked.
"""

import pytest

from scripts import compare_models
from src.agent import llm

SETTINGS = llm.LLMSettings("test-key", "https://openrouter.ai/api/v1", "main/m", "fb/m", 60.0)
CATALOG = {
    "main/m": {
        "id": "main/m",
        "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
        "supported_parameters": ["tools", "tool_choice", "temperature"],
    },
    "notools/m": {
        "id": "notools/m",
        "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
        "supported_parameters": ["temperature"],
    },
}
ENDPOINTS = [
    {"provider_name": "A", "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}},
    {"provider_name": "A", "pricing": {"prompt": "0.0000003", "completion": "0.0000006"}},
    {"provider_name": "B", "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}},
]


@pytest.fixture
def fake_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_endpoints returns ENDPOINTS for any listed model."""
    monkeypatch.setattr(llm, "model_endpoints", lambda model, settings=None: list(ENDPOINTS))


def test_check_model_reads_price_tools_and_providers(fake_endpoints: None) -> None:
    check = compare_models.check_model("main", "main/m", CATALOG, SETTINGS)
    assert check.ok
    assert check.input_price == pytest.approx(0.1) and check.output_price == pytest.approx(0.2)
    assert check.max_input_price == pytest.approx(0.3)
    assert (check.providers, check.endpoints) == (2, 3)


def test_missing_model_or_no_tools_fails(fake_endpoints: None) -> None:
    assert not compare_models.check_model("main", "unknown/m", CATALOG, SETTINGS).ok
    assert not compare_models.check_model("main", "notools/m", CATALOG, SETTINGS).ok


def test_estimate_worst_case_is_higher(fake_endpoints: None) -> None:
    check = compare_models.check_model("main", "main/m", CATALOG, SETTINGS)
    typical = compare_models.estimate_cost(check, 20, 3000, worst=False)
    worst = compare_models.estimate_cost(check, 20, 3000, worst=True)
    assert 0 < typical < worst


ROLES = {"main/m": "main", "fb/m": "fallback", "ref/m": "reference"}


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ({"main/m": 20, "fb/m": 18, "ref/m": 20}, "Keep the owner's choice"),
        ({"main/m": 18, "fb/m": 18, "ref/m": 18}, "Keep the owner's choice"),  # nobody better
        ({"main/m": 18, "fb/m": 17, "ref/m": 19}, "Consider switching the main model"),
        ({"main/m": 19, "fb/m": 16, "ref/m": 19}, "Consider a different fallback"),
    ],
)
def test_recommendation_rules(scores: dict[str, int], expected: str) -> None:
    assert expected in compare_models.recommendation(scores, ROLES)


def test_model_endpoints_unknown_model_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def not_found(path: str, settings: llm.LLMSettings) -> dict:
        raise llm.LLMRequestError("404") from urllib.error.HTTPError(path, 404, "nf", {}, None)

    monkeypatch.setattr(llm, "_get_json", not_found)
    assert llm.model_endpoints("unknown/m", SETTINGS) == []


def test_catalog_requests_are_blocked_in_tests() -> None:
    with pytest.raises(llm.LLMUnavailableError):
        llm.list_models(SETTINGS)
