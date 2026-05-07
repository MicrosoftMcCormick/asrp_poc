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

from asrp_functions.shared.logging import get_logger
from asrp_functions.shared.template_store import TemplateStore

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

# Fallback text fragments used to locate a slide when the authored
# template name is missing. All listed fragments must be present (case-
# insensitive) on the candidate slide.
SLIDE_ID_TEXT_FALLBACKS: dict[str, tuple[str, ...]] = {
    "service_queries": (
        "Global Volumes by Case Type",
        "Global Volumes by Country",
    ),
}


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
    ) -> tuple[bytes, list[str]]:
        """Render ``slides`` onto the template and return ``(bytes, failed)``."""
        if not template_bytes:
            raise ValueError("template_bytes must be non-empty")

        prs = Presentation(io.BytesIO(template_bytes))
        slide_index = self._index_slides(prs)
        failed_slide_ids: list[str] = []

        for desc in slides:
            slide_id = str(desc.get("slide_id") or "")
            if not slide_id:
                logger.error("deck_assembler.missing_slide_id")
                failed_slide_ids.append("<unknown>")
                continue

            template_name = SLIDE_ID_TO_TEMPLATE_NAME.get(slide_id)
            if template_name is None:
                logger.error(
                    "deck_assembler.unmapped_slide_id",
                    extra={"slide_id": slide_id},
                )
                failed_slide_ids.append(slide_id)
                continue

            handle = slide_index.get(template_name)

            if handle is None and slide_id == "service_queries":
                # FORCE slide 21 (index 20)
                handle = _SlideHandle(
                    name="forced_service_queries",
                    slide=prs.slides[20]
                )

            if handle is None:
                handle = self._find_slide_by_text(prs, slide_id)
                if handle is None:
                    logger.error(
                        "deck_assembler.template_slide_missing",
                        extra={"slide_id": slide_id, "template_name": template_name},
                    )
                    failed_slide_ids.append(slide_id)
                    continue
                logger.info(
                    "deck_assembler.slide_resolved_by_text",
                    extra={"slide_id": slide_id, "template_name": template_name},
                )

            try:
                self._populate_slide(handle.slide, slide_id, desc)
            except Exception as exc:  # noqa: BLE001 - per-slide isolation
                logger.error(
                    "deck_assembler.slide_failed",
                    extra={
                        "slide_id": slide_id,
                        "template_name": template_name,
                        "error_type": type(exc).__name__,
                    },
                )
                failed_slide_ids.append(slide_id)
                continue

        buf = io.BytesIO()
        prs.save(buf)
        return buf.getvalue(), failed_slide_ids

    # ------------------------------------------------------------- per-slide

    def _populate_slide(
        self, slide: Slide, slide_id: str, desc: dict[str, Any]
    ) -> None:
        data_gaps = set(desc.get("data_gaps") or [])

        # Common envelope.
        self._set_named_text(slide, "title", desc.get("title"), data_gaps, field="title")
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

        # 3) Commentary: only write into an obvious placeholder,
        # never into headings like "Global Volumes by Case Type".
        if commentary and "commentary" not in data_gaps:
            target = self._find_commentary_textbox(
                slide,
                avoid_tokens=(
                    *self._SERVICE_QUERIES_CUSTOMER_TOKENS,
                    *self._SERVICE_QUERIES_PERIOD_TOKENS,
                ),
            )

            if target is not None:
                self._write_text(target, str(commentary))
            else:
                logger.warning(
                    "deck_assembler.service_queries_no_commentary_target",
                    extra={"slide_id": "service_queries"},
                )

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
        """
        if not getattr(shape, "has_chart", False):
            return
        n = min(len(categories), len(values))
        chart_data = CategoryChartData()
        chart_data.categories = [str(c) for c in categories[:n]]
        chart_data.add_series(
            series_name,
            [0 if v is None else v for v in values[:n]],
        )
        shape.chart.replace_data(chart_data)

    @classmethod
    def _replace_text_tokens(
        cls, slide: Slide, replacements: dict[str, str]
    ) -> None:
        """In-place token replacement across all text frames on ``slide``."""
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    text = run.text or ""
                    new_text = text
                    for token, value in replacements.items():
                        if token in new_text:
                            new_text = new_text.replace(token, value)
                    if new_text != text:
                        run.text = new_text

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
