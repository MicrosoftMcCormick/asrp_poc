"""Power Automate notification dispatcher."""

from __future__ import annotations

from typing import Any

import httpx

from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def dispatch_notification(payload: dict[str, Any]) -> int:
    """POST ``payload`` to the Power Automate flow URL.

    The flow URL embeds its own short-lived signature; no additional
    authentication is performed by this function. Returns the HTTP
    status code so the caller can surface flow-level outcomes.
    """
    settings = get_settings()
    url = str(settings.POWER_AUTOMATE_FLOW_URL)

    logger.info("power_automate.dispatch", extra={"keys": sorted(payload.keys())})
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.post(url, json=payload)

    if response.status_code >= 400:
        logger.error(
            "power_automate.failed",
            extra={"status_code": response.status_code, "body": response.text[:500]},
        )
        response.raise_for_status()

    logger.info("power_automate.ok", extra={"status_code": response.status_code})
    return response.status_code
