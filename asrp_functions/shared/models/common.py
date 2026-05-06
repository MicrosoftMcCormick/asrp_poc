"""Common models shared across endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunMetadata(BaseModel):
    """Metadata propagated by the Copilot Studio orchestrator for a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="Unique run identifier from the orchestrator.")
    initiated_by: str | None = Field(
        default=None, description="UPN or object id of the human initiator."
    )
    initiated_at: datetime | None = Field(
        default=None, description="UTC timestamp the run was initiated."
    )
    correlation_id: str | None = Field(
        default=None, description="Optional correlation id for cross-system tracing."
    )


class ErrorResponse(BaseModel):
    """Standard error envelope for HTTP function failures."""

    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str | None = None
    context: dict[str, Any] | None = None
