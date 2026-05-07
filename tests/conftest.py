"""Pytest configuration for ASRP unit tests.

Sets up:
- Required environment variables so :class:`shared.config.Settings`
  validates without reaching out to Azure.
- ``sys.path`` entry so ``shared`` / ``functions`` packages resolve when
  the test runner is invoked from the repo root.
- Auto-patches ``time.sleep`` in retry-aware modules so tests are fast.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the Function App importable from the repo root.
_FUNC_APP_ROOT = Path(__file__).resolve().parent.parent / "asrp_functions"
if str(_FUNC_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_FUNC_APP_ROOT))


_REQUIRED_ENV: dict[str, str] = {
    "AI_FOUNDRY_ENDPOINT": "https://example-foundry.test/",
    "AI_FOUNDRY_PROJECT": "asrp-test",
    "AZURE_OPENAI_DEPLOYMENT_NAME": "gpt-4o-mini",
    "AZURE_OPENAI_API_VERSION": "2024-10-21",
    "AI_SEARCH_ENDPOINT": "https://example-search.test/",
    "AI_SEARCH_INDEX_NAME": "asrp-test-index",
    "BLOB_ACCOUNT_URL": "https://example.blob.core.windows.net/",
    "BLOB_TEMPLATE_CONTAINER": "templates",
    "BLOB_TEMPLATE_NAME": "hsbc_csr_template_brand_reviewed.pptx",
    "BLOB_ASSETS_CONTAINER": "assets",
    "BLOB_DECK_CONTAINER": "decks",
    "DECK_DELIVERY_MODE": "sas",
    "POWER_AUTOMATE_FLOW_URL": "https://example.test/flow?sig=test",
    "DEFAULT_PROMPT_VERSION": "v1",
    "DEFAULT_SCHEMA_VERSION": "v1",
    "LOG_LEVEL": "WARNING",
}


@pytest.fixture(scope="session", autouse=True)
def _set_required_env() -> None:
    for key, value in _REQUIRED_ENV.items():
        os.environ.setdefault(key, value)
    # Reset cached settings so tests pick up the test env.
    from shared import config as _cfg

    _cfg.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch time.sleep wherever the codebase explicitly imports it."""
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)
    try:
        from asrp_functions.functions import generate_slide_description as _gsd

        monkeypatch.setattr(_gsd.time, "sleep", lambda *_a, **_k: None)
    except ImportError:  # pragma: no cover - module always present
        pass
    try:
        from asrp_functions.functions import notify_user as _nu

        monkeypatch.setattr(_nu.time, "sleep", lambda *_a, **_k: None)
    except ImportError:  # pragma: no cover
        pass
