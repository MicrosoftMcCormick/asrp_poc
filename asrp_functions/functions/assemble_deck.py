# Implement the `assemble_deck` HTTP function for the HSBC ASRP Phase 1 POC.
#
# Steps:
#  1. Parse AssembleDeckRequest.
#  2. Validate every slide via slide_schemas.validate; collect invalid
#     slide_ids and exclude them from assembly.
#  3. Load the HSBC CSR template via TemplateStore.get_template_bytes().
#  4. Call DeckAssembler.assemble(template_bytes, valid_slides) ->
#     (deck_bytes, failed_slide_ids).
#  5. Persist deck_bytes via TemplateStore.write_deck_bytes(customer_id,
#     run_id, deck_bytes); receive deck_blob_url.
#  6. Compose AssembleDeckResponse:
#       status = "ok"      if no failures
#       status = "partial" if failed_slide_ids non-empty (still return the deck)
#       status = "failed"  only if assembly itself failed (no deck produced)
#  7. Return JSON response.
#
# Do not embed customer data anywhere outside the deck (no logs, no
# error messages).

from __future__ import annotations

import json
from typing import Any

import azure.functions as func
from pydantic import ValidationError

from shared.deck_assembler import DeckAssembler
from shared.logging import get_logger
from shared.models.contracts import AssembleDeckRequest, AssembleDeckResponse
from shared.slide_schemas import validate as validate_slide
from shared.template_store import TemplateStore, TemplateStoreError

logger = get_logger(__name__)

# Process-wide singletons.
_TEMPLATE_STORE: TemplateStore | None = None
_DECK_ASSEMBLER: DeckAssembler | None = None


def _template_store() -> TemplateStore:
    global _TEMPLATE_STORE
    if _TEMPLATE_STORE is None:
        _TEMPLATE_STORE = TemplateStore()
    return _TEMPLATE_STORE


def _deck_assembler() -> DeckAssembler:
    global _DECK_ASSEMBLER
    if _DECK_ASSEMBLER is None:
        _DECK_ASSEMBLER = DeckAssembler(template_store=_template_store())
    return _DECK_ASSEMBLER


def _slide_id_of(slide: dict[str, Any], fallback_index: int) -> str:
    """Return the slide_id for logging, never customer data."""
    sid = slide.get("slide_id") if isinstance(slide, dict) else None
    return str(sid) if sid else f"<index:{fallback_index}>"


# --------------------------------------------------------------------- entry


def main(req: func.HttpRequest) -> func.HttpResponse:
    """Azure Functions HTTP entrypoint."""
    try:
        body = req.get_json()
    except ValueError as exc:
        logger.warning("assemble_deck.invalid_json", extra={"detail": str(exc)})
        return _error_response("invalid_json", str(exc), 400)

    try:
        request = AssembleDeckRequest.model_validate(body)
    except ValidationError as exc:
        logger.warning(
            "assemble_deck.validation_error",
            extra={"errors": exc.errors(include_url=False, include_input=False)},
        )
        return _error_response(
            "validation_error",
            exc.errors(include_url=False, include_input=False),
            422,
        )

    response = _handle(request)
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


# --------------------------------------------------------------------- core


def _handle(request: AssembleDeckRequest) -> AssembleDeckResponse:
    log_ctx = {
        "run_id": request.run_id,
        "customer_id": request.customer_id,
        "slide_count": len(request.slides),
    }
    logger.info("assemble_deck.start", extra=log_ctx)

    # Step 2 — validate every slide; exclude invalid ones from assembly.
    valid_slides: list[dict[str, Any]] = []
    invalid_slide_ids: list[str] = []
    for idx, slide in enumerate(request.slides):
        slide_id = _slide_id_of(slide, idx)
        if not isinstance(slide, dict) or not slide.get("slide_id"):
            invalid_slide_ids.append(slide_id)
            logger.warning(
                "assemble_deck.slide_unidentified", extra={"slide_index": idx}
            )
            continue
        result = validate_slide(slide["slide_id"], slide)
        if result.is_valid and result.model is not None:
            # Pass the model-validated representation downstream so the
            # assembler sees a canonical shape.
            valid_slides.append(
                result.model.model_dump(by_alias=True, mode="json")
            )
        else:
            invalid_slide_ids.append(slide_id)
            logger.warning(
                "assemble_deck.slide_schema_invalid",
                extra={
                    "slide_id": slide_id,
                    "validation_error_count": len(result.errors),
                },
            )

    if not valid_slides:
        logger.error("assemble_deck.no_valid_slides", extra=log_ctx)
        return AssembleDeckResponse(
            deck_blob_url="",
            status="failed",
            failed_slide_ids=invalid_slide_ids,
        )

    # Step 3 — load template.
    try:
        template_bytes = _template_store().get_template_bytes()
    except TemplateStoreError as exc:
        logger.error(
            "assemble_deck.template_load_failed",
            extra={**log_ctx, "error_type": type(exc).__name__},
        )
        return AssembleDeckResponse(
            deck_blob_url="",
            status="failed",
            failed_slide_ids=invalid_slide_ids
            + [str(s.get("slide_id") or "") for s in valid_slides],
        )

    # Step 4 — assemble. A catastrophic failure here means no deck.
    try:
        deck_bytes, assembler_failed_ids = _deck_assembler().assemble(
            template_bytes, valid_slides
        )
    except Exception as exc:  # noqa: BLE001 - boundary handler
        logger.error(
            "assemble_deck.assembly_failed",
            extra={**log_ctx, "error_type": type(exc).__name__},
        )
        return AssembleDeckResponse(
            deck_blob_url="",
            status="failed",
            failed_slide_ids=invalid_slide_ids
            + [str(s.get("slide_id") or "") for s in valid_slides],
        )

    # Step 5 — persist.
    try:
        deck_blob_url = _template_store().write_deck_bytes(
            request.customer_id, request.run_id, deck_bytes
        )
    except TemplateStoreError as exc:
        logger.error(
            "assemble_deck.deck_write_failed",
            extra={**log_ctx, "error_type": type(exc).__name__},
        )
        return AssembleDeckResponse(
            deck_blob_url="",
            status="failed",
            failed_slide_ids=invalid_slide_ids
            + assembler_failed_ids
            + [str(s.get("slide_id") or "") for s in valid_slides],
        )

    # Step 6 — compose response.
    failed_slide_ids = sorted(
        {*invalid_slide_ids, *assembler_failed_ids}
    )
    status = "ok" if not failed_slide_ids else "partial"

    logger.info(
        "assemble_deck.ok",
        extra={
            **log_ctx,
            "status": status,
            "failed_slide_count": len(failed_slide_ids),
            "deck_size_bytes": len(deck_bytes),
        },
    )

    return AssembleDeckResponse(
        deck_blob_url=deck_blob_url,
        status=status,
        failed_slide_ids=failed_slide_ids,
    )


__all__ = ["main"]
