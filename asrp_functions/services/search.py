"""Azure AI Search retrieval for RAG grounding."""

from __future__ import annotations

from typing import Any

from azure.search.documents import SearchClient

from shared.auth import get_credential
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger(__name__)


def _client() -> SearchClient:
    settings = get_settings()
    return SearchClient(
        endpoint=str(settings.AI_SEARCH_ENDPOINT),
        index_name=settings.AI_SEARCH_INDEX_NAME,
        credential=get_credential(),
    )


def retrieve_grounding(
    *,
    customer_id: str,
    slide_id: str,
    top: int = 8,
) -> list[dict[str, Any]]:
    """Retrieve grounded context for a slide from Azure AI Search.

    Returns a list of search hits, each a dict with at minimum ``id``,
    ``score`` and ``content`` keys (additional fields are passed through).
    Filtering is scoped to the customer; the ``slide_id`` is used as the
    query text so the index's analyzer / semantic ranker matches it.
    """
    filter_expr = f"customer_id eq '{customer_id}'"
    logger.info(
        "ai_search.query",
        extra={"customer_id": customer_id, "slide_id": slide_id, "top": top},
    )
    with _client() as client:
        results = client.search(
            search_text=slide_id,
            filter=filter_expr,
            top=top,
            include_total_count=False,
        )
        hits: list[dict[str, Any]] = []
        for r in results:
            hit = {k: v for k, v in r.items() if not k.startswith("@")}
            hit["score"] = r.get("@search.score")
            hits.append(hit)
        logger.info(
            "ai_search.results",
            extra={"customer_id": customer_id, "slide_id": slide_id, "hit_count": len(hits)},
        )
        return hits
