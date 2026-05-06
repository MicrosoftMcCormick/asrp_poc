# Define pydantic models for the per-slide JSON output schemas described here:
#
# JSON Output Schema (Technical Appendix A4)
#
# Common envelope for ALL slides:
#   slide_id: str
#   title: str
#   subtitle: str | None
#   commentary: list[str]            # editable narrative bullets
#   data_gaps: list[str]             # fields missing from grounded data
#   model_metadata: dict             # model name, prompt_version, schema_version, timestamp
#
# Slide-specific examples implemented now:
#   footprint_v1
#   payments_stp_v1
#   query_analysis_v1
#   product_updates_v1
#
# `validate(slide_id, payload) -> ValidationResult` returns a typed result
# with `is_valid`, `errors`, and the parsed model when valid.
#
# No fabricated values: empty / unknown fields must be `null` and listed
# in `data_gaps`.

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


# --------------------------------------------------------------------- base


class _StrictModel(BaseModel):
    """Strict base: forbid unknown fields, strip whitespace from strings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SlideEnvelope(_StrictModel):
    """Common envelope shared by every slide schema.

    Slide-specific schemas extend this and pin ``slide_id`` to a literal.
    """

    slide_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    subtitle: str | None = None
    commentary: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    model_metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------- footprint


class Country(_StrictModel):
    country_code: str | None = Field(default=None, description="ISO 3166-1 alpha-2/3 code.")
    country_name: str | None = None
    region: str | None = None


class Region(_StrictModel):
    region_name: str | None = None
    country_count: int | None = Field(default=None, ge=0)


class FootprintV1(SlideEnvelope):
    SLIDE_ID: ClassVar[str] = "footprint_v1"

    slide_id: Literal["footprint_v1"]
    countries: list[Country] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)
    summary: list[str] = Field(
        default_factory=list,
        description="Short narrative, max 2 bullets.",
        max_length=2,
    )


# ---------------------------------------------------------- payments_stp


class Period(_StrictModel):
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, populate_by_name=True
    )


class StpVolumes(_StrictModel):
    stp_count: int | None = Field(default=None, ge=0)
    non_stp_count: int | None = Field(default=None, ge=0)
    total_count: int | None = Field(default=None, ge=0)


class StpRates(_StrictModel):
    stp_rate_pct: float | None = Field(default=None, ge=0, le=100)
    non_stp_rate_pct: float | None = Field(default=None, ge=0, le=100)


class NonStpReason(_StrictModel):
    reason: str | None = None
    count: int | None = Field(default=None, ge=0)
    pct: float | None = Field(default=None, ge=0, le=100)


class PaymentsStpV1(SlideEnvelope):
    SLIDE_ID: ClassVar[str] = "payments_stp_v1"

    slide_id: Literal["payments_stp_v1"]
    period: Period | None = None
    volumes: StpVolumes | None = None
    rates: StpRates | None = None
    top_reasons_non_stp: list[NonStpReason] = Field(default_factory=list)
    commentary: list[str] = Field(default_factory=list, max_length=2)


# --------------------------------------------------------- query_analysis


class QueryVolumes(_StrictModel):
    total_queries: int | None = Field(default=None, ge=0)


class QueryBreakdown(_StrictModel):
    category: str | None = None
    count: int | None = Field(default=None, ge=0)
    pct: float | None = Field(default=None, ge=0, le=100)


class TopCategoryMovement(_StrictModel):
    category: str | None = None
    count: int | None = Field(default=None, ge=0)
    pct: float | None = Field(default=None, ge=0, le=100)
    movement_vs_prior_pct: float | None = Field(default=None)


class QueryAnalysisV1(SlideEnvelope):
    SLIDE_ID: ClassVar[str] = "query_analysis_v1"

    slide_id: Literal["query_analysis_v1"]
    period: Period | None = None
    volumes: QueryVolumes | None = None
    breakdown: list[QueryBreakdown] = Field(default_factory=list)
    top_categories: list[TopCategoryMovement] = Field(default_factory=list)
    commentary: list[str] = Field(default_factory=list, max_length=2)


# --------------------------------------------------------- product_updates


class ProductUpdate(_StrictModel):
    product: str | None = None
    headline: str | None = None
    description: str | None = None
    effective_date: date | None = None


class ProductUpdatesV1(SlideEnvelope):
    SLIDE_ID: ClassVar[str] = "product_updates_v1"

    slide_id: Literal["product_updates_v1"]
    updates: list[ProductUpdate] = Field(default_factory=list)


# --------------------------------------------------------------- registry


SlideModel = (
    FootprintV1 | PaymentsStpV1 | QueryAnalysisV1 | ProductUpdatesV1
)

_SCHEMA_REGISTRY: dict[str, type[SlideEnvelope]] = {
    FootprintV1.SLIDE_ID: FootprintV1,
    PaymentsStpV1.SLIDE_ID: PaymentsStpV1,
    QueryAnalysisV1.SLIDE_ID: QueryAnalysisV1,
    ProductUpdatesV1.SLIDE_ID: ProductUpdatesV1,
}


# --------------------------------------------------------------- validate


class ValidationResult(_StrictModel):
    """Outcome of validating a slide payload against its schema."""

    is_valid: bool
    slide_id: str
    errors: list[dict[str, Any]] = Field(default_factory=list)
    model: SlideEnvelope | None = None


def validate(slide_id: str, payload: dict[str, Any]) -> ValidationResult:
    """Validate ``payload`` against the schema registered for ``slide_id``.

    Returns a :class:`ValidationResult`. The function never raises for
    schema or unknown-slide errors — those are reported in ``errors`` so
    the orchestration layer can decide policy. ``TypeError`` is still
    raised for clearly malformed inputs (e.g. ``payload`` not a dict).
    """
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    model_cls = _SCHEMA_REGISTRY.get(slide_id)
    if model_cls is None:
        return ValidationResult(
            is_valid=False,
            slide_id=slide_id,
            errors=[
                {
                    "loc": ("slide_id",),
                    "msg": f"No schema registered for slide_id={slide_id!r}",
                    "type": "unknown_slide_id",
                }
            ],
            model=None,
        )

    # Pin slide_id so callers can't validate a footprint payload as
    # payments_stp simply by passing the wrong slide_id argument.
    candidate = {**payload, "slide_id": slide_id}

    try:
        instance = model_cls.model_validate(candidate)
    except ValidationError as exc:
        return ValidationResult(
            is_valid=False,
            slide_id=slide_id,
            errors=[
                {"loc": tuple(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
                for err in exc.errors(include_url=False, include_input=False)
            ],
            model=None,
        )

    return ValidationResult(
        is_valid=True, slide_id=slide_id, errors=[], model=instance
    )


def list_slide_ids() -> list[str]:
    """Return the slide ids with registered schemas, sorted."""
    return sorted(_SCHEMA_REGISTRY.keys())


__all__ = [
    "SlideEnvelope",
    "Country",
    "Region",
    "FootprintV1",
    "Period",
    "StpVolumes",
    "StpRates",
    "NonStpReason",
    "PaymentsStpV1",
    "QueryVolumes",
    "QueryBreakdown",
    "TopCategoryMovement",
    "QueryAnalysisV1",
    "ProductUpdate",
    "ProductUpdatesV1",
    "SlideModel",
    "ValidationResult",
    "validate",
    "list_slide_ids",
]
