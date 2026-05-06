# Implement an `AIFoundryClient` for the HSBC ASRP Phase 1 POC.
# Constraints (from Main TDD §7 and Technical Appendix A3-A4):
# - Uses Azure AI Foundry to run a RAG flow that retrieves grounded context from Azure AI Search and calls Azure OpenAI.
# - Auth: DefaultAzureCredential (Managed Identity in Azure, developer credential locally).
# - Endpoint and project name come from environment via shared.config.
# - Single public method: `generate_slide(slide_id: str, customer_id: str, prompt_version: str, schema_version: str) -> dict`
#   which returns the parsed JSON slide description.
# - The static per-slide prompt is loaded by `PromptRegistry` (separate component) — the client does NOT compose prompts itself.
# - Send only fields relevant to the requested slide (data minimisation). The retrieval filter is keyed on `customer_id` and `slide_id`.
# - Treat Azure OpenAI as the ONLY run-time egress — log boundary-crossing events via shared.logging with event name "BX_AzureOpenAI".
# - Forbid any other LLM providers.
# - Return raw model output plus a `model_metadata` dict (model name, deployment, prompt_version, schema_version, generation timestamp).
# - Do not catch exceptions here; let the orchestration layer decide retry behaviour.

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol

from openai import AzureOpenAI

from shared.auth import get_aoai_token_provider
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger(__name__)


class PromptRegistryProtocol(Protocol):
    """Structural type for the external PromptRegistry component."""

    def get(self, slide_id: str, prompt_version: str) -> str: ...


class AIFoundryClient:
    """Thin wrapper around Azure AI Foundry's RAG-on-AI-Search flow.

    The orchestrator owns retry, timeout escalation and circuit breaking.
    This client deliberately does **not** catch exceptions: callers see
    the raw SDK error and decide policy.

    Azure OpenAI is the only run-time egress permitted from the HSBC
    application boundary. Introducing additional LLM providers in this
    module is forbidden.
    """

    _BX_EVENT = "BX_AzureOpenAI"

    def __init__(
        self,
        prompt_registry: PromptRegistryProtocol,
        *,
        client: AzureOpenAI | None = None,
    ) -> None:
        self._prompts = prompt_registry
        self._settings = get_settings()
        self._client = client or AzureOpenAI(
            azure_endpoint=str(self._settings.AI_FOUNDRY_ENDPOINT),
            api_version=self._settings.AZURE_OPENAI_API_VERSION,
            azure_ad_token_provider=get_aoai_token_provider(),
        )

    # ------------------------------------------------------------------ public

    def generate_slide(
        self,
        slide_id: str,
        customer_id: str,
        prompt_version: str,
        schema_version: str,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        """Run the RAG flow for one slide and return the parsed JSON.

        Returns a dict with two keys:

        * ``description`` — the parsed JSON object emitted by the model.
        * ``model_metadata`` — model name, deployment, prompt_version,
          schema_version, generation timestamp (UTC ISO-8601).
        """
        return self._invoke(
            run_id=run_id,
            slide_id=slide_id,
            customer_id=customer_id,
            prompt_version=prompt_version,
            schema_version=schema_version,
            extra_system=None,
        )

    def generate_slide_strict(
        self,
        slide_id: str,
        customer_id: str,
        prompt_version: str,
        schema_version: str,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        """Retry a slide generation with an additional schema reminder.

        Used by orchestrators after a per-slide schema validation failure.
        Behaviour is identical to :meth:`generate_slide` but injects a
        stricter system message that reiterates the schema contract.
        """
        reminder = (
            "STRICT MODE: Your previous response failed schema validation. "
            f"Re-read the slide schema for slide_id={slide_id!r} "
            f"(schema_version={schema_version!r}) and emit a single JSON "
            "object that conforms to it exactly. Unknown fields are "
            "forbidden. For any value you cannot ground in the retrieved "
            "context, return null and add the field name to the top-level "
            "`data_gaps` array. Do not fabricate any values."
        )
        return self._invoke(
            run_id=run_id,
            slide_id=slide_id,
            customer_id=customer_id,
            prompt_version=prompt_version,
            schema_version=schema_version,
            extra_system=reminder,
        )

    # ---------------------------------------------------------------- internal

    def _invoke(
        self,
        *,
        run_id: str,
        slide_id: str,
        customer_id: str,
        prompt_version: str,
        schema_version: str,
        extra_system: str | None,
    ) -> dict[str, Any]:
        prompt = self._prompts.get(slide_id, prompt_version)
        data_source = self._build_data_source(
            customer_id=customer_id, slide_id=slide_id
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": prompt}]
        if extra_system:
            messages.append({"role": "system", "content": extra_system})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"slide_id={slide_id}\n"
                    f"customer_id={customer_id}\n"
                    "Return a single JSON object that conforms to the "
                    "slide schema. For any field that cannot be grounded "
                    "in the retrieved context, return null and add the "
                    "field name to the top-level `data_gaps` array. "
                    "Do not fabricate values."
                ),
            }
        )

        completion = self._client.chat.completions.create(
            model=self._settings.AZURE_OPENAI_DEPLOYMENT_NAME,
            response_format={"type": "json_object"},
            temperature=0,
            messages=messages,
            extra_body={"data_sources": [data_source]},
        )

        generated_at = datetime.now(timezone.utc).isoformat()
        choice = completion.choices[0]
        content = choice.message.content or "{}"

        usage = getattr(completion, "usage", None)
        # Single boundary-crossing event per call. Azure OpenAI is the
        # only run-time egress permitted from the application boundary,
        # so emitting more than one event per call (or emitting this
        # event from any other component) would break the audit
        # invariant tested in tests/test_bx_event_contract.py.
        logger.info(
            self._BX_EVENT,
            extra={
                "run_id": run_id,
                "slide_id": slide_id,
                "customer_id": customer_id,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "deployment": self._settings.AZURE_OPENAI_DEPLOYMENT_NAME,
                "endpoint": str(self._settings.AI_FOUNDRY_ENDPOINT),
                "project": self._settings.AI_FOUNDRY_PROJECT,
                "model": completion.model,
                "strict": extra_system is not None,
                "finish_reason": choice.finish_reason,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
                "generated_at": generated_at,
            },
        )

        try:
            description = json.loads(content)
        except json.JSONDecodeError as exc:
            # Re-raise as ValueError so the orchestration layer (not this
            # client) decides retry semantics, while preserving cause.
            raise ValueError(
                f"AI Foundry returned non-JSON content for slide_id={slide_id!r}"
            ) from exc

        if not isinstance(description, dict):
            raise ValueError(
                f"AI Foundry returned non-object JSON for slide_id={slide_id!r}"
            )

        model_metadata: dict[str, Any] = {
            "model": completion.model,
            "deployment": self._settings.AZURE_OPENAI_DEPLOYMENT_NAME,
            "project": self._settings.AI_FOUNDRY_PROJECT,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "generated_at": generated_at,
        }

        return {"description": description, "model_metadata": model_metadata}

    # ----------------------------------------------------------------- private

    def _build_data_source(
        self, *, customer_id: str, slide_id: str
    ) -> dict[str, Any]:
        """Return the AI Search ``data_source`` block for "On Your Data".

        The retrieval filter is scoped to the requested customer and
        slide so the model never sees fields outside the slide's
        responsibility (data minimisation).
        """
        return {
            "type": "azure_search",
            "parameters": {
                "endpoint": str(self._settings.AI_SEARCH_ENDPOINT),
                "index_name": self._settings.AI_SEARCH_INDEX_NAME,
                "authentication": {"type": "system_assigned_managed_identity"},
                "filter": (
                    f"customer_id eq '{customer_id}' and "
                    f"slide_id eq '{slide_id}'"
                ),
                "in_scope": True,
                "strictness": 5,
                "top_n_documents": 8,
            },
        }


__all__ = ["AIFoundryClient", "PromptRegistryProtocol"]
