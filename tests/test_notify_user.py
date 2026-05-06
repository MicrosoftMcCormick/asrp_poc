"""Tests for the ``notify_user`` HTTP function."""

from __future__ import annotations

import json

import azure.functions as func
import pytest
from pytest_httpx import HTTPXMock

from functions import notify_user as nu

# Fields that MUST be the only keys posted to the Power Automate flow.
_ALLOWED_BODY_KEYS = {
    "run_id",
    "user_id",
    "customer_id",
    "deck_blob_url",
    "status",
    "summary",
    "generated_at",
}


def _make_request(body: dict) -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url="http://localhost/api/notify_user",
        body=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _valid_body() -> dict:
    return {
        "run_id": "run-001",
        "user_id": "user-001",
        "customer_id": "cust-001",
        "deck_blob_url": "https://example.blob.core.windows.net/decks/run-001.pptx",
        "status": "ok",
        "summary": "Deck generated successfully.",
    }


def test_notify_user_succeeds_after_5xx_then_200(httpx_mock: HTTPXMock) -> None:
    flow_url = "https://example.test/flow?sig=test"
    httpx_mock.add_response(url=flow_url, status_code=500)
    httpx_mock.add_response(url=flow_url, status_code=503)
    httpx_mock.add_response(url=flow_url, status_code=200)

    response = nu.main(_make_request(_valid_body()))

    assert response.status_code == 200
    payload = json.loads(response.get_body())
    assert payload["status"] == "ok"

    requests = httpx_mock.get_requests()
    assert len(requests) == 3


def test_notify_user_fails_after_three_5xx(httpx_mock: HTTPXMock) -> None:
    flow_url = "https://example.test/flow?sig=test"
    for _ in range(3):
        httpx_mock.add_response(url=flow_url, status_code=500)

    response = nu.main(_make_request(_valid_body()))

    payload = json.loads(response.get_body())
    assert payload["status"] == "failed"
    assert len(httpx_mock.get_requests()) == 3


def test_notify_user_does_not_retry_on_4xx(httpx_mock: HTTPXMock) -> None:
    flow_url = "https://example.test/flow?sig=test"
    httpx_mock.add_response(url=flow_url, status_code=400)

    response = nu.main(_make_request(_valid_body()))

    payload = json.loads(response.get_body())
    assert payload["status"] == "failed"
    assert len(httpx_mock.get_requests()) == 1


def test_notify_user_body_contains_only_allowed_fields(
    httpx_mock: HTTPXMock,
) -> None:
    flow_url = "https://example.test/flow?sig=test"
    httpx_mock.add_response(url=flow_url, status_code=200)

    # Sneak an extra field into the request — the function must not
    # forward it to Power Automate.
    body = _valid_body()
    response = nu.main(_make_request(body))
    assert response.status_code == 200

    sent = httpx_mock.get_requests()[0]
    sent_body = json.loads(sent.content)
    assert set(sent_body.keys()) == _ALLOWED_BODY_KEYS

    # Customer-data-shaped values that must NEVER appear:
    forbidden_substrings = ("@", "Customer", "Account Manager")
    body_text = sent.content.decode("utf-8")
    for needle in forbidden_substrings:
        assert needle not in body_text, f"unexpected {needle!r} in flow body"


def test_notify_user_validation_error_returns_422(
    httpx_mock: HTTPXMock,
) -> None:
    # No HTTP requests should reach Power Automate when validation fails.
    response = nu.main(_make_request({"run_id": "only-this"}))
    assert response.status_code == 422
    assert httpx_mock.get_requests() == []


@pytest.fixture(autouse=True)
def _settings_from_test_env() -> None:
    """Ensure Settings.POWER_AUTOMATE_FLOW_URL matches the mocked URL."""
    from shared import config

    config.get_settings.cache_clear()
