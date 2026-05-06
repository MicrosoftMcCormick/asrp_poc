# Define a small error taxonomy for the HSBC ASRP Phase 1 POC:
# - TransientAzureOpenAIError    (retryable)
# - SchemaValidationError        (one retry, then fail the slide)
# - GroundedDataMissingError     (not an error — record in data_gaps and continue)
# - TemplateAccessError          (terminal for the run)
# - DeckAssemblyError            (per-slide, recoverable; whole-run, terminal)
#
# Each carries: run_id, customer_id, slide_id (optional), cause (str),
# is_retryable (bool). Map these to function HTTP responses consistently
# across all three functions.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final

import azure.functions as func


# ---------------------------------------------------------- error categories


@dataclass
class AsrpError(Exception):
    """Base class for ASRP domain errors.

    All subclasses carry the run/customer/slide context so the HTTP
    boundary can produce a structured, customer-data-free response.
    Subclasses pin ``code`` and ``default_retryable``; ``is_retryable``
    is settable per instance for cases where a normally-retryable
    error has exhausted its budget.
    """

    code: str = field(init=False)
    default_retryable: bool = field(init=False, default=False)

    run_id: str = ""
    customer_id: str = ""
    slide_id: str | None = None
    cause: str = ""
    is_retryable: bool | None = None

    def __post_init__(self) -> None:
        # Use the dataclass-managed message so str(exc) is meaningful.
        Exception.__init__(self, self.cause or self.code)
        if self.is_retryable is None:
            self.is_retryable = self.default_retryable

    # ------------------------------------------------------- serialisation

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-safe dict free of customer data bodies."""
        payload: dict[str, Any] = {
            "error": self.code,
            "is_retryable": bool(self.is_retryable),
            "run_id": self.run_id,
            "customer_id": self.customer_id,
            "cause": self.cause or self.code,
        }
        if self.slide_id is not None:
            payload["slide_id"] = self.slide_id
        return payload


class TransientAzureOpenAIError(AsrpError):
    """Transient Azure OpenAI fault (timeout, 5xx, rate limit). Retryable."""

    code = "transient_azure_openai_error"
    default_retryable = True


class SchemaValidationError(AsrpError):
    """Per-slide schema validation failure.

    Caller policy: retry exactly once with the strict-mode prompt; on
    second failure, fail the slide and continue the run.
    """

    code = "schema_validation_error"
    default_retryable = True


class GroundedDataMissingError(AsrpError):
    """Not an error — represents missing grounded data for a field.

    Raised by lower layers only when callers explicitly opt-in; the
    canonical handling for missing grounded data is to return ``null``
    in the slide JSON and append the field name to ``data_gaps``.
    """

    code = "grounded_data_missing"
    default_retryable = False


class TemplateAccessError(AsrpError):
    """Failed to read the CSR template or required asset. Terminal for the run."""

    code = "template_access_error"
    default_retryable = False


class DeckAssemblyError(AsrpError):
    """Deck assembly failure.

    Per-slide failures are recoverable: the slide is recorded in
    ``failed_slide_ids`` and the run continues. A whole-run failure
    (e.g. the template open itself fails) is terminal.
    """

    code = "deck_assembly_error"
    default_retryable = False


# ------------------------------------------------------------ HTTP mapping

# code -> default HTTP status returned to upstream callers.
HTTP_STATUS_BY_CODE: Final[dict[str, int]] = {
    TransientAzureOpenAIError.code: 503,
    SchemaValidationError.code: 422,
    GroundedDataMissingError.code: 200,  # not an error path
    TemplateAccessError.code: 502,
    DeckAssemblyError.code: 500,
}


def http_status_for(error: AsrpError) -> int:
    """Return the canonical HTTP status code for ``error``."""
    return HTTP_STATUS_BY_CODE.get(error.code, 500)


def to_http_response(
    error: AsrpError, *, status_code: int | None = None
) -> func.HttpResponse:
    """Convert ``error`` into a JSON :class:`HttpResponse`.

    The body contains only the structured taxonomy fields — never
    customer data, payloads or stack traces.
    """
    body = json.dumps(error.to_payload())
    return func.HttpResponse(
        body=body,
        status_code=status_code if status_code is not None else http_status_for(error),
        mimetype="application/json",
    )


__all__ = [
    "AsrpError",
    "TransientAzureOpenAIError",
    "SchemaValidationError",
    "GroundedDataMissingError",
    "TemplateAccessError",
    "DeckAssemblyError",
    "HTTP_STATUS_BY_CODE",
    "http_status_for",
    "to_http_response",
]
