"""Azure OpenAI / AI Foundry slide generation."""

from __future__ import annotations

import json
from typing import Any

from openai import AzureOpenAI

from shared.auth import get_aoai_token_provider
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger(__name__)

_SYSTEM_BASELINE = (
    "You generate slide content for the HSBC Customer Service Review pack. "
    "You must use ONLY the grounded customer context provided. Never "
    "fabricate data. When a value cannot be grounded, set the field to "
    "null and add the field name to the top-level `data_gaps` array. "
    "Respond with a single JSON object only."
)


def _client() -> AzureOpenAI:
    settings = get_settings()
    return AzureOpenAI(
        azure_endpoint=str(settings.AI_FOUNDRY_ENDPOINT),
        api_version=settings.AZURE_OPENAI_API_VERSION,
        azure_ad_token_provider=get_aoai_token_provider(),
    )


def generate_slide_json(
    *,
    slide_prompt: str,
    grounding_hits: list[dict[str, Any]],
    customer_id: str,
    slide_id: str,
) -> dict[str, Any]:
    """Call Azure OpenAI in JSON-mode and return the parsed slide content.

    The returned dict is the model's raw JSON object. Callers are
    responsible for validating it against the per-slide schema and
    extracting ``data_gaps``.
    """
    settings = get_settings()
    grounding_text = json.dumps(grounding_hits, default=str)
    user_message = (
        f"Customer id: {customer_id}\n"
        f"Slide id: {slide_id}\n\n"
        f"Slide instructions:\n{slide_prompt}\n\n"
        f"Grounded context (JSON array of search hits):\n{grounding_text}"
    )

    logger.info(
        "aoai.invoke",
        extra={
            "customer_id": customer_id,
            "slide_id": slide_id,
            "grounding_hit_count": len(grounding_hits),
            "deployment": settings.AZURE_OPENAI_DEPLOYMENT_NAME,
        },
    )

    with _client() as client:
        completion = client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT_NAME,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM_BASELINE},
                {"role": "user", "content": user_message},
            ],
        )

    content = completion.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error(
            "aoai.invalid_json",
            extra={"customer_id": customer_id, "slide_id": slide_id},
        )
        raise ValueError("Model returned non-JSON content") from exc
