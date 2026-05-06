# Implement environment-driven configuration for the HSBC ASRP Phase 1 POC
# using pydantic-settings.
#
# Settings:
#   - AI_FOUNDRY_ENDPOINT          (str, required)
#   - AI_FOUNDRY_PROJECT           (str, required)
#   - AZURE_OPENAI_DEPLOYMENT_NAME (str, required)
#   - AI_SEARCH_INDEX_NAME         (str, required)
#   - BLOB_ACCOUNT_URL             (str, required)
#   - BLOB_TEMPLATE_CONTAINER      (str, default "templates")
#   - BLOB_TEMPLATE_NAME           (str, default "hsbc_csr_template_brand_reviewed.pptx")
#   - BLOB_DECK_CONTAINER          (str, default "decks")
#   - POWER_AUTOMATE_FLOW_URL      (str, required) — pulled from Key Vault reference
#   - DEFAULT_PROMPT_VERSION       (str, default "v1")
#   - DEFAULT_SCHEMA_VERSION       (str, default "v1")
#
# No secrets in code. No defaults that point to non-HSBC tenants.
#
# Operationally adjacent settings (no tenant identity, all generic):
# AI_SEARCH_ENDPOINT, AZURE_OPENAI_API_VERSION, BLOB_ASSETS_CONTAINER,
# DECK_DELIVERY_MODE and LOG_LEVEL are also surfaced here because they
# are consumed by the runtime; none of them carry tenant-identifying
# defaults.

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings.

    All values are sourced from environment variables (Function App
    Application Settings in Azure, ``local.settings.json`` locally).
    Secrets, keys and connection strings must never be hard-coded;
    runtime auth uses Managed Identity via :mod:`shared.auth`.
    """

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=True,
        extra="ignore",
    )

    # --- Azure AI Foundry / Azure OpenAI ---
    AI_FOUNDRY_ENDPOINT: HttpUrl = Field(
        ..., description="Azure AI Foundry / Azure OpenAI endpoint URL."
    )
    AI_FOUNDRY_PROJECT: str = Field(
        ..., min_length=1, description="Azure AI Foundry project name."
    )
    AZURE_OPENAI_DEPLOYMENT_NAME: str = Field(
        ...,
        min_length=1,
        description="Deployed Azure OpenAI chat completion model name.",
    )
    AZURE_OPENAI_API_VERSION: str = Field(default="2024-10-21")

    # --- Azure AI Search ---
    AI_SEARCH_ENDPOINT: HttpUrl = Field(
        ..., description="Azure AI Search service endpoint."
    )
    AI_SEARCH_INDEX_NAME: str = Field(
        ...,
        min_length=1,
        description="Index containing grounded Dataverse content.",
    )

    # --- Azure Blob Storage ---
    BLOB_ACCOUNT_URL: HttpUrl = Field(
        ...,
        description=(
            "Storage account blob endpoint, e.g. "
            "https://<acct>.blob.core.windows.net."
        ),
    )
    BLOB_TEMPLATE_CONTAINER: str = Field(default="templates")
    BLOB_TEMPLATE_NAME: str = Field(
        default="hsbc_csr_template_brand_reviewed.pptx"
    )
    BLOB_ASSETS_CONTAINER: str = Field(
        default="assets",
        description="Container for icons, pictograms and other deck assets.",
    )
    BLOB_DECK_CONTAINER: str = Field(default="decks")

    # --- Deck delivery ---
    DECK_DELIVERY_MODE: Literal["sas", "dataverse"] = Field(
        default="sas",
        description="How the assembled deck is delivered back to the orchestrator.",
    )

    # --- Power Automate ---
    POWER_AUTOMATE_FLOW_URL: HttpUrl = Field(
        ...,
        description=(
            "HTTP-trigger URL for the Power Automate notification flow. "
            "Resolved from a Key Vault reference; never hard-coded."
        ),
    )

    # --- Versioning defaults ---
    DEFAULT_PROMPT_VERSION: str = Field(default="v1")
    DEFAULT_SCHEMA_VERSION: str = Field(default="v1")

    # --- Logging ---
    LOG_LEVEL: str = Field(default="INFO")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()  # type: ignore[call-arg]


__all__ = ["Settings", "get_settings"]
