"""Contract test for the ``BX_AzureOpenAI`` boundary-crossing event.

The Phase 1 TDD pins two invariants:

1. ``AIFoundryClient.generate_slide`` emits **exactly one**
   ``BX_AzureOpenAI`` log event per call, carrying at minimum the
   audit fields ``run_id``, ``customer_id``, ``slide_id``,
   ``prompt_version`` and ``schema_version``.
2. **No other component** in the codebase emits this event. This
   protects the rule that Azure OpenAI is the only run-time egress
   from the HSBC application boundary.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from shared.ai_foundry_client import AIFoundryClient

_REQUIRED_FIELDS = {
    "run_id",
    "customer_id",
    "slide_id",
    "prompt_version",
    "schema_version",
}


def _fake_completion() -> SimpleNamespace:
    """Build the minimum AzureOpenAI-shaped completion object."""
    return SimpleNamespace(
        model="gpt-4o-mini",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content='{"hello": "world"}'),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=20, total_tokens=30
        ),
    )


def _build_client() -> AIFoundryClient:
    prompts = MagicMock()
    prompts.get.return_value = "system prompt"
    aoai = MagicMock()
    aoai.chat.completions.create.return_value = _fake_completion()
    return AIFoundryClient(prompt_registry=prompts, client=aoai)


@pytest.fixture
def bx_caplog(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Capture records from the AIFoundryClient logger.

    The shared JSON logger sets ``propagate=False`` so caplog (which
    listens on the root logger) misses records by default. Attach
    caplog's handler directly to the named logger for the duration of
    the test.
    """
    target = logging.getLogger("shared.ai_foundry_client")
    target.addHandler(caplog.handler)
    target.setLevel(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="shared.ai_foundry_client")
    try:
        yield caplog
    finally:
        target.removeHandler(caplog.handler)


def test_generate_slide_emits_exactly_one_bx_event(
    bx_caplog: pytest.LogCaptureFixture,
) -> None:
    client = _build_client()

    client.generate_slide(
        slide_id="footprint_v1",
        customer_id="cust-001",
        prompt_version="v1",
        schema_version="v1",
        run_id="run-001",
    )

    bx_records = [r for r in bx_caplog.records if r.message == "BX_AzureOpenAI"]
    assert len(bx_records) == 1, (
        f"expected exactly one BX_AzureOpenAI record, got {len(bx_records)}"
    )

    record = bx_records[0]
    missing = {f for f in _REQUIRED_FIELDS if not hasattr(record, f)}
    assert not missing, f"BX_AzureOpenAI is missing required fields: {missing}"

    assert record.run_id == "run-001"
    assert record.customer_id == "cust-001"
    assert record.slide_id == "footprint_v1"
    assert record.prompt_version == "v1"
    assert record.schema_version == "v1"


def test_generate_slide_strict_also_emits_exactly_one_bx_event(
    bx_caplog: pytest.LogCaptureFixture,
) -> None:
    client = _build_client()

    client.generate_slide_strict(
        slide_id="footprint_v1",
        customer_id="cust-001",
        prompt_version="v1",
        schema_version="v1",
        run_id="run-001",
    )

    bx_records = [r for r in bx_caplog.records if r.message == "BX_AzureOpenAI"]
    assert len(bx_records) == 1
    assert bx_records[0].strict is True


# ------------------------------------------------------------------ codebase
# Static check: no other component is allowed to emit ``BX_AzureOpenAI``.

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BX_LITERAL = "BX_AzureOpenAI"
_ALLOWED_EMITTER = (
    _REPO_ROOT / "asrp_functions" / "shared" / "ai_foundry_client.py"
).resolve()
_TESTS_DIR = (_REPO_ROOT / "tests").resolve()

_SCAN_DIRS = (
    _REPO_ROOT / "asrp_functions",
    _REPO_ROOT / "tools",
)
# Match anything that looks like a logging emission of the literal:
#   logger.info("BX_AzureOpenAI", ...)
#   log.warning('BX_AzureOpenAI')
#   logging.getLogger(...).info("BX_AzureOpenAI")
_EMIT_PATTERN = re.compile(
    r"""\.(?:info|warning|error|critical|debug|exception|log)\s*\(\s*['"]BX_AzureOpenAI['"]"""
)


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCAN_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            files.append(path.resolve())
    return files


def test_only_ai_foundry_client_emits_bx_event() -> None:
    offenders: list[str] = []
    for path in _iter_python_files():
        # Allow the contract test itself (under /tests) and the
        # canonical emitter to mention the literal.
        if path == _ALLOWED_EMITTER:
            continue
        if _TESTS_DIR in path.parents:
            continue

        text = path.read_text(encoding="utf-8")
        if _EMIT_PATTERN.search(text):
            offenders.append(str(path.relative_to(_REPO_ROOT)))

    assert not offenders, (
        "BX_AzureOpenAI must only be emitted by "
        "asrp_functions/shared/ai_foundry_client.py, but found emissions in: "
        f"{offenders}"
    )
