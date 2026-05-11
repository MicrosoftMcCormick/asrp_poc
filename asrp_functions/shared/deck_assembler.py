# Implement `DeckAssembler` for the HSBC ASRP Phase 1 POC.
# Inputs: HSBC CSR template bytes, list of validated slide descriptions
# (common envelope + slide-specific schema).
# Output: PPTX bytes.
#
# Behaviour:
# - Open the template with python-pptx (Presentation(BytesIO(template_bytes))).
# - For each slide description, locate the corresponding template slide
#   by `slide_id` mapped to a named slide in the template (mapping table
#   provided as a constant; placeholders match those in the brand-
#   reviewed deck).
# - Populate placeholders for: title, subtitle, commentary bullets,
#   tables (e.g. payments analysis), and named text frames.
# - Slide-specific population:
#     hsbc_footprint        - countries table + regions block
#     payments_analysis_stp - period, volumes, rates, top_reasons_non_stp table
#     query_analysis        - breakdown + top_categories
#     product_updates       - updates list
# - For any field that is null OR appears in `data_gaps`, leave the
#   placeholder blank. A "[data not available]" marker is only ever
#   written in the commentary area — never in numerical fields.
# - Phase 1 limitation: do NOT generate native PowerPoint charts. If the
#   slide_id requires a chart, expect a pre-rendered image asset path
#   and embed it via assets from TemplateStore.
# - On a per-slide assembly error, log the slide_id, skip that slide's
#   content (leave template placeholders untouched) and add the slide_id
#   to a `failed_slide_ids` return list.
# - Return (deck_bytes, failed_slide_ids).

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Iterable

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.presentation import Presentation as PresentationType
from pptx.shapes.autoshape import Shape
from pptx.slide import Slide
from pptx.table import Table, _Cell
from pptx.util import Emu

from shared.logging import get_logger
from shared.template_store import TemplateStore

logger = get_logger(__name__)

DATA_NOT_AVAILABLE_MARKER = "[data not available]"

# slide_id (schema) -> template slide name as authored in the brand-reviewed deck.
SLIDE_ID_TO_TEMPLATE_NAME: dict[str, str] = {
    "footprint_v1": "hsbc_footprint",
    "payments_stp_v1": "payments_analysis_stp",
    "query_analysis_v1": "query_analysis",
    "product_updates_v1": "product_updates",
    "service_queries": "service_queries",
}

# Direct positional fallback. The HSBC CSR template does not author
# unique slide names on every slide, so for the data-driven slides we
# pin the slide_id to its 0-based index in the template's slide
# sequence. The assembler tries this map first, then the named lookup,
# then text-fragment fallbacks.
SLIDE_ID_TO_INDEX: dict[str, int] = {
    "service_queries": 20,  # Slide 21 in the deck (1-based)
    "volume_by_country_s29": 28,
    "volume_by_country_s43": 42,
    "volume_by_country_s49": 48,
    "volume_by_type_s28": 27,
    "volume_by_type_s31": 30,
    "volume_by_type_s32": 31,
    "volume_by_type_s34": 33,
    "volume_by_type_s42": 41,
    "channel_mix_s46": 45,
    "channel_mix_s47": 46,
    "channel_mix_s48": 47,
    "volume_with_table_s33": 32,
    "volume_with_table_s35": 34,
    "volume_with_table_s36": 35,
    "volume_with_table_s37": 36,
    "volume_with_table_s38": 37,
    "volume_with_table_s39": 38,
    "volume_with_table_s40": 39,
    "volume_with_table_s41": 40,
    "case_subtype_s22": 21,
    "multi_chart_trend_s20": 19,
    "multi_chart_trend_s25": 24,
    "multi_chart_trend_s45": 44,
}

# Fallback text fragments used to locate a slide when the authored
# template name is missing. All listed fragments must be present (case-
# insensitive) on the candidate slide.
SLIDE_ID_TEXT_FALLBACKS: dict[str, tuple[str, ...]] = {
    "service_queries": (
        "Global Volumes by Case Type",
        "Global Volumes by Country",
    ),
}

# Token variants used across the HSBC CSR template for the customer /
# account name placeholder. PowerPoint occasionally splits these across
# runs and uses inconsistent spacing around the leading ``MG`` token,
# hence the multiple variants. ``_replace_text_tokens`` matches at the
# paragraph level so split runs are still substituted.
_CUSTOMER_NAME_TOKENS: tuple[str, ...] = (
    "[MG / Legal Entity / Client / Account Name]",
    "[MG/ Legal Entity / Client / Account Name]",
    "[MG / Legal Entity / Client /Account Name]",
    "[MG/ Legal Entity / Client /Account Name]",
)

# Token variants used for the reporting period placeholder.
_PERIOD_TOKENS: tuple[str, ...] = (
    "[MMM YYYY \u2013 MMM YYYY]",  # en-dash (template uses this one)
    "[MMM YYYY - MMM YYYY]",
)


@dataclass(frozen=True)
class _SlideHandle:
    """Lookup result pairing a template slide with its declared name."""

    name: str
    slide: Slide


class DeckAssembler:
    """Assemble the HSBC CSR PPTX deck from validated slide descriptions."""

    def __init__(self, template_store: TemplateStore | None = None) -> None:
        self._template_store = template_store

    # ------------------------------------------------------------------ public

    def assemble(
        self,
        template_bytes: bytes,
        slides: list[dict[str, Any]],
        *,
        customer_name: str | None = None,
        period: str | None = None,
    ) -> tuple[bytes, list[str]]:
        """Render ``slides`` onto the template and return ``(bytes, failed)``.

        Parameters
        ----------
        template_bytes:
            Raw PPTX bytes of the HSBC CSR template.
        slides:
            Validated per-slide JSON descriptions.
        customer_name:
            Optional display name (e.g. ``"Alpine Industries Ltd"``).
            When provided, every occurrence of the
            ``[MG / Legal Entity / Client / Account Name]`` token (and
            its punctuation variants) across the deck is replaced with
            this value. When omitted, the placeholder tokens are left
            untouched.
        period:
            Optional reporting-period label (e.g.
            ``"Oct 2024 \u2013 Sep 2025"``). When provided, every
            occurrence of the ``[MMM YYYY \u2013 MMM YYYY]`` token
            across the deck is replaced with this value.
        """
        if not template_bytes:
            raise ValueError("template_bytes must be non-empty")

        prs = Presentation(io.BytesIO(template_bytes))

        # Apply deck-wide header tokens before per-slide population so a
        # later populator that targets the same shape (e.g. service
        # queries) can rewrite already-substituted text without having
        # to know about the global tokens.
        self._apply_global_tokens(prs, customer_name=customer_name, period=period)

        slide_index = self._index_slides(prs)
        failed_slide_ids: list[str] = []

        for desc in slides:
            slide_id = str(desc.get("slide_id") or "")
            if not slide_id:
                logger.error("deck_assembler.missing_slide_id")
                failed_slide_ids.append("<unknown>")
                continue

            template_name = SLIDE_ID_TO_TEMPLATE_NAME.get(slide_id)

            # Resolution order: explicit positional index, then authored
            # template name, then text-fragment fallback.
            handle: _SlideHandle | None = None
            slide_index_position = SLIDE_ID_TO_INDEX.get(slide_id)
            if slide_index_position is not None and 0 <= slide_index_position < len(prs.slides):
                handle = _SlideHandle(
                    name=template_name or slide_id,
                    slide=prs.slides[slide_index_position],
                )
            if handle is None and template_name is not None:
                handle = slide_index.get(template_name)
            if handle is None:
                handle = self._find_slide_by_text(prs, slide_id)

            if handle is None:
                logger.error(
                    "deck_assembler.template_slide_missing",
                    extra={"slide_id": slide_id, "template_name": template_name},
                )
                failed_slide_ids.append(slide_id)
                continue

            try:
                self._populate_slide(handle.slide, slide_id, desc)
            except Exception as exc:  # noqa: BLE001 - per-slide isolation
                logger.error(
                    "deck_assembler.slide_failed",
                    extra={
                        "slide_id": slide_id,
                        "template_name": template_name,
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc),
                    },
                    exc_info=True,
                )
                failed_slide_ids.append(slide_id)
                continue

        # Final sweep: strip any leftover ``[  ]`` commentary placeholder
        # tokens authored on slides that have no per-slide populator
        # (cover page, agenda, dividers, narrative-only slides). Slides
        # whose populator already substituted commentary are unaffected
        # because the token is no longer present.
        leftover_replacements = {self._COMMENTARY_TOKEN: " "}
        for slide in prs.slides:
            self._replace_text_tokens(slide, leftover_replacements)

        buf = io.BytesIO()
        prs.save(buf)
        return buf.getvalue(), failed_slide_ids

    # ------------------------------------------------------------- per-slide

    def _populate_slide(
        self, slide: Slide, slide_id: str, desc: dict[str, Any]
    ) -> None:
        data_gaps = set(desc.get("data_gaps") or [])

        # Slide 21 (service_queries) and the volume_by_country family
        # do not use the generic title/subtitle/commentary placeholders
        # — their headings are rendered via deck-wide token substitution
        # and their commentary lives in a dedicated text frame on the
        # slide. Skip the envelope-level writers so we don't accidentally
        # overwrite the title with commentary text.
        skip_envelope_writers = (
            slide_id == "service_queries"
            or slide_id.startswith("volume_by_country_")
            or slide_id.startswith("volume_by_type_")
            or slide_id.startswith("channel_mix_")
            or slide_id.startswith("volume_with_table_")
            or slide_id.startswith("case_subtype_")
            or slide_id.startswith("multi_chart_trend_")
        )
        if not skip_envelope_writers:
            self._set_named_text(
                slide, "title", desc.get("title"), data_gaps, field="title"
            )
            self._set_named_text(
                slide, "subtitle", desc.get("subtitle"), data_gaps, field="subtitle"
            )
            self._set_commentary(slide, desc.get("commentary") or [], data_gaps)

        # Slide-specific.
        if slide_id == "footprint_v1":
            self._populate_footprint(slide, desc, data_gaps)
        elif slide_id == "payments_stp_v1":
            self._populate_payments_stp(slide, desc, data_gaps)
        elif slide_id == "query_analysis_v1":
            self._populate_query_analysis(slide, desc, data_gaps)
        elif slide_id == "product_updates_v1":
            self._populate_product_updates(slide, desc, data_gaps)
        elif slide_id == "service_queries":
            self._populate_service_queries(slide, desc, data_gaps)
        elif slide_id.startswith("volume_by_country_"):
            self._populate_volume_by_country(slide, desc, data_gaps)
        elif slide_id.startswith("volume_by_type_"):
            self._populate_volume_by_type(slide, desc, data_gaps)
        elif slide_id.startswith("channel_mix_"):
            self._populate_channel_mix(slide, desc, data_gaps)
        elif slide_id.startswith("volume_with_table_"):
            self._populate_volume_with_table(slide, desc, data_gaps)
        elif slide_id.startswith("case_subtype_"):
            self._populate_case_subtype(slide, desc, data_gaps)
        elif slide_id.startswith("multi_chart_trend_"):
            self._populate_multi_chart_trend(slide, desc, data_gaps)

    # --- footprint_v1 ----------------------------------------------------

    def _populate_footprint(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        countries = desc.get("countries") or []
        regions = desc.get("regions") or []
        summary = desc.get("summary") or []

        country_table = self._find_table(slide, "countries_table")
        if country_table is not None:
            self._fill_table(
                country_table,
                rows=[
                    [
                        c.get("country_code"),
                        c.get("country_name"),
                        c.get("region"),
                    ]
                    for c in countries
                ],
            )

        regions_text = "\n".join(
            f"{(r.get('region_name') or '')}: "
            f"{r.get('country_count') if r.get('country_count') is not None else ''}".rstrip(": ")
            for r in regions
        )
        self._set_named_text(
            slide, "regions_block", regions_text or None, data_gaps, field="regions"
        )

        # Slide-specific summary lives alongside commentary on this slide.
        self._set_named_bullets(slide, "summary_block", summary)

    # --- payments_stp_v1 -------------------------------------------------

    def _populate_payments_stp(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        period = desc.get("period") or {}
        volumes = desc.get("volumes") or {}
        rates = desc.get("rates") or {}
        reasons = desc.get("top_reasons_non_stp") or []

        self._set_named_text(
            slide,
            "period_from",
            period.get("from"),
            data_gaps,
            field="period.from",
            numeric=True,
        )
        self._set_named_text(
            slide,
            "period_to",
            period.get("to"),
            data_gaps,
            field="period.to",
            numeric=True,
        )

        for ph_name, value, field in (
            ("stp_count", volumes.get("stp_count"), "volumes.stp_count"),
            ("non_stp_count", volumes.get("non_stp_count"), "volumes.non_stp_count"),
            ("total_count", volumes.get("total_count"), "volumes.total_count"),
            ("stp_rate_pct", rates.get("stp_rate_pct"), "rates.stp_rate_pct"),
            (
                "non_stp_rate_pct",
                rates.get("non_stp_rate_pct"),
                "rates.non_stp_rate_pct",
            ),
        ):
            self._set_named_text(
                slide, ph_name, value, data_gaps, field=field, numeric=True
            )

        reasons_table = self._find_table(slide, "top_reasons_non_stp_table")
        if reasons_table is not None:
            self._fill_table(
                reasons_table,
                rows=[
                    [r.get("reason"), r.get("count"), r.get("pct")]
                    for r in reasons
                ],
            )

    # --- query_analysis_v1 ----------------------------------------------

    def _populate_query_analysis(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        period = desc.get("period") or {}
        volumes = desc.get("volumes") or {}
        breakdown = desc.get("breakdown") or []
        top_categories = desc.get("top_categories") or []

        self._set_named_text(
            slide, "period_from", period.get("from"), data_gaps,
            field="period.from", numeric=True,
        )
        self._set_named_text(
            slide, "period_to", period.get("to"), data_gaps,
            field="period.to", numeric=True,
        )
        self._set_named_text(
            slide, "total_queries", volumes.get("total_queries"), data_gaps,
            field="volumes.total_queries", numeric=True,
        )

        breakdown_table = self._find_table(slide, "breakdown_table")
        if breakdown_table is not None:
            self._fill_table(
                breakdown_table,
                rows=[
                    [b.get("category"), b.get("count"), b.get("pct")]
                    for b in breakdown
                ],
            )

        top_categories_table = self._find_table(slide, "top_categories_table")
        if top_categories_table is not None:
            self._fill_table(
                top_categories_table,
                rows=[
                    [
                        t.get("category"),
                        t.get("count"),
                        t.get("pct"),
                        t.get("movement_vs_prior_pct"),
                    ]
                    for t in top_categories
                ],
            )

    # --- product_updates_v1 ---------------------------------------------

    def _populate_product_updates(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        updates = desc.get("updates") or []
        updates_table = self._find_table(slide, "updates_table")
        if updates_table is not None:
            self._fill_table(
                updates_table,
                rows=[
                    [
                        u.get("product"),
                        u.get("headline"),
                        u.get("description"),
                        u.get("effective_date"),
                    ]
                    for u in updates
                ],
            )

    # --- service_queries (Slide 21) -------------------------------------

    _SERVICE_QUERIES_CUSTOMER_TOKENS = (
        "[MG / Legal Entity / Client / Account Name]",
        "[MG/ Legal Entity / Client / Account Name]",
    )
    _SERVICE_QUERIES_PERIOD_TOKENS = (
        "[MMM YYYY – MMM YYYY]",
        "[MMM YYYY - MMM YYYY]",
    )
    _SERVICE_QUERIES_EXCLUSION_HINT = "exclud"

    def _populate_service_queries(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        content = desc.get("content") or {}
        customer_id = desc.get("customer_id")
        period = content.get("period")
        top_case_types = content.get("top_case_types") or []
        top_countries = content.get("top_countries") or []
        commentary = content.get("commentary")

        customer_label = "" if customer_id is None else str(customer_id)
        period_label = "" if period is None else str(period)

        # 1) Replace title placeholders in-place, preserving formatting.
        replacements: dict[str, str] = {}
        for tok in self._SERVICE_QUERIES_CUSTOMER_TOKENS:
            replacements[tok] = customer_label
        for tok in self._SERVICE_QUERIES_PERIOD_TOKENS:
            replacements[tok] = period_label

        self._replace_text_tokens(slide, replacements)

        # 2) Replace chart data.
        # Expected chart order on Slide 21:
        #   first chart  -> Global Volumes by Case Type
        #   second chart -> Global Volumes by Country
        chart_shapes = [s for s in slide.shapes if getattr(s, "has_chart", False)]

        if len(chart_shapes) >= 1 and top_case_types:
            self._replace_chart_data(
                chart_shapes[0],
                categories=[str(c.get("name") or "") for c in top_case_types],
                values=[c.get("count") for c in top_case_types],
                series_name="Cases",
            )

        if len(chart_shapes) >= 2 and top_countries:
            self._replace_chart_data(
                chart_shapes[1],
                categories=[str(c.get("name") or "") for c in top_countries],
                values=[c.get("count") for c in top_countries],
                series_name="Cases",
            )

        # NOTE: Slide 21 has no commentary placeholder; commentary text
        # is intentionally not rendered here. Including it would risk
        # overwriting the title or chart heading shapes.

    # --- volume_by_country (Slides 22, 29, 32, 34, 38, 40, 42, 43, 49) ---

    # The HSBC CSR template authors a literal ``[  ]`` (square brackets
    # wrapping two spaces) as the commentary placeholder on every
    # data-driven slide in this family. Per-slide commentary is written
    # by replacing this token with the LLM-supplied text.
    _COMMENTARY_TOKEN = "[  ]"

    def _populate_volume_by_country(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        content = desc.get("content") or {}
        by_country = content.get("by_country") or []
        commentary = content.get("commentary")

        # 1) Replace the single bar chart's category/value pairs.
        chart_shapes = [s for s in slide.shapes if getattr(s, "has_chart", False)]
        if chart_shapes and by_country:
            self._replace_chart_data(
                chart_shapes[0],
                categories=[str(c.get("country") or "") for c in by_country],
                values=[c.get("count") for c in by_country],
                series_name="Volume",
            )

        # 2) Render commentary into the dedicated text frame by
        #    replacing the ``[  ]`` placeholder token. Falls back to a
        #    single space when commentary is missing so the bracket
        #    artefact does not appear in the rendered deck.
        commentary_text = (commentary or "").strip()
        self._replace_text_tokens(
            slide,
            {self._COMMENTARY_TOKEN: commentary_text or " "},
        )

    # --- volume_by_type (Slides 28, 31, 32, 34, 42) ---------------------
    #
    # Clustered/stacked bar chart with monthly categories and one or
    # more named series. Reuses the ``[  ]`` commentary token.

    def _populate_volume_by_type(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        content = desc.get("content") or {}
        categories = [str(c) for c in (content.get("categories") or [])]
        series_payload = content.get("series") or []
        commentary = content.get("commentary")

        chart_shapes = [s for s in slide.shapes if getattr(s, "has_chart", False)]
        if chart_shapes and categories and series_payload:
            series = [
                {
                    "name": str(s.get("name") or ""),
                    "values": [0 if v is None else v for v in (s.get("values") or [])],
                }
                for s in series_payload
            ]
            self._replace_chart_data_multi_series(
                chart_shapes[0], categories=categories, series=series
            )

        commentary_text = (commentary or "").strip()
        self._replace_text_tokens(
            slide,
            {self._COMMENTARY_TOKEN: commentary_text or " "},
        )

    # --- channel_mix (Slides 46, 47, 48) --------------------------------
    #
    # Each slide has one doughnut chart per channel (e.g. SWIFT, FLU,
    # H2H, HSBCnet, On screen, API). Each doughnut has a single series
    # whose categories are country codes (HK, SG, FR, DE, UKRFB).

    def _populate_channel_mix(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        content = desc.get("content") or {}
        channels = content.get("channels") or []
        commentary = content.get("commentary")

        chart_shapes = [s for s in slide.shapes if getattr(s, "has_chart", False)]
        for shape, channel in zip(chart_shapes, channels):
            by_country = channel.get("by_country") or []
            if not by_country:
                continue
            self._replace_chart_data(
                shape,
                categories=[str(c.get("country") or "") for c in by_country],
                values=[c.get("count") for c in by_country],
                series_name=str(channel.get("name") or "Volume"),
            )

        commentary_text = (commentary or "").strip()
        self._replace_text_tokens(
            slide,
            {self._COMMENTARY_TOKEN: commentary_text or " "},
        )

    # --- volume_with_table (Slides 33, 35-41) ---------------------------
    #
    # Each slide pairs a clustered bar chart (months as categories,
    # currencies as named series) with a Top-5 counterparty table whose
    # columns are {Name, Location, Value}. Header row is left untouched
    # because it varies by slide (Beneficiary vs Remitter, Volumes vs
    # Amount). Up to 5 data rows are populated; extra payload rows are
    # ignored, missing rows leave the template cells blank.

    def _populate_volume_with_table(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        content = desc.get("content") or {}
        categories = [str(c) for c in (content.get("chart_categories") or [])]
        series_payload = content.get("chart_series") or []
        table_rows = content.get("table_rows") or []
        commentary = content.get("commentary")

        # 1) Chart: same shape as volume_by_type.
        chart_shapes = [s for s in slide.shapes if getattr(s, "has_chart", False)]
        if chart_shapes and categories and series_payload:
            series = [
                {
                    "name": str(s.get("name") or ""),
                    "values": [
                        0 if v is None else v for v in (s.get("values") or [])
                    ],
                }
                for s in series_payload
            ]
            self._replace_chart_data_multi_series(
                chart_shapes[0], categories=categories, series=series
            )

        # 2) Table: locate first table on the slide and write up to 5
        #    data rows (rows 2..6 of the template, leaving the header).
        table_shape = next(
            (s for s in slide.shapes if getattr(s, "has_table", False)),
            None,
        )
        if table_shape is not None and table_rows:
            tbl = table_shape.table
            data_row_indices = list(range(1, len(tbl.rows)))  # skip header
            for ri, row_payload in zip(data_row_indices, table_rows):
                row = tbl.rows[ri]
                cells = list(row.cells)
                values_in_order = [
                    row_payload.get("name"),
                    row_payload.get("location"),
                    row_payload.get("value"),
                ]
                for ci, cell in enumerate(cells[: len(values_in_order)]):
                    new_text = values_in_order[ci]
                    # Use the row's first cell as the formatting
                    # reference so the value column inherits the same
                    # font weight and size as the other columns. The
                    # template's value column was authored with a
                    # different font (Light vs Medium) and no explicit
                    # size, which renders inconsistently otherwise.
                    fmt_source = cells[0] if ci > 0 else None
                    self._set_cell_text(
                        cell,
                        "" if new_text is None else str(new_text),
                        formatting_source=fmt_source,
                    )

        commentary_text = (commentary or "").strip()
        self._replace_text_tokens(
            slide,
            {self._COMMENTARY_TOKEN: commentary_text or " "},
        )

    # --- case_subtype (Slide 22) ----------------------------------------
    #
    # Single bar chart, one series, multi-line text categories. The
    # template additionally carries rotated annotation TextBoxes above
    # each bar (one or two per bar) which we populate from the
    # ``top_drivers`` payload to label the dominant sub-types within
    # each bucket. Any annotation boxes with no grounded driver are
    # cleared.

    def _populate_case_subtype(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        content = desc.get("content") or {}
        by_subtype = content.get("by_subtype") or []
        commentary = content.get("commentary")

        chart_shapes = [s for s in slide.shapes if getattr(s, "has_chart", False)]
        if chart_shapes and by_subtype:
            self._replace_chart_data(
                chart_shapes[0],
                categories=[str(c.get("name") or "") for c in by_subtype],
                values=[c.get("count") for c in by_subtype],
                series_name="Cases",
            )
            # Assign rotated annotation TextBoxes to bars by x-order
            # using the chart's plot area as a reference.
            self._populate_case_subtype_drivers(
                slide,
                chart_shapes[0],
                by_subtype,
            )

        commentary_text = (commentary or "").strip()
        self._replace_text_tokens(
            slide,
            {self._COMMENTARY_TOKEN: commentary_text or " "},
        )

    def _populate_case_subtype_drivers(
        self,
        slide: Slide,
        chart_shape: Any,
        by_subtype: list[dict[str, Any]],
    ) -> None:
        """Map rotated annotation TextBoxes above the chart to bar buckets.

        Rotated text boxes inside the chart's horizontal span are
        sorted by their left coordinate and split evenly across the
        ``by_subtype`` buckets in the same order. The first up to two
        boxes assigned to a bucket receive ``top_drivers[0]`` and
        ``top_drivers[1]`` respectively; remaining boxes for that
        bucket and any bucket without grounded drivers are cleared.
        """
        if not by_subtype:
            return

        chart_left = int(chart_shape.left or 0)
        chart_right = chart_left + int(chart_shape.width or 0)

        rotated: list[tuple[int, Any]] = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            try:
                rot = float(shape.rotation or 0.0)
            except (TypeError, ValueError):
                rot = 0.0
            if abs(rot) < 0.5:
                continue
            left = int(shape.left or 0)
            if left < chart_left or left > chart_right:
                continue
            rotated.append((left, shape))

        if not rotated:
            return

        rotated.sort(key=lambda item: item[0])
        n_boxes = len(rotated)
        n_buckets = len(by_subtype)
        if n_buckets == 0:
            return

        # Distribute boxes across buckets as evenly as possible while
        # preserving x-order. Any leftover from integer division goes
        # to the leading buckets, matching the template layout where
        # the dominant buckets carry more annotation labels.
        base, extra = divmod(n_boxes, n_buckets)
        assignments: list[list[Any]] = []
        cursor = 0
        for i in range(n_buckets):
            take = base + (1 if i < extra else 0)
            assignments.append([shape for _, shape in rotated[cursor : cursor + take]])
            cursor += take

        for row, shapes in zip(by_subtype, assignments):
            drivers = list(row.get("top_drivers") or [])
            for idx, shape in enumerate(shapes):
                text = drivers[idx] if idx < len(drivers) else ""
                self._set_textbox_text(shape, text or " ")

    @staticmethod
    def _set_textbox_text(shape: Any, text: str) -> None:
        """Replace the first paragraph text in ``shape`` preserving formatting.

        Keeps the original ``<a:rPr>`` of the first run so font, size
        and rotation-related styling carry through. Empties any
        additional runs/paragraphs.
        """
        tf = shape.text_frame
        if not tf.paragraphs:
            return
        first_para = tf.paragraphs[0]
        # Clear additional paragraphs by setting their runs to empty.
        for para in tf.paragraphs[1:]:
            for run in para.runs:
                run.text = ""
        if not first_para.runs:
            # No run -> add one by writing the text directly via the
            # paragraph's underlying element machinery.
            first_para.text = text
            return
        first_run = first_para.runs[0]
        first_run.text = text
        for run in first_para.runs[1:]:
            run.text = ""

    # --- multi_chart_trend (Slides 20, 25, 45) --------------------------
    #
    # One or more multi-series charts on the same slide, all sharing a
    # category axis. Each ``charts`` payload entry maps positionally to
    # the chart shape at that index. Series ordering must match the
    # template's authored series order (bar series first, then line
    # series, etc.) because the underlying writer rewrites cached
    # ``<c:ser>`` elements in document order.

    def _populate_multi_chart_trend(
        self, slide: Slide, desc: dict[str, Any], data_gaps: set[str]
    ) -> None:
        content = desc.get("content") or {}
        charts_payload = content.get("charts") or []
        commentary = content.get("commentary")

        chart_shapes = [s for s in slide.shapes if getattr(s, "has_chart", False)]
        for shape, chart_payload in zip(chart_shapes, charts_payload):
            categories = [str(c) for c in (chart_payload.get("categories") or [])]
            series_payload = chart_payload.get("series") or []
            if not categories or not series_payload:
                continue
            series = [
                {
                    "name": str(s.get("name") or ""),
                    "values": [
                        0 if v is None else v for v in (s.get("values") or [])
                    ],
                }
                for s in series_payload
            ]
            self._replace_chart_data_multi_series(
                shape, categories=categories, series=series
            )

        commentary_text = (commentary or "").strip()
        self._replace_text_tokens(
            slide,
            {self._COMMENTARY_TOKEN: commentary_text or " "},
        )

    @staticmethod
    def _first_run_rpr(cell: _Cell) -> Any | None:
        """Return the ``<a:rPr>`` element of the cell's first run, or None."""
        tf = cell.text_frame
        if not tf.paragraphs:
            return None
        runs = tf.paragraphs[0].runs
        if not runs:
            return None
        # python-pptx exposes the underlying ``<a:r>`` element via ``_r``;
        # its ``<a:rPr>`` child carries the run formatting.
        r_el = runs[0]._r
        a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
        return r_el.find(f"{{{a_ns}}}rPr")

    @staticmethod
    def _set_run_rpr(run: Any, rpr_el: Any) -> None:
        """Replace the run's ``<a:rPr>`` child with ``rpr_el``.

        ``rpr_el`` must be an lxml element already detached / cloned
        from any other tree. ``<a:rPr>`` is required by the schema to
        appear before ``<a:t>``.
        """
        a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
        r_el = run._r
        existing = r_el.find(f"{{{a_ns}}}rPr")
        if existing is not None:
            r_el.remove(existing)
        # Insert as the first child so it precedes ``<a:t>``.
        r_el.insert(0, rpr_el)

    @staticmethod
    def _set_cell_text(
        cell: _Cell, text: str, *, formatting_source: _Cell | None = None
    ) -> None:
        """Overwrite a table cell's text while preserving its first
        run's formatting. Subsequent runs are removed.

        When ``formatting_source`` is provided, the source cell's
        first-run ``<a:rPr>`` is copied onto the destination's first
        run before its text is rewritten. Useful when the destination
        cell was authored with incomplete or inconsistent formatting
        (e.g. the value column of the volume-with-table slides, which
        renders with a different font weight and no explicit size).
        """
        from copy import deepcopy

        tf = cell.text_frame
        # Use the first paragraph; clear remaining paragraphs.
        if not tf.paragraphs:
            tf.text = text
            return
        first_p = tf.paragraphs[0]
        # Preserve first run formatting; rewrite its text and clear others.
        runs = first_p.runs
        if runs:
            target_run = runs[0]
            if formatting_source is not None:
                src_rpr = DeckAssembler._first_run_rpr(formatting_source)
                if src_rpr is not None:
                    DeckAssembler._set_run_rpr(target_run, deepcopy(src_rpr))
            target_run.text = text
            for r in runs[1:]:
                r.text = ""
        else:
            first_p.text = text
            if formatting_source is not None and first_p.runs:
                src_rpr = DeckAssembler._first_run_rpr(formatting_source)
                if src_rpr is not None:
                    DeckAssembler._set_run_rpr(
                        first_p.runs[0], deepcopy(src_rpr)
                    )
        # Remove additional paragraphs (would otherwise stack below).
        for p in list(tf.paragraphs[1:]):
            p._p.getparent().remove(p._p)

    @staticmethod
    def _replace_chart_data_multi_series(
        shape: Shape,
        *,
        categories: list[str],
        series: list[dict[str, Any]],
    ) -> None:
        """Rewrite a multi-series chart's cached XML in place.

        Iterates the existing ``<c:ser>`` elements and updates each
        series' name (``c:tx``), categories (``c:cat``) and values
        (``c:val``) caches. The number of series in the chart XML is
        left unchanged: extra payload series are ignored, and unused
        existing series are zero-filled so the rendered chart matches
        the shape of ``series``.

        Falls back silently when the shape has no chart.
        """
        if not getattr(shape, "has_chart", False):
            return
        from lxml import etree  # local import; lxml ships with python-pptx

        ns = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
        c_ns = ns["c"]
        chart_xml = shape.chart._chartSpace  # type: ignore[attr-defined]
        sers = chart_xml.findall(".//c:ser", ns)
        if not sers:
            return

        def _qn_c(tag: str) -> str:
            return f"{{{c_ns}}}{tag}"

        n_cats = len(categories)

        for i, ser in enumerate(sers):
            payload = (
                series[i]
                if i < len(series)
                else {"name": "", "values": [0] * n_cats}
            )
            name = str(payload.get("name") or "")
            values = list(payload.get("values") or [])
            if len(values) < n_cats:
                values = values + [0] * (n_cats - len(values))
            else:
                values = values[:n_cats]

            # --- series name (c:tx) -----------------------------------
            tx = ser.find("c:tx", ns)
            if tx is not None:
                for child in list(tx):
                    tx.remove(child)
                str_ref = etree.SubElement(tx, _qn_c("strRef"))
                etree.SubElement(str_ref, _qn_c("f")).text = ""
                str_cache = etree.SubElement(str_ref, _qn_c("strCache"))
                etree.SubElement(str_cache, _qn_c("ptCount")).set("val", "1")
                pt = etree.SubElement(str_cache, _qn_c("pt"))
                pt.set("idx", "0")
                etree.SubElement(pt, _qn_c("v")).text = name

            # --- categories (c:cat) -----------------------------------
            cat = ser.find("c:cat", ns)
            if cat is not None:
                for child in list(cat):
                    cat.remove(child)
                str_ref = etree.SubElement(cat, _qn_c("strRef"))
                etree.SubElement(str_ref, _qn_c("f")).text = ""
                str_cache = etree.SubElement(str_ref, _qn_c("strCache"))
                etree.SubElement(str_cache, _qn_c("ptCount")).set(
                    "val", str(n_cats)
                )
                for idx, label in enumerate(categories):
                    pt = etree.SubElement(str_cache, _qn_c("pt"))
                    pt.set("idx", str(idx))
                    etree.SubElement(pt, _qn_c("v")).text = str(label)

            # --- values (c:val) ---------------------------------------
            val = ser.find("c:val", ns)
            if val is not None:
                for child in list(val):
                    val.remove(child)
                num_ref = etree.SubElement(val, _qn_c("numRef"))
                etree.SubElement(num_ref, _qn_c("f")).text = ""
                num_cache = etree.SubElement(num_ref, _qn_c("numCache"))
                etree.SubElement(num_cache, _qn_c("formatCode")).text = "General"
                etree.SubElement(num_cache, _qn_c("ptCount")).set(
                    "val", str(len(values))
                )
                for idx, v in enumerate(values):
                    pt = etree.SubElement(num_cache, _qn_c("pt"))
                    pt.set("idx", str(idx))
                    etree.SubElement(pt, _qn_c("v")).text = str(v)

    @staticmethod
    def _replace_chart_data(
        shape: Shape,
        categories: list[Any],
        values: list[Any],
        series_name: str = "Cases",
    ) -> None:
        """Replace a chart's data via python-pptx ``chart.replace_data``.

        ``None`` values are coerced to ``0`` so the chart renders without
        injecting a marker. Mismatched lengths are truncated to the
        shorter of the two inputs.

        Charts whose embedded workbook is referenced externally (target-
        mode "External") cannot be updated by python-pptx via
        ``replace_data`` because it requires writing into an internal
        xlsx package part. In that case we fall back to editing the
        chart XML's cached series values directly: PowerPoint displays
        those cached values when it cannot resolve the external
        workbook, so users see the refreshed numbers.
        """
        if not getattr(shape, "has_chart", False):
            return
        n = min(len(categories), len(values))
        cats = [str(c) for c in categories[:n]]
        vals = [0 if v is None else v for v in values[:n]]

        chart_data = CategoryChartData()
        chart_data.categories = cats
        chart_data.add_series(series_name, vals)
        replaced_via_pptx = False
        try:
            shape.chart.replace_data(chart_data)
            replaced_via_pptx = True
        except ValueError as exc:
            logger.warning(
                "deck_assembler.chart_replace_data_failed_falling_back",
                extra={
                    "shape_name": getattr(shape, "name", None),
                    "detail": str(exc),
                },
            )

        if not replaced_via_pptx:
            # XML-level fallback for charts with external workbook
            # relationships. Update each series' cached categories
            # (<c:strCache>) and values (<c:numCache>) so PowerPoint
            # renders the new data.
            try:
                DeckAssembler._update_chart_xml_cache(
                    shape.chart, categories=cats, values=vals
                )
            except Exception as exc:  # noqa: BLE001 - defensive
                logger.warning(
                    "deck_assembler.chart_xml_cache_update_failed",
                    extra={
                        "shape_name": getattr(shape, "name", None),
                        "error_type": type(exc).__name__,
                        "detail": str(exc),
                    },
                )

        # Always rewrite cached data-label text. Templates often pin
        # per-point labels with an ``a:fld`` CELLRANGE field whose
        # cached ``a:t`` text is what PowerPoint shows when the linked
        # workbook is offline (or just stale). ``replace_data`` does
        # not touch these, so percentages would otherwise stay frozen
        # at the template's authored values.
        try:
            DeckAssembler._rewrite_data_label_cache(shape.chart, values=vals)
        except Exception as exc:  # noqa: BLE001 - defensive
            logger.warning(
                "deck_assembler.chart_label_rewrite_failed",
                extra={
                    "shape_name": getattr(shape, "name", None),
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                },
            )

    @staticmethod
    def _update_chart_xml_cache(
        chart: Any,
        *,
        categories: list[str],
        values: list[Any],
    ) -> None:
        """Rewrite the cached series data in the chart's XML.

        Other chart attributes (titles, formatting, axes, data labels)
        are left untouched. Cached data-label text is rewritten by
        ``_rewrite_data_label_cache``, called separately by the caller.
        """
        from lxml import etree  # local import; lxml ships with python-pptx
        ns = {
            "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
        }
        c_ns = ns["c"]
        chart_xml = chart._chartSpace  # type: ignore[attr-defined]
        sers = chart_xml.findall(".//c:ser", ns)
        if not sers:
            return

        def _qn_c(tag: str) -> str:
            return f"{{{c_ns}}}{tag}"

        for ser in sers:
            # --- categories (c:cat) -----------------------------------
            cat = ser.find("c:cat", ns)
            if cat is not None:
                for child in list(cat):
                    cat.remove(child)
                str_ref = etree.SubElement(cat, _qn_c("strRef"))
                etree.SubElement(str_ref, _qn_c("f")).text = ""
                str_cache = etree.SubElement(str_ref, _qn_c("strCache"))
                etree.SubElement(str_cache, _qn_c("ptCount")).set(
                    "val", str(len(categories))
                )
                for idx, name in enumerate(categories):
                    pt = etree.SubElement(str_cache, _qn_c("pt"))
                    pt.set("idx", str(idx))
                    etree.SubElement(pt, _qn_c("v")).text = str(name)

            # --- values (c:val) ---------------------------------------
            val = ser.find("c:val", ns)
            if val is not None:
                for child in list(val):
                    val.remove(child)
                num_ref = etree.SubElement(val, _qn_c("numRef"))
                etree.SubElement(num_ref, _qn_c("f")).text = ""
                num_cache = etree.SubElement(num_ref, _qn_c("numCache"))
                etree.SubElement(num_cache, _qn_c("formatCode")).text = "General"
                etree.SubElement(num_cache, _qn_c("ptCount")).set(
                    "val", str(len(values))
                )
                for idx, v in enumerate(values):
                    pt = etree.SubElement(num_cache, _qn_c("pt"))
                    pt.set("idx", str(idx))
                    etree.SubElement(pt, _qn_c("v")).text = str(v)

    @staticmethod
    def _rewrite_data_label_cache(
        chart: Any,
        *,
        values: list[Any],
    ) -> None:
        """Replace cached per-point data-label text in the chart XML.

        Templates frequently pin per-point data labels with an
        ``a:fld`` of type ``CELLRANGE`` whose cached ``a:t`` text is
        what PowerPoint renders when the linked workbook is offline or
        the field cannot be re-evaluated. We replace that cached text
        with the per-point percentage of the new series total so the
        bar labels match the refreshed data.
        """
        from lxml import etree  # local import; lxml ships with python-pptx
        ns = {
            "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
            "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
            "c15": "http://schemas.microsoft.com/office/drawing/2012/chart",
        }
        chart_xml = chart._chartSpace  # type: ignore[attr-defined]
        sers = chart_xml.findall(".//c:ser", ns)
        if not sers:
            return

        numeric_values: list[float] = []
        for v in values:
            try:
                numeric_values.append(float(v))
            except (TypeError, ValueError):
                numeric_values.append(0.0)
        total = sum(numeric_values)
        if total > 0:
            label_texts = [
                f"{(v / total) * 100:.1f}%" for v in numeric_values
            ]
        else:
            label_texts = [str(int(v)) for v in numeric_values]

        for ser in sers:
            for d_lbl in ser.findall("c:dLbls/c:dLbl", ns):
                idx_el = d_lbl.find("c:idx", ns)
                if idx_el is None:
                    continue
                try:
                    idx = int(idx_el.get("val"))
                except (TypeError, ValueError):
                    continue
                if idx >= len(label_texts):
                    continue
                new_text = label_texts[idx]

                # Replace the rich text inside c:tx with a single plain
                # paragraph so PowerPoint renders our literal text.
                # Leaving the original ``a:fld`` (type="CELLRANGE") in
                # place causes PowerPoint to either re-evaluate the
                # field against the (now stale) workbook range and
                # display the template percentages, or render the
                # field's literal "[CELLRANGE]" placeholder when the
                # range is empty. Removing the field eliminates both.
                tx = d_lbl.find("c:tx", ns)
                if tx is None:
                    continue
                rich = tx.find("c:rich", ns)
                if rich is None:
                    continue
                # Drop every paragraph and rebuild a minimal one
                # containing a single plain text run.
                for p in rich.findall("a:p", ns):
                    rich.remove(p)
                p = etree.SubElement(rich, "{%s}p" % ns["a"])
                r = etree.SubElement(p, "{%s}r" % ns["a"])
                rpr = etree.SubElement(r, "{%s}rPr" % ns["a"])
                rpr.set("lang", "en-GB")
                t = etree.SubElement(r, "{%s}t" % ns["a"])
                t.text = new_text

            # The Office 2012 chart extension caches the rendered label
            # text in ``c15:dlblRangeCache`` and a formula reference in
            # ``c15:datalabelsRange``. PowerPoint prefers values it can
            # re-evaluate from the cell range over the per-point a:t
            # cache, so we must replace those entries too. Without this,
            # charts whose embedded workbook resolves successfully
            # render the original template percentages.
            for dlbl_range in ser.findall(
                "c:extLst/c:ext/c15:datalabelsRange", ns
            ):
                # Point the formula to a non-existent range so the cache
                # is what PowerPoint uses on next render.
                f_el = dlbl_range.find("c15:f", ns)
                if f_el is not None:
                    f_el.text = ""
                cache = dlbl_range.find("c15:dlblRangeCache", ns)
                if cache is None:
                    continue
                # Wipe and rebuild cache entries.
                for child in list(cache):
                    cache.remove(child)
                pt_count = etree.SubElement(
                    cache, "{%s}ptCount" % ns["c"]
                )
                pt_count.set("val", str(len(label_texts)))
                for i, txt in enumerate(label_texts):
                    pt = etree.SubElement(cache, "{%s}pt" % ns["c"])
                    pt.set("idx", str(i))
                    v_el = etree.SubElement(pt, "{%s}v" % ns["c"])
                    v_el.text = txt

    @classmethod
    def _apply_global_tokens(
        cls,
        prs: PresentationType,
        *,
        customer_name: str | None,
        period: str | None,
    ) -> None:
        """Substitute customer-name / period tokens across every slide.

        These two tokens appear in titles and subtitles on most of the
        slides in the HSBC CSR template. Centralising the substitution
        avoids forcing every per-slide populator to repeat the same
        token-table boilerplate, and ensures static slides (cover page,
        agenda, dividers) get correct headers even though they have no
        per-slide schema.
        """
        replacements: dict[str, str] = {}
        if customer_name:
            for tok in _CUSTOMER_NAME_TOKENS:
                replacements[tok] = customer_name
        if period:
            for tok in _PERIOD_TOKENS:
                replacements[tok] = period
        if not replacements:
            return
        for slide in prs.slides:
            cls._replace_text_tokens(slide, replacements)

    @classmethod
    def _replace_text_tokens(
        cls, slide: Slide, replacements: dict[str, str]
    ) -> None:
        """In-place token replacement across all text frames on ``slide``.

        Replacement happens at the paragraph level so tokens that span
        multiple runs (e.g. when PowerPoint split ``[MG / Legal Entity
        / Client / Account Name]`` across two runs because of a manual
        line break or an auto-correct edit during template authoring)
        are still substituted. The first run keeps the formatting and
        receives the rewritten paragraph text; the remaining runs are
        cleared so no duplicate or stray fragment is rendered.

        Top-level shape text frames, table-cell text frames, and group
        shape descendants are all visited so that placeholder tokens
        nested inside ``<p:grpSp>`` group shapes (used on the
        Summary / Next Steps slide) are still substituted.
        """
        if not replacements:
            return
        cls._replace_in_shape_iter(slide.shapes, replacements)

    @classmethod
    def _replace_in_shape_iter(
        cls, shapes: Any, replacements: dict[str, str]
    ) -> None:
        for shape in shapes:
            # Recurse into group shapes (PPTX MSO_SHAPE_TYPE.GROUP == 6).
            if getattr(shape, "shape_type", None) == 6 and hasattr(shape, "shapes"):
                cls._replace_in_shape_iter(shape.shapes, replacements)
                continue
            if getattr(shape, "has_text_frame", False):
                cls._replace_text_tokens_in_frame(shape.text_frame, replacements)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    for cell in row.cells:
                        cls._replace_text_tokens_in_frame(
                            cell.text_frame, replacements
                        )

    @staticmethod
    def _replace_text_tokens_in_frame(
        text_frame: Any, replacements: dict[str, str]
    ) -> None:
        """Apply ``replacements`` to every paragraph in ``text_frame``.

        Replacement is whitespace-aware: when the substituted value
        would land directly against an alphanumeric character on
        either side (e.g. the HSBC CSR template authors
        ``"Service Queries[MMM YYYY \u2013 MMM YYYY]"`` with no
        separating space), a single space is injected so the rendered
        output reads ``"Service Queries Oct 2024 ..."`` instead of
        ``"Service QueriesOct 2024 ..."``. A space is never inserted
        when the original token was already adjacent to whitespace.
        """
        for paragraph in text_frame.paragraphs:
            runs = list(paragraph.runs)
            if not runs:
                continue
            joined = "".join(r.text or "" for r in runs)
            new_joined = joined
            for token, value in replacements.items():
                if not token or token not in new_joined:
                    continue
                # Whitespace-aware replace: pad ``value`` with a
                # leading/trailing space when the token is adjacent
                # to an alphanumeric character on that side and the
                # replacement value itself doesn't already start /
                # end with whitespace.
                pieces: list[str] = []
                cursor = 0
                tlen = len(token)
                while True:
                    idx = new_joined.find(token, cursor)
                    if idx == -1:
                        pieces.append(new_joined[cursor:])
                        break
                    pieces.append(new_joined[cursor:idx])
                    prev_char = new_joined[idx - 1] if idx > 0 else ""
                    next_char = new_joined[idx + tlen] if idx + tlen < len(new_joined) else ""
                    pad_left = bool(
                        prev_char
                        and prev_char.isalnum()
                        and value
                        and not value[0].isspace()
                    )
                    pad_right = bool(
                        next_char
                        and next_char.isalnum()
                        and value
                        and not value[-1].isspace()
                    )
                    pieces.append(
                        ("" if not pad_left else " ")
                        + value
                        + ("" if not pad_right else " ")
                    )
                    cursor = idx + tlen
                new_joined = "".join(pieces)
            if new_joined == joined:
                continue
            runs[0].text = new_joined
            for extra in runs[1:]:
                extra.text = ""

    @classmethod
    def _find_commentary_textbox(
        cls, slide: Slide, avoid_tokens: tuple[str, ...] = ()
    ) -> Shape | None:
        """Find the first plausible commentary text box on ``slide``.

        Skips charts, tables, the exclusion note, and any shape still
        carrying a title token (so the title placeholder is never
        overwritten with commentary).
        """
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            if getattr(shape, "has_chart", False) or getattr(shape, "has_table", False):
                continue
            text = (shape.text_frame.text or "").strip()
            lowered = text.lower()
            if cls._SERVICE_QUERIES_EXCLUSION_HINT in lowered:
                continue
            if any(tok in text for tok in avoid_tokens):
                continue
            return shape
        return None

    @classmethod
    def _find_slide_by_text(
        cls, prs: PresentationType, slide_id: str
    ) -> _SlideHandle | None:
        """Locate a slide by required text fragments when name lookup fails."""
        fragments = SLIDE_ID_TEXT_FALLBACKS.get(slide_id)
        if not fragments:
            return None
        needles = [f.lower() for f in fragments]
        for slide in prs.slides:
            haystack_parts: list[str] = []
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False):
                    haystack_parts.append(shape.text_frame.text or "")
            haystack = "\n".join(haystack_parts).lower()
            if any(n in haystack for n in needles):
                return _SlideHandle(name=slide_id, slide=slide)
        return None

    # -------------------------------------------------- chart-image embed

    def embed_chart_image(
        self,
        slide_id: str,
        slide: Slide,
        placeholder_name: str,
        asset_path: str,
    ) -> None:
        """Embed a pre-rendered chart image at the named placeholder.

        Phase 1 disallows native PowerPoint charts: callers fetch a
        pre-rendered image via :class:`TemplateStore` and pass the asset
        path. The method is exposed so the orchestrator (or a slide-
        specific extension) can opt-in per slide.
        """
        if self._template_store is None:
            raise RuntimeError(
                "embed_chart_image requires a TemplateStore instance"
            )
        target = self._find_shape(slide, placeholder_name)
        if target is None:
            logger.warning(
                "deck_assembler.chart_placeholder_missing",
                extra={"slide_id": slide_id, "placeholder": placeholder_name},
            )
            return

        image_bytes = self._template_store.get_asset_bytes(asset_path)
        left = Emu(target.left or 0)
        top = Emu(target.top or 0)
        width = Emu(target.width or 0) or None
        height = Emu(target.height or 0) or None
        # Remove the placeholder shape and replace with the image.
        sp = target._element  # noqa: SLF001 - python-pptx convention
        sp.getparent().remove(sp)
        slide.shapes.add_picture(
            io.BytesIO(image_bytes), left, top, width=width, height=height
        )

    # --------------------------------------------------------- placeholders

    @staticmethod
    def _index_slides(prs: PresentationType) -> dict[str, _SlideHandle]:
        """Index slides by their authored shape-tree name (PPTX `name`)."""
        index: dict[str, _SlideHandle] = {}
        for slide in prs.slides:
            # python-pptx exposes the shape-tree (cSld) name via the
            # underlying XML; the brand-reviewed deck authors set this
            # name as the slide identifier.
            name = ""
            try:
                cSld = slide.element.find(
                    "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}cSld"
                )
                if cSld is None:
                    cSld = slide.element.find(
                        "{http://schemas.openxmlformats.org/presentationml/2006/main}cSld"
                    )
                if cSld is not None:
                    name = cSld.get("name", "") or ""
            except Exception:  # noqa: BLE001 - best-effort
                name = ""
            if not name:
                # Fall back to the layout name; sufficient for Phase 1
                # if slide names are not authored explicitly.
                layout = slide.slide_layout
                name = getattr(layout, "name", "") or ""
            if name:
                index.setdefault(name, _SlideHandle(name=name, slide=slide))
        return index

    @staticmethod
    def _iter_named_shapes(slide: Slide) -> Iterable[Shape]:
        for shape in slide.shapes:
            yield shape

    @classmethod
    def _find_shape(cls, slide: Slide, name: str) -> Shape | None:
        for shape in cls._iter_named_shapes(slide):
            if getattr(shape, "name", "") == name:
                return shape
        return None

    @classmethod
    def _find_table(cls, slide: Slide, name: str) -> Table | None:
        shape = cls._find_shape(slide, name)
        if shape is None or not getattr(shape, "has_table", False):
            return None
        return shape.table

    # ------------------------------------------------------------- writers

    @classmethod
    def _set_named_text(
        cls,
        slide: Slide,
        placeholder_name: str,
        value: Any,
        data_gaps: set[str],
        *,
        field: str,
        numeric: bool = False,
    ) -> None:
        shape = cls._find_shape(slide, placeholder_name)
        if shape is None or not getattr(shape, "has_text_frame", False):
            return

        is_gap = field in data_gaps or value is None
        if is_gap:
            # Numerical placeholders MUST stay blank — never inject a marker.
            if numeric:
                cls._write_text(shape, "")
                return
            # For non-commentary text fields (title, subtitle, regions
            # block, etc.) leave blank as well; the commentary block is
            # the only place the marker is permitted.
            cls._write_text(shape, "")
            return

        cls._write_text(shape, str(value))

    @classmethod
    def _set_commentary(
        cls,
        slide: Slide,
        bullets: list[str],
        data_gaps: set[str],
    ) -> None:
        shape = cls._find_shape(slide, "commentary")
        if shape is None or not getattr(shape, "has_text_frame", False):
            return

        rendered: list[str] = [str(b) for b in bullets if b]
        if not rendered and "commentary" in data_gaps:
            rendered = [DATA_NOT_AVAILABLE_MARKER]
        cls._write_bullets(shape, rendered)

    @classmethod
    def _set_named_bullets(
        cls, slide: Slide, placeholder_name: str, bullets: list[Any]
    ) -> None:
        shape = cls._find_shape(slide, placeholder_name)
        if shape is None or not getattr(shape, "has_text_frame", False):
            return
        cls._write_bullets(shape, [str(b) for b in bullets if b])

    @staticmethod
    def _write_text(shape: Shape, value: str) -> None:
        tf = shape.text_frame
        tf.clear()
        tf.paragraphs[0].text = value

    @staticmethod
    def _write_bullets(shape: Shape, bullets: list[str]) -> None:
        tf = shape.text_frame
        tf.clear()
        if not bullets:
            return
        tf.paragraphs[0].text = bullets[0]
        for line in bullets[1:]:
            p = tf.add_paragraph()
            p.text = line

    # ------------------------------------------------------------- tables

    @classmethod
    def _fill_table(cls, table: Table, rows: list[list[Any]]) -> None:
        """Fill ``table`` cells row-by-row, leaving header row 0 alone.

        Cells beyond the available pre-authored rows are ignored. ``None``
        values render as empty strings — never as a marker (numerical-
        adjacent cells must stay blank when grounded data is missing).
        """
        body_rows = list(table.rows)[1:]
        for row_idx, values in enumerate(rows):
            if row_idx >= len(body_rows):
                break
            cells = list(body_rows[row_idx].cells)
            for col_idx, raw in enumerate(values):
                if col_idx >= len(cells):
                    break
                cls._write_cell(cells[col_idx], raw)

    @staticmethod
    def _write_cell(cell: _Cell, value: Any) -> None:
        text = "" if value is None else str(value)
        tf = cell.text_frame
        tf.clear()
        tf.paragraphs[0].text = text


__all__ = [
    "DeckAssembler",
    "DATA_NOT_AVAILABLE_MARKER",
    "SLIDE_ID_TO_TEMPLATE_NAME",
    "SLIDE_ID_TEXT_FALLBACKS",
]
