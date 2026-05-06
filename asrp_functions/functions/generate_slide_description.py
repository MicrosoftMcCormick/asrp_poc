# Implement the `generate_slide_description` HTTP function for the HSBC ASRP Phase 1 POC.
#
# Behaviour:
#  1. Parse the request as GenerateSlideRequest.
#  2. Resolve the static prompt via PromptRegistry.get(slide_id, prompt_version).
#  3. Call AIFoundryClient.generate_slide(...) to get a JSON slide description.
#  4. Validate against the per-slide schema via slide_schemas.validate(...).
#  5. On schema failure, retry exactly once with a stricter "schema reminder"
#     instruction (delegate to AIFoundryClient.generate_slide_strict).
#  6. On second failure, return status="failed" with empty description and
#     the failure reason in data_gaps.
#  7. On transient Azure OpenAI errors (timeout / 5xx), use exponential
#     backoff: 3 attempts, 2s/5s/10s.
#  8. Always populate model_metadata with model name, prompt_version,
#     schema_version, and UTC timestamp.
#  9. Return GenerateSlideResponse.

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

import azure.functions as func
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from pydantic import ValidationError

from shared.ai_foundry_client import AIFoundryClient
from shared.logging import get_logger
from shared.models.contracts import GenerateSlideRequest, GenerateSlideResponse
from shared.prompt_registry import PromptNotFoundError, PromptRegistry
from shared.slide_schemas import validate as validate_slide

logger = get_logger(__name__)

# Exponential backoff schedule for transient Azure OpenAI failures.
_RETRY_DELAYS_SECONDS: tuple[float, ...] = (2.0, 5.0, 10.0)

# Lazily-constructed singletons (process-wide).
_PROMPT_REGISTRY: PromptRegistry | None = None
_AI_FOUNDRY_CLIENT: AIFoundryClient | None = None


def _registry() -> PromptRegistry:
    global _PROMPT_REGISTRY
    if _PROMPT_REGISTRY is None:
        _PROMPT_REGISTRY = PromptRegistry()
    return _PROMPT_REGISTRY


def _ai_client() -> AIFoundryClient:
    global _AI_FOUNDRY_CLIENT
    if _AI_FOUNDRY_CLIENT is None:
        _AI_FOUNDRY_CLIENT = AIFoundryClient(prompt_registry=_registry())
    return _AI_FOUNDRY_CLIENT


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_transient(exc: BaseException) -> bool:
    """Return True for Azure OpenAI errors safe to retry."""
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        return isinstance(status, int) and status >= 500
    return False


def _call_with_backoff(
    op_name: str, slide_id: str, fn: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """Invoke ``fn`` with exponential backoff for transient AOAI errors."""
    last_exc: BaseException | None = None
    for attempt_index, delay in enumerate(_RETRY_DELAYS_SECONDS, start=1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - boundary handler
            last_exc = exc
            if not _is_transient(exc):
                raise
            if attempt_index == len(_RETRY_DELAYS_SECONDS):
                logger.error(
                    f"{op_name}.transient_exhausted",
                    extra={
                        "slide_id": slide_id,
                        "attempt": attempt_index,
                        "error_type": type(exc).__name__,
                    },
                )
                raise
            logger.warning(
                f"{op_name}.transient_retry",
                extra={
                    "slide_id": slide_id,
                    "attempt": attempt_index,
                    "next_delay_s": delay,
                    "error_type": type(exc).__name__,
                },
            )
            time.sleep(delay)
    # Defensive — loop above either returns or raises.
    raise RuntimeError("unreachable") from last_exc  # pragma: no cover


def _fallback_metadata(
    *,
    prompt_version: str,
    schema_version: str,
    deployment: str | None = None,
) -> dict[str, Any]:
    return {
        "model": None,
        "deployment": deployment,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "generated_at": _now_iso(),
    }


def _failure_response(
    *,
    request: GenerateSlideRequest,
    reason: str,
    model_metadata: dict[str, Any] | None = None,
) -> GenerateSlideResponse:
    metadata = model_metadata or _fallback_metadata(
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
    )
    metadata.setdefault("generated_at", _now_iso())
    metadata.setdefault("prompt_version", request.prompt_version)
    metadata.setdefault("schema_version", request.schema_version)
    return GenerateSlideResponse(
        slide_id=request.slide_id,
        status="failed",
        description={},
        data_gaps=[reason],
        model_metadata=metadata,
    )


def _format_validation_errors(errors: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for err in errors:
        loc = ".".join(str(p) for p in (err.get("loc") or ())) or "<root>"
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts) or "schema_validation_failed"


# --------------------------------------------------------------------- entry


def main(req: func.HttpRequest) -> func.HttpResponse:
    """Azure Functions HTTP entrypoint."""
    try:
        body = req.get_json()
    except ValueError as exc:
        logger.warning("generate_slide_description.invalid_json", extra={"detail": str(exc)})
        return func.HttpResponse(
            json.dumps({"error": "invalid_json", "detail": str(exc)}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        request = GenerateSlideRequest.model_validate(body)
    except ValidationError as exc:
        logger.warning(
            "generate_slide_description.validation_error",
            extra={"errors": exc.errors(include_url=False, include_input=False)},
        )
        return func.HttpResponse(
            json.dumps(
                {
                    "error": "validation_error",
                    "detail": exc.errors(include_url=False, include_input=False),
                }
            ),
            status_code=422,
            mimetype="application/json",
        )

    response = _handle(request)
    return func.HttpResponse(
        body=response.model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )


# --------------------------------------------------------------------- core


def _handle(request: GenerateSlideRequest) -> GenerateSlideResponse:
    log_ctx = {
        "slide_id": request.slide_id,
        "customer_id": request.customer_id,
        "run_id": request.run_id,
        "user_id": request.user_id,
        "prompt_version": request.prompt_version,
        "schema_version": request.schema_version,
    }
    logger.info("generate_slide_description.start", extra=log_ctx)

    # Step 2 — resolve the static prompt up front so a missing prompt
    # surfaces deterministically before any model invocation.
    try:
        _registry().get(request.slide_id, request.prompt_version)
    except PromptNotFoundError as exc:
        logger.error("generate_slide_description.prompt_not_found", extra=log_ctx)
        return _failure_response(
            request=request, reason=f"prompt_not_found: {exc}"
        )

    client = _ai_client()

    # Step 3 — first model call with backoff for transient AOAI faults.
    try:
        first = _call_with_backoff(
            "generate_slide_description.generate",
            request.slide_id,
            lambda: client.generate_slide(
                slide_id=request.slide_id,
                customer_id=request.customer_id,
                prompt_version=request.prompt_version,
                schema_version=request.schema_version,
                run_id=request.run_id,
            ),
        )
    except (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError) as exc:
        logger.error(
            "generate_slide_description.transient_failure",
            extra={**log_ctx, "error_type": type(exc).__name__},
        )
        return _failure_response(
            request=request, reason=f"azure_openai_unavailable: {type(exc).__name__}"
        )
    except ValueError as exc:
        # Non-JSON / non-object content from the model.
        logger.error(
            "generate_slide_description.bad_model_output",
            extra={**log_ctx, "detail": str(exc)},
        )
        return _failure_response(
            request=request, reason=f"model_output_invalid: {exc}"
        )

    # Step 4 — validate first response against the per-slide schema.
    description = first.get("description") or {}
    metadata = first.get("model_metadata") or _fallback_metadata(
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
    )
    first_result = validate_slide(request.slide_id, description)

    if first_result.is_valid:
        return _build_success_response(request, first_result, metadata)

    logger.warning(
        "generate_slide_description.schema_invalid_first",
        extra={
            **log_ctx,
            "validation_errors": first_result.errors,
        },
    )

    # Step 5 — exactly one strict retry, also with backoff.
    try:
        second = _call_with_backoff(
            "generate_slide_description.generate_strict",
            request.slide_id,
            lambda: client.generate_slide_strict(
                slide_id=request.slide_id,
                customer_id=request.customer_id,
                prompt_version=request.prompt_version,
                schema_version=request.schema_version,
                run_id=request.run_id,
            ),
        )
    except (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError) as exc:
        logger.error(
            "generate_slide_description.strict_transient_failure",
            extra={**log_ctx, "error_type": type(exc).__name__},
        )
        return _failure_response(
            request=request,
            reason=f"azure_openai_unavailable_strict: {type(exc).__name__}",
            model_metadata=metadata,
        )
    except ValueError as exc:
        logger.error(
            "generate_slide_description.strict_bad_model_output",
            extra={**log_ctx, "detail": str(exc)},
        )
        return _failure_response(
            request=request,
            reason=f"model_output_invalid_strict: {exc}",
            model_metadata=metadata,
        )

    description = second.get("description") or {}
    metadata = second.get("model_metadata") or metadata
    second_result = validate_slide(request.slide_id, description)

    if second_result.is_valid:
        return _build_success_response(request, second_result, metadata)

    # Step 6 — schema validation failed twice.
    logger.error(
        "generate_slide_description.schema_invalid_strict",
        extra={**log_ctx, "validation_errors": second_result.errors},
    )
    return _failure_response(
        request=request,
        reason=(
            "schema_validation_failed: "
            + _format_validation_errors(second_result.errors)
        ),
        model_metadata=metadata,
    )


def _build_success_response(
    request: GenerateSlideRequest,
    validation_result: Any,  # ValidationResult from slide_schemas
    metadata: dict[str, Any],
) -> GenerateSlideResponse:
    if validation_result.model is None:  # pragma: no cover - defensive
        return _failure_response(
            request=request,
            reason="schema_validation_failed: model unavailable",
            model_metadata=metadata,
        )

    description = validation_result.model.model_dump(by_alias=True, mode="json")
    data_gaps = list(description.get("data_gaps") or [])
    status = "partial" if data_gaps else "ok"

    metadata = {**metadata}
    metadata.setdefault("prompt_version", request.prompt_version)
    metadata.setdefault("schema_version", request.schema_version)
    metadata.setdefault("generated_at", _now_iso())

    response = GenerateSlideResponse(
        slide_id=request.slide_id,
        status=status,
        description=description,
        data_gaps=data_gaps,
        model_metadata=metadata,
    )
    logger.info(
        "generate_slide_description.ok",
        extra={
            "slide_id": request.slide_id,
            "customer_id": request.customer_id,
            "run_id": request.run_id,
            "status": status,
            "data_gap_count": len(data_gaps),
        },
    )
    return response


__all__ = ["main"]
