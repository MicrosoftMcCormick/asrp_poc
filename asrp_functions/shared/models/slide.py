"""Models for the ``generate_slide_description`` endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shared.models.common import RunMetadata


class GenerateSlideRequest(BaseModel):
    """Request to generate a single slide's JSON description."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(..., description="Dataverse customer (account) id.")
    slide_id: str = Field(
        ...,
        description="Identifier of the slide in the HSBC CSR template "
        "(e.g. 'exec_summary', 'volumes_trend').",
    )
    run_metadata: RunMetadata


class SlideDescription(BaseModel):
    """JSON slide description returned by the model.

    The exact schema for ``content`` is per-slide and validated downstream
    against the slide-specific schema. ``data_gaps`` lists the names of
    fields that could not be grounded; per the house rules, missing
    grounded data must be returned as ``null`` rather than fabricated.
    """

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    slide_id: str
    content: dict[str, Any] = Field(
        ..., description="Slide-specific JSON payload validated by per-slide schema."
    )
    data_gaps: list[str] = Field(
        default_factory=list,
        description="Names of fields that were null due to missing grounded data.",
    )
    citations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Search hits used to ground the answer (id, score, source).",
    )


class GenerateSlideResponse(BaseModel):
    """Wrapper response for ``generate_slide_description``."""

    model_config = ConfigDict(extra="forbid")

    slide: SlideDescription
    run_id: str
