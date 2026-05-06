"""Retry-behaviour tests for the AI Foundry call path.

The :class:`AIFoundryClient` deliberately does not retry; the
orchestration layer (the ``generate_slide_description`` function) wraps
it in :func:`functions.generate_slide_description._call_with_backoff`.
These tests pin the retry semantics there.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from functions import generate_slide_description as gsd


def _connection_error() -> APIConnectionError:
    return APIConnectionError(request=httpx.Request("POST", "https://example.test/x"))


def _status_error(code: int) -> APIStatusError:
    response = httpx.Response(
        status_code=code,
        request=httpx.Request("POST", "https://example.test/x"),
    )
    return APIStatusError("server error", response=response, body=None)


def test_call_with_backoff_succeeds_after_two_transient_failures() -> None:
    fn = MagicMock(
        side_effect=[
            _connection_error(),
            _status_error(503),
            {"description": {"ok": True}, "model_metadata": {"model": "m"}},
        ]
    )

    result = gsd._call_with_backoff("op", "footprint_v1", fn)

    assert result == {"description": {"ok": True}, "model_metadata": {"model": "m"}}
    assert fn.call_count == 3


def test_call_with_backoff_exhausts_after_three_transient_failures() -> None:
    fn = MagicMock(
        side_effect=[
            _connection_error(),
            _connection_error(),
            _status_error(502),
        ]
    )

    with pytest.raises(APIStatusError):
        gsd._call_with_backoff("op", "footprint_v1", fn)
    assert fn.call_count == 3


def test_call_with_backoff_does_not_retry_non_transient() -> None:
    fn = MagicMock(side_effect=ValueError("bad json"))
    with pytest.raises(ValueError):
        gsd._call_with_backoff("op", "footprint_v1", fn)
    assert fn.call_count == 1


def test_call_with_backoff_does_not_retry_4xx_status() -> None:
    fn = MagicMock(side_effect=_status_error(400))
    with pytest.raises(APIStatusError):
        gsd._call_with_backoff("op", "footprint_v1", fn)
    assert fn.call_count == 1
