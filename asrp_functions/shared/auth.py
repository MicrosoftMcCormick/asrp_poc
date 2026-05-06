"""Managed-identity credential helpers.

All Azure SDK clients in this app authenticate via
:class:`azure.identity.DefaultAzureCredential`. Secrets, keys and
connection strings must never be used at runtime.
"""

from __future__ import annotations

from functools import lru_cache

from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential

# Cognitive Services / Azure OpenAI scope for AAD token exchange.
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


@lru_cache(maxsize=1)
def get_credential() -> TokenCredential:
    """Return a process-wide :class:`DefaultAzureCredential`.

    Locally this resolves to the developer's signed-in identity (VS Code,
    Azure CLI, etc.). In Azure it resolves to the Function App's managed
    identity.
    """
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def get_aoai_token_provider():
    """Return an Azure-AD token provider for the Azure OpenAI client."""
    from azure.identity import get_bearer_token_provider

    return get_bearer_token_provider(get_credential(), COGNITIVE_SERVICES_SCOPE)
