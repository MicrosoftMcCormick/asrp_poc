# Generate pydantic v2 models for the HSBC ASRP Phase 1 POC Function App contracts.
# Source of truth: Main TDD and Technical Appendix.
#
# Models required:
# - GenerateSlideRequest: customer_id (str), slide_id (str), run_id (str), user_id (str), prompt_version (str), schema_version (str)
# - GenerateSlideResponse: slide_id, status ("ok" | "partial" | "failed"), description (dict), data_gaps (list[str]), model_metadata (dict)
# - AssembleDeckRequest: customer_id, run_id, slides (list[dict]) where each dict is a validated slide description
# - AssembleDeckResponse: deck_blob_url (str), status, failed_slide_ids (list[str])
# - NotifyUserRequest: run_id, user_id, customer_id, deck_blob_url, status, summary (str)
# - NotifyUserResponse: status, notified_at (datetime)
#
# All datetimes UTC ISO-8601. All IDs are opaque strings. No PII validation here — that lives in Dataverse.

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SlideStatus = Literal["ok", "partial", "failed"]
DeckStatus = Literal["ok", "partial", "failed"]
NotifyStatus = Literal["ok", "failed"]


class _Base(BaseModel):
    """Shared config: forbid unknown fields, strip whitespace from strings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GenerateSlideRequest(_Base):
    """Request to generate a single slide's JSON description."""

    customer_id: str = Field(..., min_length=1)
    slide_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    prompt_version: str = Field(..., min_length=1)
    schema_version: str = Field(..., min_length=1)


class GenerateSlideResponse(_Base):
    """Validated slide description plus grounding/model telemetry."""

    slide_id: str = Field(..., min_length=1)
    status: SlideStatus
    description: dict[str, Any] = Field(
        default_factory=dict,
        description="Slide JSON validated against the per-slide schema.",
    )
    data_gaps: list[str] = Field(
        default_factory=list,
        description="Names of fields returned as null due to missing grounded data.",
    )
    model_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Model deployment, prompt/schema versions, token usage, latency, etc.",
    )


class AssembleDeckRequest(_Base):
    """Aggregate slide descriptions for deck assembly."""

    customer_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    slides: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description="Ordered list of validated per-slide JSON descriptions.",
    )


class AssembleDeckResponse(_Base):
    """Result of PPTX assembly and upload to Blob Storage."""

    deck_blob_url: str = Field(..., min_length=1)
    status: DeckStatus
    failed_slide_ids: list[str] = Field(default_factory=list)


class NotifyUserRequest(_Base):
    """Payload sent to Power Automate to notify the requesting user."""

    run_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    customer_id: str = Field(..., min_length=1)
    deck_blob_url: str = Field(..., min_length=1)
    status: DeckStatus
    summary: str


class NotifyUserResponse(_Base):
    """Acknowledgement that the notification was dispatched."""

    status: NotifyStatus
    notified_at: datetime = Field(
        ..., description="UTC ISO-8601 timestamp the notification was dispatched."
    )


__all__ = [
    "GenerateSlideRequest",
    "GenerateSlideResponse",
    "AssembleDeckRequest",
    "AssembleDeckResponse",
    "NotifyUserRequest",
    "NotifyUserResponse",
    "SlideStatus",
    "DeckStatus",
    "NotifyStatus",
]
