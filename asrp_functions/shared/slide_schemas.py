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


# --------------------------------------------------------- service_queries


class ServiceQueriesItem(_StrictModel):
    """A single (name, count) entry for case-type or country breakdowns."""

    name: str | None = None
    count: int | None = Field(default=None, ge=0)


class ServiceQueriesContent(_StrictModel):
    """Slide-21 content payload consumed by the deck assembler."""

    period: str | None = None
    total_cases: int | None = Field(default=None, ge=0)
    top_case_types: list[ServiceQueriesItem] = Field(default_factory=list)
    top_countries: list[ServiceQueriesItem] = Field(default_factory=list)
    excluded_countries: list[str] | None = None
    commentary: str | None = None


class ServiceQueriesV1(SlideEnvelope):
    SLIDE_ID: ClassVar[str] = "service_queries"

    slide_id: Literal["service_queries"]
    customer_id: str | None = None
    content: ServiceQueriesContent | None = None


# --------------------------------------------------------- volume_by_country
#
# A reusable family schema for slides that render a single horizontal
# bar chart of payment / case volumes broken down by country, plus a
# short commentary block. Each concrete slide gets its own ``slide_id``
# (e.g. ``volume_by_country_s29``) so the orchestrator can ground the
# data per slide via Azure AI Search, but every concrete id validates
# against the same schema and is rendered by the same populator.


# Concrete slide_ids in this family. The integer suffix is the 1-based
# slide number in the HSBC CSR template.
_VOLUME_BY_COUNTRY_SLIDE_IDS: tuple[str, ...] = (
    "volume_by_country_s29",
    "volume_by_country_s43",
    "volume_by_country_s49",
)


class CountryCount(_StrictModel):
    """A single ``(country, count)`` row used by the country chart."""

    country: str | None = None
    count: int | None = Field(default=None, ge=0)


class VolumeByCountryContent(_StrictModel):
    """Payload consumed by the volume-by-country populator."""

    period: str | None = None
    total: int | None = Field(default=None, ge=0)
    by_country: list[CountryCount] = Field(default_factory=list)
    commentary: str | None = None


class VolumeByCountryV1(SlideEnvelope):
    """Schema for every slide in the ``volume_by_country`` family.

    ``slide_id`` is constrained by a regex rather than a ``Literal`` so
    one schema class serves all nine concrete slides.
    """

    SLIDE_ID: ClassVar[str] = "volume_by_country"

    slide_id: str = Field(..., pattern=r"^volume_by_country_s\d+$")
    customer_id: str | None = None
    content: VolumeByCountryContent | None = None


# ------------------------------------------------------------- volume_by_type
#
# A reusable family schema for slides that render a clustered/stacked
# bar chart of monthly payment / case volumes broken down into one or
# more named series (e.g. STP vs non-STP, Repaired vs Rejected, case
# types). Each concrete slide carries its own ``slide_id`` (e.g.
# ``volume_by_type_s28``) so the orchestrator grounds per slide via
# Azure AI Search, and every concrete id validates against the same
# schema and is rendered by the same populator.


_VOLUME_BY_TYPE_SLIDE_IDS: tuple[str, ...] = (
    "volume_by_type_s28",
    "volume_by_type_s31",
    "volume_by_type_s32",
    "volume_by_type_s34",
    "volume_by_type_s42",
)


class VolumeByTypeSeries(_StrictModel):
    """A single named series of monthly counts."""

    name: str | None = None
    values: list[int | None] = Field(default_factory=list)


class VolumeByTypeContent(_StrictModel):
    """Payload consumed by the volume-by-type populator.

    ``categories`` is the shared category axis (typically months such as
    ``"Oct'24"``). ``series`` is the list of named series; each series'
    ``values`` array must align positionally with ``categories``.
    """

    period: str | None = None
    total: int | None = Field(default=None, ge=0)
    categories: list[str] = Field(default_factory=list)
    series: list[VolumeByTypeSeries] = Field(default_factory=list)
    commentary: str | None = None


class VolumeByTypeV1(SlideEnvelope):
    """Schema for every slide in the ``volume_by_type`` family."""

    SLIDE_ID: ClassVar[str] = "volume_by_type"

    slide_id: str = Field(..., pattern=r"^volume_by_type_s\d+$")
    customer_id: str | None = None
    content: VolumeByTypeContent | None = None


# ------------------------------------------------------------- channel_mix
#
# Family for the doughnut-chart slides showing transaction volumes by
# channel (e.g. SWIFT, FLU, H2H, HSBCnet, API). Each slide has one or
# more doughnut charts; each chart represents one channel and slices
# the volume by country code. Concrete slide_ids match
# ``channel_mix_s\d+``.


_CHANNEL_MIX_SLIDE_IDS: tuple[str, ...] = (
    "channel_mix_s46",
    "channel_mix_s47",
    "channel_mix_s48",
)


class ChannelMixChannel(_StrictModel):
    """One channel doughnut: a name and country-coded slices."""

    name: str | None = None
    by_country: list[CountryCount] = Field(default_factory=list)


class ChannelMixContent(_StrictModel):
    """Payload consumed by the channel-mix populator.

    ``channels`` is the ordered list of channel doughnuts to render;
    its length should match the number of doughnut chart shapes on the
    target slide.
    """

    period: str | None = None
    total: int | None = Field(default=None, ge=0)
    channels: list[ChannelMixChannel] = Field(default_factory=list)
    commentary: str | None = None


class ChannelMixV1(SlideEnvelope):
    """Schema for every slide in the ``channel_mix`` family."""

    SLIDE_ID: ClassVar[str] = "channel_mix"

    slide_id: str = Field(..., pattern=r"^channel_mix_s\d+$")
    customer_id: str | None = None
    content: ChannelMixContent | None = None


# -------------------------------------------------------- volume_with_table
#
# Family for the priority-payment, ACH, DD and RTP slides that pair a
# clustered bar chart (months as categories, currencies as series)
# with a Top-5 counterparty table (name, location, value). One concrete
# slide_id per template slide; all share this schema and populator.


_VOLUME_WITH_TABLE_SLIDE_IDS: tuple[str, ...] = (
    "volume_with_table_s33",
    "volume_with_table_s35",
    "volume_with_table_s36",
    "volume_with_table_s37",
    "volume_with_table_s38",
    "volume_with_table_s39",
    "volume_with_table_s40",
    "volume_with_table_s41",
)


class CounterpartyRow(_StrictModel):
    """A single row in the Top-5 counterparty table.

    ``value`` is rendered verbatim into the third column. The caller
    is responsible for formatting (e.g. ``"USD 12.4m"`` or ``"284"``)
    so the same schema serves both volume and value variants.
    """

    name: str | None = None
    location: str | None = None
    value: str | None = None


class VolumeWithTableContent(_StrictModel):
    """Payload consumed by the volume-with-table populator."""

    period: str | None = None
    total: int | None = Field(default=None, ge=0)
    chart_categories: list[str] = Field(default_factory=list)
    chart_series: list[VolumeByTypeSeries] = Field(default_factory=list)
    table_rows: list[CounterpartyRow] = Field(default_factory=list)
    commentary: str | None = None


class VolumeWithTableV1(SlideEnvelope):
    """Schema for every slide in the ``volume_with_table`` family."""

    SLIDE_ID: ClassVar[str] = "volume_with_table"

    slide_id: str = Field(..., pattern=r"^volume_with_table_s\d+$")
    customer_id: str | None = None
    content: VolumeWithTableContent | None = None


# ------------------------------------------------------------- case_subtype
#
# Family for the lone case-subtype bar slide (S22). Single chart, one
# series, free-form text categories (e.g. ``Status / Trace``).


_CASE_SUBTYPE_SLIDE_IDS: tuple[str, ...] = (
    "case_subtype_s22",
)


class NamedCount(_StrictModel):
    """Generic ``(name, count)`` row used by single-series bar charts."""

    name: str | None = None
    count: int | None = Field(default=None, ge=0)
    # Optional dominant sub-drivers within this bucket (e.g. the
    # leading case sub-types within the "Additional Details" case-type
    # bucket). When populated, the case_subtype assembler renders these
    # into the rotated annotation TextBoxes above the corresponding bar
    # (up to two labels per bar, matching the HSBC template layout).
    top_drivers: list[str] = Field(default_factory=list, max_length=2)


class CaseSubtypeContent(_StrictModel):
    """Payload consumed by the case-subtype populator."""

    period: str | None = None
    total: int | None = Field(default=None, ge=0)
    by_subtype: list[NamedCount] = Field(default_factory=list)
    commentary: str | None = None


class CaseSubtypeV1(SlideEnvelope):
    """Schema for the ``case_subtype`` family."""

    SLIDE_ID: ClassVar[str] = "case_subtype"

    slide_id: str = Field(..., pattern=r"^case_subtype_s\d+$")
    customer_id: str | None = None
    content: CaseSubtypeContent | None = None


# --------------------------------------------------------- multi_chart_trend
#
# Family for slides whose body is one or more multi-series charts that
# all share a category axis (months). Used for the bar+line trend
# slides (S20, S25) and the dual stacked-bar channel slides (S45).
# Each entry in ``charts`` populates the chart at the same on-slide
# index. Series ordering must match the chart's authored series order
# (bar series first then line series, etc.) because we rewrite cached
# ``<c:ser>`` elements in document order.


_MULTI_CHART_TREND_SLIDE_IDS: tuple[str, ...] = (
    "multi_chart_trend_s20",
    "multi_chart_trend_s25",
    "multi_chart_trend_s45",
)


class MultiChartTrendChart(_StrictModel):
    """One chart on a multi-chart trend slide."""

    categories: list[str] = Field(default_factory=list)
    series: list[VolumeByTypeSeries] = Field(default_factory=list)


class MultiChartTrendContent(_StrictModel):
    """Payload consumed by the multi-chart-trend populator."""

    period: str | None = None
    charts: list[MultiChartTrendChart] = Field(default_factory=list)
    commentary: str | None = None


class MultiChartTrendV1(SlideEnvelope):
    """Schema for the ``multi_chart_trend`` family."""

    SLIDE_ID: ClassVar[str] = "multi_chart_trend"

    slide_id: str = Field(..., pattern=r"^multi_chart_trend_s\d+$")
    customer_id: str | None = None
    content: MultiChartTrendContent | None = None


# --------------------------------------------------------------- registry


SlideModel = (
    FootprintV1
    | PaymentsStpV1
    | QueryAnalysisV1
    | ProductUpdatesV1
    | ServiceQueriesV1
    | VolumeByCountryV1
    | VolumeByTypeV1
    | ChannelMixV1
    | VolumeWithTableV1
    | CaseSubtypeV1
    | MultiChartTrendV1
)

_SCHEMA_REGISTRY: dict[str, type[SlideEnvelope]] = {
    FootprintV1.SLIDE_ID: FootprintV1,
    PaymentsStpV1.SLIDE_ID: PaymentsStpV1,
    QueryAnalysisV1.SLIDE_ID: QueryAnalysisV1,
    ProductUpdatesV1.SLIDE_ID: ProductUpdatesV1,
    ServiceQueriesV1.SLIDE_ID: ServiceQueriesV1,
    **{sid: VolumeByCountryV1 for sid in _VOLUME_BY_COUNTRY_SLIDE_IDS},
    **{sid: VolumeByTypeV1 for sid in _VOLUME_BY_TYPE_SLIDE_IDS},
    **{sid: ChannelMixV1 for sid in _CHANNEL_MIX_SLIDE_IDS},
    **{sid: VolumeWithTableV1 for sid in _VOLUME_WITH_TABLE_SLIDE_IDS},
    **{sid: CaseSubtypeV1 for sid in _CASE_SUBTYPE_SLIDE_IDS},
    **{sid: MultiChartTrendV1 for sid in _MULTI_CHART_TREND_SLIDE_IDS},
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
    "ServiceQueriesItem",
    "ServiceQueriesContent",
    "ServiceQueriesV1",
    "CountryCount",
    "VolumeByCountryContent",
    "VolumeByCountryV1",
    "VolumeByTypeSeries",
    "VolumeByTypeContent",
    "VolumeByTypeV1",
    "ChannelMixChannel",
    "ChannelMixContent",
    "ChannelMixV1",
    "CounterpartyRow",
    "VolumeWithTableContent",
    "VolumeWithTableV1",
    "NamedCount",
    "CaseSubtypeContent",
    "CaseSubtypeV1",
    "MultiChartTrendChart",
    "MultiChartTrendContent",
    "MultiChartTrendV1",
    "SlideModel",
    "ValidationResult",
    "validate",
    "list_slide_ids",
]
