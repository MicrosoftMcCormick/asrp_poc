"""Models for the ``assemble_deck`` endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from shared.models.common import RunMetadata
from shared.models.slide import SlideDescription


class AssembleDeckRequest(BaseModel):
    """Request to assemble the final PPTX from per-slide descriptions."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    slides: list[SlideDescription] = Field(
        ..., min_length=1, description="Aggregated per-slide JSON descriptions."
    )
    run_metadata: RunMetadata


class AssembleDeckResponse(BaseModel):
    """Response from ``assemble_deck``."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    run_id: str
    delivery_mode: Literal["sas", "dataverse"]
    blob_url: HttpUrl = Field(
        ..., description="Blob URL of the generated deck in the output container."
    )
    deck_url: HttpUrl = Field(
        ...,
        description="Caller-usable deck URL: SAS-signed blob URL, or "
        "Dataverse record URL when delivery_mode=='dataverse'.",
    )
    expires_at: datetime | None = Field(
        default=None, description="Expiry of the SAS URL when applicable."
    )
    aggregate_data_gaps: list[str] = Field(
        default_factory=list,
        description="Union of data_gaps reported across all slides.",
    )
