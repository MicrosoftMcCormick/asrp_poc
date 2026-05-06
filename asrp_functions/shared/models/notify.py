"""Models for the ``notify_user`` endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl

from shared.models.common import RunMetadata


class RunSummary(BaseModel):
    """Summary of a completed ASRP run."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    customer_name: str | None = None
    slide_count: int = Field(..., ge=0)
    aggregate_data_gaps: list[str] = Field(default_factory=list)


class NotifyUserRequest(BaseModel):
    """Request to dispatch a Power Automate notification."""

    model_config = ConfigDict(extra="forbid")

    run_metadata: RunMetadata
    summary: RunSummary
    deck_url: HttpUrl
    recipient: EmailStr | None = Field(
        default=None,
        description="Optional override; flow may resolve recipient itself.",
    )


class NotifyUserResponse(BaseModel):
    """Response from ``notify_user``."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    dispatched: bool
    flow_status_code: int
