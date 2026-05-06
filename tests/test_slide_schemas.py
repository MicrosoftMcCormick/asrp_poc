"""Tests for :mod:`shared.slide_schemas`."""

from __future__ import annotations

from shared.slide_schemas import PaymentsStpV1, validate


def test_validate_happy_path_payments_stp() -> None:
    payload = {
        "title": "Payments STP",
        "subtitle": "Q1 2026",
        "commentary": ["STP rate held above 95% all quarter."],
        "data_gaps": [],
        "model_metadata": {"model": "gpt-4o-mini", "prompt_version": "v1"},
        "period": {"from": "2026-01-01", "to": "2026-03-31"},
        "volumes": {"stp_count": 9500, "non_stp_count": 500, "total_count": 10000},
        "rates": {"stp_rate_pct": 95.0, "non_stp_rate_pct": 5.0},
        "top_reasons_non_stp": [
            {"reason": "Missing BIC", "count": 200, "pct": 40.0}
        ],
    }

    result = validate("payments_stp_v1", payload)

    assert result.is_valid is True
    assert result.errors == []
    assert isinstance(result.model, PaymentsStpV1)
    assert result.model.slide_id == "payments_stp_v1"
    assert result.model.volumes is not None
    assert result.model.volumes.total_count == 10000


def test_validate_missing_fields_marked_in_data_gaps() -> None:
    """Null grounded fields validate; the envelope's data_gaps lists them."""
    payload = {
        "title": "Payments STP",
        "subtitle": None,
        "commentary": [],
        "data_gaps": ["volumes.stp_count", "rates.stp_rate_pct"],
        "model_metadata": {},
        "period": {"from": "2026-01-01", "to": "2026-03-31"},
        "volumes": {
            "stp_count": None,
            "non_stp_count": 500,
            "total_count": None,
        },
        "rates": {"stp_rate_pct": None, "non_stp_rate_pct": 5.0},
        "top_reasons_non_stp": [],
    }

    result = validate("payments_stp_v1", payload)

    assert result.is_valid is True, result.errors
    assert result.model is not None
    # The schema permits null grounded values; data_gaps records them.
    assert "volumes.stp_count" in result.model.data_gaps
    assert "rates.stp_rate_pct" in result.model.data_gaps
    assert result.model.volumes is not None
    assert result.model.volumes.stp_count is None
    assert result.model.rates is not None
    assert result.model.rates.stp_rate_pct is None


def test_validate_unknown_slide_id_returns_structured_error() -> None:
    result = validate("not_a_real_slide_v1", {"title": "x"})
    assert result.is_valid is False
    assert result.model is None
    assert any(
        e.get("type") == "unknown_slide_id" for e in result.errors
    )


def test_validate_rejects_extra_fields() -> None:
    payload = {
        "title": "Footprint",
        "commentary": [],
        "data_gaps": [],
        "model_metadata": {},
        "countries": [],
        "regions": [],
        "summary": [],
        "rogue_field": "should be rejected",
    }
    result = validate("footprint_v1", payload)
    assert result.is_valid is False
    assert result.errors
