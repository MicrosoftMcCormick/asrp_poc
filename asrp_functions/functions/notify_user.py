# Implement the `notify_user` HTTP function for the HSBC ASRP Phase 1 POC.
#
# The function calls a configured Power Automate flow over HTTPS to
# raise an in-app notification and/or email to the originating CSP user.
#
# Behaviour:
#  1. Parse NotifyUserRequest (run_id, user_id, customer_id,
#     deck_blob_url, status, summary).
#  2. POST to the Power Automate flow URL from shared.config (no secret
#     in code; URL carries the flow's signature parameter and is
#     supplied via app settings / Key Vault reference).
#  3. Body: { run_id, user_id, customer_id, deck_blob_url, status,
#            summary, generated_at }.
#  4. Retry policy: 3 attempts with exponential backoff 1s/3s/8s on
#     transient HTTP 5xx and timeouts.
#  5. Never include customer data fields in the body — only the IDs,
#     the deck URL, and the orchestrator-supplied summary string.
#  6. Return NotifyUserResponse with `status` ("ok" | "failed") and
#     `notified_at`.
#
# The Power Automate flow itself is not built here; this function only
# triggers it.

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import azure.functions as func
import httpx
from pydantic import ValidationError

from shared.config import get_settings
from shared.logging import get_logger
from shared.models.contracts import NotifyUserRequest, NotifyUserResponse

logger = get_logger(__name__)

_BX_EVENT = "BX_PowerAutomate"
_RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 3.0, 8.0)
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_transient_status(status_code: int) -> bool:
    return status_code >= 500


def _is_transient_exception(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


def _build_body(request: NotifyUserRequest, generated_at: datetime) -> dict[str, Any]:
    """Build the Power Automate body — IDs, URL, status, summary only.

    No additional customer detail (names, emails, etc.) is permitted.
    """
    return {
        "run_id": request.run_id,
        "user_id": request.user_id,
        "customer_id": request.customer_id,
        "deck_blob_url": request.deck_blob_url,
        "status": request.status,
        "summary": request.summary,
        "generated_at": generated_at.isoformat(),
    }


def _dispatch(body: dict[str, Any]) -> tuple[bool, int | None]:
    """POST ``body`` to the configured flow URL with retries.

    Returns ``(ok, last_status_code)``. ``last_status_code`` is ``None``
    when the call never produced an HTTP response (e.g. all attempts
    timed out).
    """
    settings = get_settings()
    url = str(settings.POWER_AUTOMATE_FLOW_URL)
    last_status: int | None = None

    with httpx.Client(timeout=_TIMEOUT) as client:
        for attempt_index, delay in enumerate(_RETRY_DELAYS_SECONDS, start=1):
            logger.info(
                _BX_EVENT,
                extra={"phase": "request", "attempt": attempt_index},
            )
            try:
                response = client.post(url, json=body)
            except httpx.HTTPError as exc:
                last_status = None
                transient = _is_transient_exception(exc)
                logger.warning(
                    _BX_EVENT,
                    extra={
                        "phase": "transport_error",
                        "attempt": attempt_index,
                        "error_type": type(exc).__name__,
                        "transient": transient,
                    },
                )
                if not transient or attempt_index == len(_RETRY_DELAYS_SECONDS):
                    return False, None
                time.sleep(delay)
                continue

            last_status = response.status_code
            if response.status_code < 400:
                logger.info(
                    _BX_EVENT,
                    extra={
                        "phase": "ok",
                        "attempt": attempt_index,
                        "status_code": response.status_code,
                    },
                )
                return True, response.status_code

            transient = _is_transient_status(response.status_code)
            logger.warning(
                _BX_EVENT,
                extra={
                    "phase": "http_error",
                    "attempt": attempt_index,
                    "status_code": response.status_code,
                    "transient": transient,
                },
            )
            if not transient or attempt_index == len(_RETRY_DELAYS_SECONDS):
                return False, response.status_code
            time.sleep(delay)

    return False, last_status


# --------------------------------------------------------------------- entry


def main(req: func.HttpRequest) -> func.HttpResponse:
    """Azure Functions HTTP entrypoint."""
    try:
        body = req.get_json()
    except ValueError as exc:
        logger.warning("notify_user.invalid_json", extra={"detail": str(exc)})
        return _error_response("invalid_json", str(exc), 400)

    try:
        request = NotifyUserRequest.model_validate(body)
    except ValidationError as exc:
        logger.warning(
            "notify_user.validation_error",
            extra={"errors": exc.errors(include_url=False, include_input=False)},
        )
        return _error_response(
            "validation_error",
            exc.errors(include_url=False, include_input=False),
            422,
        )

    generated_at = _now_utc()
    payload = _build_body(request, generated_at)

    logger.info(
        "notify_user.start",
        extra={
            "run_id": request.run_id,
            "user_id": request.user_id,
            "customer_id": request.customer_id,
            "deck_status": request.status,
        },
    )

    ok, _last_status = _dispatch(payload)
    response = NotifyUserResponse(
        status="ok" if ok else "failed",
        notified_at=_now_utc(),
    )

    logger.info(
        "notify_user.done",
        extra={
            "run_id": request.run_id,
            "user_id": request.user_id,
            "customer_id": request.customer_id,
            "result_status": response.status,
        },
    )

    return func.HttpResponse(
        body=response.model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )


def _error_response(
    error: str, detail: Any, status_code: int
) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps({"error": error, "detail": detail}),
        status_code=status_code,
        mimetype="application/json",
    )


__all__ = ["main"]
