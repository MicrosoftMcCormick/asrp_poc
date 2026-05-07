"""Azure Functions v4 (Python) entrypoint for the ASRP Function App.

Three HTTP-triggered functions exposed:

* ``generate_slide_description`` — RAG-grounded JSON for one slide.
* ``assemble_deck`` — render the HSBC CSR PPTX from per-slide JSON.
* ``notify_user`` — fan-out via Power Automate flow.

Auth model: HTTP routes are ``AuthLevel.FUNCTION`` for Phase 1 — the
Copilot Studio Agent calls these endpoints with a function key. All
outbound Azure SDK calls use Managed Identity via :mod:`shared.auth`.
"""


from __future__ import annotations

import json
import logging
from typing import Callable, TypeVar

import azure.functions as func
from pydantic import BaseModel, ValidationError

try:
    from asrp_functions.services.deck_builder import assemble_and_upload
    from asrp_functions.services.openai_client import generate_slide_json
    from asrp_functions.services.power_automate import dispatch_notification
    from asrp_functions.services.prompts import get_slide_prompt
    from asrp_functions.services.search import retrieve_grounding
    from asrp_functions.shared.logging import get_logger
    from asrp_functions.shared.models import (
        AssembleDeckRequest,
        AssembleDeckResponse,
        ErrorResponse,
        GenerateSlideRequest,
        GenerateSlideResponse,
        NotifyUserRequest,
        NotifyUserResponse,
        SlideDescription,
    )
except Exception:
    logging.exception("FUNCTION_APP_IMPORT_FAILURE")
    raise


logger = get_logger(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


# --------------------------------------------------------------------- helpers


def _json_response(model: BaseModel, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body=model.model_dump_json(),
        status_code=status_code,
        mimetype="application/json",
    )


def _error_response(
    *, error: str, status_code: int, detail: str | None = None, context: dict | None = None
) -> func.HttpResponse:
    return _json_response(
        ErrorResponse(error=error, detail=detail, context=context),
        status_code=status_code,
    )



from typing import TypeVar

T = TypeVar("T", bound=BaseModel)

def _parse_body(
    req: func.HttpRequest, model_cls: type[T]
) -> T | func.HttpResponse:
    try:
        raw = req.get_body()
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        return _error_response(
            error="invalid_json", status_code=400, detail=str(exc)
        )
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        return _error_response(
            error="validation_error",
            status_code=422,
            detail=exc.errors(include_url=False, include_input=False).__repr__(),
        )


def _safe_handler(name: str, fn: Callable[[func.HttpRequest], func.HttpResponse]):
    """Wrap a handler so unhandled exceptions become structured 500s."""

    def wrapper(req: func.HttpRequest) -> func.HttpResponse:
        try:
            return fn(req)
        except Exception as exc:  # noqa: BLE001 - boundary handler
            logger.exception(f"{name}.unhandled_error")
            return _error_response(
                error="internal_error",
                status_code=500,
                detail=str(exc),
            )

    wrapper.__name__ = fn.__name__
    return wrapper


# ------------------------------------------------------------ generate_slide


@app.function_name(name="generate_slide_description")
@app.route(route="slides/generate", methods=["POST"])
def generate_slide_description(req: func.HttpRequest) -> func.HttpResponse:
    return _safe_handler("generate_slide_description", _generate_slide_impl)(req)


def _generate_slide_impl(req: func.HttpRequest) -> func.HttpResponse:
    parsed = _parse_body(req, GenerateSlideRequest)
    if isinstance(parsed, func.HttpResponse):
        return parsed

    logger.info(
        "generate_slide.start",
        extra={
            "customer_id": parsed.customer_id,
            "slide_id": parsed.slide_id,
            "run_id": parsed.run_metadata.run_id,
        },
    )

    try:
        slide_prompt = get_slide_prompt(parsed.slide_id)
    except KeyError:
        return _error_response(
            error="unknown_slide_id",
            status_code=400,
            detail=f"slide_id '{parsed.slide_id}' is not registered.",
        )

    grounding_hits = retrieve_grounding(
        customer_id=parsed.customer_id, slide_id=parsed.slide_id
    )

    raw_content = generate_slide_json(
        slide_prompt=slide_prompt,
        grounding_hits=grounding_hits,
        customer_id=parsed.customer_id,
        slide_id=parsed.slide_id,
    )

    data_gaps = raw_content.pop("data_gaps", []) or []
    if not isinstance(data_gaps, list):
        data_gaps = []

    citations = [
        {"id": h.get("id"), "score": h.get("score"), "source": h.get("source")}
        for h in grounding_hits
    ]

    slide = SlideDescription(
        customer_id=parsed.customer_id,
        slide_id=parsed.slide_id,
        content=raw_content,
        data_gaps=[str(g) for g in data_gaps],
        citations=citations,
    )

    logger.info(
        "generate_slide.ok",
        extra={
            "customer_id": parsed.customer_id,
            "slide_id": parsed.slide_id,
            "run_id": parsed.run_metadata.run_id,
            "data_gap_count": len(slide.data_gaps),
        },
    )
    return _json_response(
        GenerateSlideResponse(slide=slide, run_id=parsed.run_metadata.run_id)
    )


# ----------------------------------------------------------------- assemble


@app.function_name(name="assemble_deck")
@app.route(route="deck/assemble", methods=["POST"])
def assemble_deck(req: func.HttpRequest) -> func.HttpResponse:
    return _safe_handler("assemble_deck", _assemble_deck_impl)(req)


def _assemble_deck_impl(req: func.HttpRequest) -> func.HttpResponse:
    parsed = _parse_body(req, AssembleDeckRequest)
    if isinstance(parsed, func.HttpResponse):
        return parsed

    run_id = parsed.run_metadata.run_id
    logger.info(
        "assemble_deck.start",
        extra={
            "customer_id": parsed.customer_id,
            "run_id": run_id,
            "slide_count": len(parsed.slides),
        },
    )

    blob_url, deck_url, expires_at = assemble_and_upload(
        customer_id=parsed.customer_id,
        run_id=run_id,
        slides=parsed.slides,
    )

    aggregate_gaps: list[str] = sorted(
        {gap for s in parsed.slides for gap in s.data_gaps}
    )

    from asrp_functions.shared.config import get_settings

    response = AssembleDeckResponse(
        customer_id=parsed.customer_id,
        run_id=run_id,
        delivery_mode=get_settings().DECK_DELIVERY_MODE,
        blob_url=blob_url,
        deck_url=deck_url,
        expires_at=expires_at,
        aggregate_data_gaps=aggregate_gaps,
    )
    logger.info(
        "assemble_deck.ok",
        extra={
            "customer_id": parsed.customer_id,
            "run_id": run_id,
            "delivery_mode": response.delivery_mode,
            "aggregate_gap_count": len(aggregate_gaps),
        },
    )
    return _json_response(response)


# ------------------------------------------------------------------- notify


@app.function_name(name="notify_user")
@app.route(route="notify", methods=["POST"])
def notify_user(req: func.HttpRequest) -> func.HttpResponse:
    return _safe_handler("notify_user", _notify_user_impl)(req)


def _notify_user_impl(req: func.HttpRequest) -> func.HttpResponse:
    parsed = _parse_body(req, NotifyUserRequest)
    if isinstance(parsed, func.HttpResponse):
        return parsed

    run_id = parsed.run_metadata.run_id
    logger.info(
        "notify_user.start",
        extra={"run_id": run_id, "customer_id": parsed.summary.customer_id},
    )

    payload = parsed.model_dump(mode="json")
    status_code = dispatch_notification(payload)

    return _json_response(
        NotifyUserResponse(
            run_id=run_id, dispatched=True, flow_status_code=status_code
        )
    )
