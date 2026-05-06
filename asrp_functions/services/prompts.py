"""Static per-slide prompts.

In Phase 1 the orchestrator selects a slide and the Function looks up the
prompt here. Each prompt instructs the model to:

* Use *only* the provided grounded context.
* Return a JSON object matching the slide's expected shape.
* Set fields to ``null`` when grounding is missing and append the field
  name to ``data_gaps`` — never fabricate data.
"""

from __future__ import annotations

# NOTE: Slide-specific JSON shapes are validated downstream against
# per-slide schemas (out of scope for this scaffold).
SLIDE_PROMPTS: dict[str, str] = {
    "exec_summary": (
        "You are generating the 'Executive Summary' slide of the HSBC "
        "Customer Service Review (CSR). Use ONLY the grounded context. "
        "Return JSON with fields: headline (string|null), "
        "key_points (array of strings, may be empty), "
        "period (string|null). For any field you cannot ground, return "
        "null and add the field name to a top-level `data_gaps` array."
    ),
    "volumes_trend": (
        "You are generating the 'Transaction Volumes Trend' slide. Use "
        "ONLY the grounded context. Return JSON with fields: "
        "period (string|null), total_volume (number|null), "
        "yoy_change_pct (number|null), commentary (string|null). "
        "Return null and append to `data_gaps` for any ungrounded field."
    ),
    "incidents": (
        "You are generating the 'Incidents & Issues' slide. Use ONLY "
        "the grounded context. Return JSON with fields: "
        "incident_count (integer|null), top_incidents (array, may be "
        "empty), commentary (string|null). Return null and append to "
        "`data_gaps` for any ungrounded field."
    ),
}


def get_slide_prompt(slide_id: str) -> str:
    """Return the static prompt for ``slide_id``.

    Raises:
        KeyError: when the slide id is not registered.
    """
    if slide_id not in SLIDE_PROMPTS:
        raise KeyError(f"Unknown slide_id: {slide_id!r}")
    return SLIDE_PROMPTS[slide_id]
