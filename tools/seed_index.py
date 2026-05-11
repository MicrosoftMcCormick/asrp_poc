"""Seed the Azure AI Search index with one grounding document per concrete
data-driven slide_id for a single demo customer (cust-alpine-industries).

Each document carries a natural-language ``content`` blob that the LLM can
ground against (it lists every fact the slide needs: months, totals,
country breakdowns, series values, top counterparties, etc.) plus a
``metadata`` JSON string that echoes the same numbers in structured form
for traceability.

Numbers are randomised but deterministic (fixed seed) and shaped to match
each slide's family schema.

Usage::

    # Defaults: customer cust-alpine-industries, period Oct 2024 - Sep 2025
    python -m tools.seed_index

    # Override
    SEARCH_ENDPOINT=https://srch-asrp-poc-sm01.search.windows.net \\
    SEARCH_INDEX=asrp-customer-context-v1 \\
    python -m tools.seed_index --customer-id cust-alpine-industries

Authentication uses ``DefaultAzureCredential``. The signed-in principal
needs the "Search Index Data Contributor" role on the target index.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from dataclasses import dataclass
from typing import Any, Callable

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient


logger = logging.getLogger("seed_index")


# --------------------------------------------------------------- defaults

DEFAULT_ENDPOINT = "https://srch-asrp-poc-sm01.search.windows.net"
DEFAULT_INDEX = "asrp-customer-context-v1"
DEFAULT_CUSTOMER_ID = "cust-alpine-industries"
DEFAULT_CUSTOMER_NAME = "Alpine Industries Ltd"
DEFAULT_PERIOD = "Oct 2024 - Sep 2025"

MONTHS: list[str] = [
    "Oct'24", "Nov'24", "Dec'24", "Jan'25", "Feb'25", "Mar'25",
    "Apr'25", "May'25", "Jun'25", "Jul'25", "Aug'25", "Sep'25",
]

# Country pools, biased so totals look realistic (top markets first).
COUNTRY_POOL: list[str] = [
    "India", "Singapore", "Netherlands", "Hong Kong SAR", "Malaysia",
    "Philippines", "Australia", "Japan", "United Kingdom", "South Africa",
    "Czech Republic", "New Zealand", "Israel", "Indonesia", "South Korea",
    "Germany", "France", "United Arab Emirates", "Canada", "United States",
]
COUNTRY_CODE_POOL: list[str] = [
    "IN", "SG", "NL", "HK", "MY", "PH", "AU", "JP", "GB", "ZA",
    "CZ", "NZ", "IL", "ID", "KR", "DE", "FR", "AE", "CA", "US",
]


# --------------------------------------------------------------- helpers


def _vals(rng: random.Random, low: int, high: int, n: int = 12) -> list[int]:
    return [rng.randint(low, high) for _ in range(n)]


def _floats(rng: random.Random, low: float, high: float, n: int = 12) -> list[float]:
    return [round(rng.uniform(low, high), 1) for _ in range(n)]


def _ranked_country_counts(
    rng: random.Random, n: int, low: int, high: int
) -> list[tuple[str, int]]:
    """Pick ``n`` countries and return (country, count) ranked desc."""
    chosen = rng.sample(COUNTRY_POOL, k=min(n, len(COUNTRY_POOL)))
    counts = sorted((rng.randint(low, high) for _ in chosen), reverse=True)
    return list(zip(chosen, counts))


def _ranked_code_counts(
    rng: random.Random, n: int, low: int, high: int
) -> list[tuple[str, int]]:
    chosen = rng.sample(COUNTRY_CODE_POOL, k=min(n, len(COUNTRY_CODE_POOL)))
    counts = sorted((rng.randint(low, high) for _ in chosen), reverse=True)
    return list(zip(chosen, counts))


def _fmt_pairs(pairs: list[tuple[str, int]]) -> str:
    return ", ".join(f"{name} {count}" for name, count in pairs)


def _series_lines(name: str, values: list[int | float]) -> str:
    parts = ", ".join(f"{m} {v}" for m, v in zip(MONTHS, values))
    return f"Series '{name}' monthly values: {parts}."


# --------------------------------------------------------------- registry


@dataclass(frozen=True)
class SeedSpec:
    slide_id: str
    title: str
    builder: Callable[[random.Random, str, str, str], tuple[str, dict[str, Any]]]


def build_service_queries(
    rng: random.Random, customer_name: str, period: str, slide_id: str
) -> tuple[str, dict[str, Any]]:
    case_types = [
        ("Payment Queries", rng.randint(600, 900)),
        ("Account Management and Maintenance", rng.randint(200, 350)),
        ("Channel", rng.randint(180, 280)),
        ("Account Information", rng.randint(150, 230)),
        ("Cash and Cheques", rng.randint(100, 180)),
        ("Collection Services and Receivables", rng.randint(70, 130)),
        ("Liquidity Management", rng.randint(60, 120)),
        ("Card Services", rng.randint(40, 90)),
        ("Client Admin", rng.randint(15, 50)),
        ("Production Support", rng.randint(10, 30)),
        ("Implementation Team", rng.randint(2, 12)),
        ("Incident Management", rng.randint(1, 8)),
    ]
    case_types.sort(key=lambda x: x[1], reverse=True)
    countries = _ranked_country_counts(rng, 15, 20, 350)
    total = sum(c for _, c in case_types)
    excluded = ["China", "Egypt", "Turkey", "Saudi", "HASE"]

    content = (
        f"Customer {customer_name}. Service Queries overview for {period}. "
        f"Total cases {total}. "
        f"Largest case type is {case_types[0][0]} with {case_types[0][1]} cases, "
        f"followed by " + ", ".join(f"{n} with {c}" for n, c in case_types[1:]) + ". "
        f"Highest-volume countries are {_fmt_pairs(countries)}. "
        f"{case_types[0][0]} dominate the service profile and are concentrated in "
        f"{countries[0][0]} and {countries[1][0]}. "
        f"Excluded countries note applies: {', '.join(excluded)} are not included."
    )
    metadata = {
        "period": period,
        "top_case_type": case_types[0][0],
        "top_country": countries[0][0],
        "key_observation": (
            f"{case_types[0][0]} and {case_types[1][0]} make up most of the demand, "
            f"driven by {countries[0][0]} and {countries[1][0]}."
        ),
        "excluded_countries": excluded,
    }
    return content, metadata


def _make_volume_by_country(
    title: str, low: int, high: int, n_countries: int = 10
) -> Callable[[random.Random, str, str, str], tuple[str, dict[str, Any]]]:
    def _build(
        rng: random.Random, customer_name: str, period: str, slide_id: str
    ) -> tuple[str, dict[str, Any]]:
        rows = _ranked_country_counts(rng, n_countries, low, high)
        total = sum(c for _, c in rows)
        content = (
            f"Customer {customer_name}. Slide {slide_id}: {title}. "
            f"Period {period}. Total volume {total}. "
            f"By country, ranked descending: {_fmt_pairs(rows)}. "
            f"{rows[0][0]} leads with {rows[0][1]}, followed by {rows[1][0]} "
            f"({rows[1][1]}) and {rows[2][0]} ({rows[2][1]})."
        )
        metadata = {
            "period": period,
            "total": total,
            "by_country": [{"country": n, "count": c} for n, c in rows],
            "title_hint": title,
        }
        return content, metadata
    return _build


def _make_volume_by_type(
    title: str, series_names: list[str], low: int, high: int
) -> Callable[[random.Random, str, str, str], tuple[str, dict[str, Any]]]:
    def _build(
        rng: random.Random, customer_name: str, period: str, slide_id: str
    ) -> tuple[str, dict[str, Any]]:
        series = [(name, _vals(rng, low, high)) for name in series_names]
        total = sum(sum(v) for _, v in series)
        lines = " ".join(_series_lines(n, v) for n, v in series)
        content = (
            f"Customer {customer_name}. Slide {slide_id}: {title}. "
            f"Period {period}. Categories (months) in chronological order: "
            f"{', '.join(MONTHS)}. "
            f"Series in template order: {', '.join(series_names)}. "
            f"Total across all series and months: {total}. "
            f"{lines} "
            f"Volumes remained broadly stable across the period."
        )
        metadata = {
            "period": period,
            "total": total,
            "categories": MONTHS,
            "series": [{"name": n, "values": v} for n, v in series],
            "title_hint": title,
        }
        return content, metadata
    return _build


def _make_channel_mix(
    title: str, channels: list[str], low: int, high: int
) -> Callable[[random.Random, str, str, str], tuple[str, dict[str, Any]]]:
    def _build(
        rng: random.Random, customer_name: str, period: str, slide_id: str
    ) -> tuple[str, dict[str, Any]]:
        channel_data: list[tuple[str, list[tuple[str, int]]]] = []
        grand_total = 0
        for ch in channels:
            rows = _ranked_code_counts(rng, 6, low, high)
            grand_total += sum(c for _, c in rows)
            channel_data.append((ch, rows))
        parts = [
            f"Channel '{name}' by country: {_fmt_pairs(rows)}"
            for name, rows in channel_data
        ]
        content = (
            f"Customer {customer_name}. Slide {slide_id}: {title}. "
            f"Period {period}. Channel order (left-to-right): {', '.join(channels)}. "
            f"Grand total across all channels and countries: {grand_total}. "
            + ". ".join(parts) + ". "
            f"{channel_data[0][0]} remains the dominant channel."
        )
        metadata = {
            "period": period,
            "total": grand_total,
            "channels": [
                {
                    "name": name,
                    "by_country": [{"country": c, "count": v} for c, v in rows],
                }
                for name, rows in channel_data
            ],
            "title_hint": title,
        }
        return content, metadata
    return _build


def _make_volume_with_table(
    title: str,
    currencies: list[str],
    chart_low: int,
    chart_high: int,
    counterparty_prefix: str,
    row_low: int,
    row_high: int,
    value_format: str,
) -> Callable[[random.Random, str, str, str], tuple[str, dict[str, Any]]]:
    def _build(
        rng: random.Random, customer_name: str, period: str, slide_id: str
    ) -> tuple[str, dict[str, Any]]:
        series = [(c, _vals(rng, chart_low, chart_high)) for c in currencies]
        total = sum(sum(v) for _, v in series)
        locations = ["US", "UK", "HK", "FR", "SG"]
        rows = []
        for i, loc in enumerate(locations, start=1):
            v = rng.randint(row_low, row_high)
            rows.append((f"{counterparty_prefix} {i} Ltd", loc, value_format.format(v)))
        lines = " ".join(_series_lines(n, v) for n, v in series)
        rows_str = "; ".join(f"{n} ({l}) {v}" for n, l, v in rows)
        content = (
            f"Customer {customer_name}. Slide {slide_id}: {title}. "
            f"Period {period}. Categories (months): {', '.join(MONTHS)}. "
            f"Currency series in template order: {', '.join(currencies)}. "
            f"Total: {total}. {lines} "
            f"Top 5 counterparties (Name, Location, Value): {rows_str}. "
            f"USD and EUR dominate with a Q1 seasonal peak."
        )
        metadata = {
            "period": period,
            "total": total,
            "chart_categories": MONTHS,
            "chart_series": [{"name": n, "values": v} for n, v in series],
            "table_rows": [{"name": n, "location": l, "value": v} for n, l, v in rows],
            "title_hint": title,
        }
        return content, metadata
    return _build


def _make_case_subtype(
    title: str,
    subtypes: list[str],
    low: int,
    high: int,
    drivers_by_subtype: dict[str, list[str]] | None = None,
) -> Callable[[random.Random, str, str, str], tuple[str, dict[str, Any]]]:
    drivers_by_subtype = drivers_by_subtype or {}

    def _build(
        rng: random.Random, customer_name: str, period: str, slide_id: str
    ) -> tuple[str, dict[str, Any]]:
        rows = sorted(
            ((s, rng.randint(low, high)) for s in subtypes),
            key=lambda x: x[1],
            reverse=True,
        )
        total = sum(c for _, c in rows)
        # Per-bucket top drivers (one or two), drawn from the curated
        # pool when available.
        drivers_per_row: list[list[str]] = []
        for name, _count in rows:
            pool = drivers_by_subtype.get(name) or []
            if not pool:
                drivers_per_row.append([])
                continue
            k = 2 if len(pool) >= 2 and rng.random() < 0.6 else 1
            drivers_per_row.append(pool[:k])
        drivers_text = "; ".join(
            f"{name}: " + (", ".join(drv) if drv else "n/a")
            for (name, _), drv in zip(rows, drivers_per_row)
        )
        # Commentary modelled on the customer's own example: lead
        # statement plus quantified detail and runner-up context.
        top1_name, top1_count = rows[0]
        top1_pct = round(top1_count / total * 100, 1)
        top2_name, top2_count = rows[1] if len(rows) > 1 else (None, 0)
        commentary_sentences = [
            f"{top1_name} remains the dominant sub-type, accounting for "
            f"{top1_pct} per cent of total cases ({top1_count:,} of {total:,}).",
        ]
        if top2_name is not None:
            top2_pct = round(top2_count / total * 100, 1)
            commentary_sentences.append(
                f"{top2_name} follows at {top2_pct} per cent "
                f"({top2_count:,} cases), and together the top two sub-types "
                f"represent {round(top1_pct + top2_pct, 1)} per cent of activity."
            )
        if drivers_per_row[0]:
            commentary_sentences.append(
                f"Within {top1_name}, the leading driver is "
                f"{drivers_per_row[0][0]}."
            )
        commentary = " ".join(commentary_sentences)
        content = (
            f"Customer {customer_name}. Slide {slide_id}: {title}. "
            f"Period {period}. Total cases {total}. "
            f"By sub-type, ranked descending: {_fmt_pairs(rows)}. "
            f"Top drivers per sub-type: {drivers_text}. "
            f"{commentary}"
        )
        metadata = {
            "period": period,
            "total": total,
            "by_subtype": [
                {"name": n, "count": c, "top_drivers": drv}
                for (n, c), drv in zip(rows, drivers_per_row)
            ],
            "commentary": commentary,
            "title_hint": title,
        }
        return content, metadata
    return _build


def _make_multi_chart_trend(
    title: str,
    charts: list[tuple[list[str], list[tuple[int, int]]]],  # [(series_names, [(low, high), ...])]
    commentary: str,
) -> Callable[[random.Random, str, str, str], tuple[str, dict[str, Any]]]:
    def _build(
        rng: random.Random, customer_name: str, period: str, slide_id: str
    ) -> tuple[str, dict[str, Any]]:
        chart_payloads: list[dict[str, Any]] = []
        chart_lines: list[str] = []
        for chart_idx, (names, ranges) in enumerate(charts, start=1):
            series = []
            for name, (low, high) in zip(names, ranges):
                series.append((name, _vals(rng, low, high)))
            chart_payloads.append(
                {
                    "categories": MONTHS,
                    "series": [{"name": n, "values": v} for n, v in series],
                }
            )
            chart_lines.append(
                f"Chart {chart_idx} series in template order: {', '.join(names)}. "
                + " ".join(_series_lines(n, v) for n, v in series)
            )
        content = (
            f"Customer {customer_name}. Slide {slide_id}: {title}. "
            f"Period {period}. Categories (months): {', '.join(MONTHS)}. "
            f"The slide contains {len(charts)} chart(s). "
            + " ".join(chart_lines)
            + f" {commentary}"
        )
        metadata = {
            "period": period,
            "charts": chart_payloads,
            "title_hint": title,
        }
        return content, metadata
    return _build


# Ordered registry. One entry per concrete data-driven slide_id.
SEEDS: list[SeedSpec] = [
    SeedSpec("service_queries", "Service Queries", build_service_queries),

    # volume_by_country family
    SeedSpec(
        "volume_by_country_s29", "Service Cases by Country",
        _make_volume_by_country("Service Cases by Country", 20, 350),
    ),
    SeedSpec(
        "volume_by_country_s43", "Cheque Volume by Country",
        _make_volume_by_country("Cheque Volume by Country", 10, 180),
    ),
    SeedSpec(
        "volume_by_country_s49", "Channel Volume by Country",
        _make_volume_by_country("Channel Volume by Country", 50, 900),
    ),

    # volume_by_type family (matches test_type5.ps1 series names)
    SeedSpec(
        "volume_by_type_s28", "Service Cases by Type",
        _make_volume_by_type(
            "Service Cases by Type",
            ["Investigation", "Amendment", "Compliance", "Other"],
            20, 200,
        ),
    ),
    SeedSpec(
        "volume_by_type_s31", "Investigation Sub-types",
        _make_volume_by_type(
            "Investigation Sub-types",
            ["Status / Trace", "NSF", "Additional Details", "Compliance / OFAC"],
            20, 200,
        ),
    ),
    SeedSpec(
        "volume_by_type_s32", "Amendment Volume",
        _make_volume_by_type(
            "Amendment Volume",
            ["Beneficiary", "Reference"],
            20, 200,
        ),
    ),
    SeedSpec(
        "volume_by_type_s34", "Compliance Volume",
        _make_volume_by_type(
            "Compliance Volume",
            ["RFI", "Sanctions"],
            20, 200,
        ),
    ),
    SeedSpec(
        "volume_by_type_s42", "Cheque Detail",
        _make_volume_by_type(
            "Cheque Detail",
            ["Issued", "Returned"],
            10, 150,
        ),
    ),

    # channel_mix family
    SeedSpec(
        "channel_mix_s46", "Payment Channel Mix",
        _make_channel_mix(
            "Payment Channel Mix",
            ["SWIFT", "FLU", "H2H", "HSBCnet", "On Screen", "API"],
            100, 5000,
        ),
    ),
    SeedSpec(
        "channel_mix_s47", "Receipt Channel Mix",
        _make_channel_mix(
            "Receipt Channel Mix",
            ["SWIFT", "FLU", "H2H", "HSBCnet", "On Screen", "API"],
            100, 5000,
        ),
    ),
    SeedSpec(
        "channel_mix_s48", "Statement Channel Mix",
        _make_channel_mix(
            "Statement Channel Mix",
            ["SWIFT", "FLU", "H2H", "HSBCnet", "On Screen", "API"],
            100, 5000,
        ),
    ),

    # volume_with_table family (matches test_table8.ps1 layout)
    SeedSpec(
        "volume_with_table_s33", "Priority Payments - Volume",
        _make_volume_with_table(
            "Priority Payments - Volume", ["USD", "EUR", "GBP"],
            1000, 80000, "Beneficiary", 200, 50000, "{:,}",
        ),
    ),
    SeedSpec(
        "volume_with_table_s35", "Priority Payments - Value",
        _make_volume_with_table(
            "Priority Payments - Value", ["USD", "EUR", "GBP"],
            1000, 80000, "Remitter", 200, 50000, "USD {:,}",
        ),
    ),
    SeedSpec(
        "volume_with_table_s36", "ACH - Volume",
        _make_volume_with_table(
            "ACH - Volume", ["USD", "EUR", "GBP"],
            1000, 80000, "Beneficiary", 200, 50000, "{:,}",
        ),
    ),
    SeedSpec(
        "volume_with_table_s37", "ACH - Value",
        _make_volume_with_table(
            "ACH - Value", ["USD", "EUR", "GBP"],
            1000, 80000, "Remitter", 200, 50000, "USD {:,}",
        ),
    ),
    SeedSpec(
        "volume_with_table_s38", "Direct Debits - Volume",
        _make_volume_with_table(
            "Direct Debits - Volume", ["USD", "EUR", "GBP"],
            1000, 80000, "Beneficiary", 200, 50000, "{:,}",
        ),
    ),
    SeedSpec(
        "volume_with_table_s39", "Direct Debits - Value",
        _make_volume_with_table(
            "Direct Debits - Value", ["USD", "EUR", "GBP"],
            1000, 80000, "Remitter", 200, 50000, "USD {:,}",
        ),
    ),
    SeedSpec(
        "volume_with_table_s40", "Real Time Payments - Volume",
        _make_volume_with_table(
            "Real Time Payments - Volume", ["USD", "EUR", "GBP"],
            1000, 80000, "Beneficiary", 200, 50000, "{:,}",
        ),
    ),
    SeedSpec(
        "volume_with_table_s41", "Real Time Payments - Value",
        _make_volume_with_table(
            "Real Time Payments - Value", ["USD", "EUR", "GBP"],
            1000, 80000, "Remitter", 200, 50000, "USD {:,}",
        ),
    ),

    # case_subtype family
    SeedSpec(
        "case_subtype_s22", "Cases by Sub-type",
        _make_case_subtype(
            "Cases by Sub-type",
            [
                "Additional Details",
                "Amend / Recall / Cancel",
                "Return of Funds",
                "Status / Trace",
                "Compliance / OFAC / RFI",
                "NSF / Limit Enquiry",
            ],
            50, 320,
            drivers_by_subtype={
                "Additional Details": [
                    "Track Payments",
                    "RFI Message Centre and Customer Alerts",
                ],
                "Amend / Recall / Cancel": [
                    "Track Payments",
                    "RFI Message Centre",
                ],
                "Return of Funds": [
                    "Message Centre",
                    "Message Centre Open Investigation",
                ],
                "Status / Trace": [
                    "Message Centre Open Investigation",
                ],
                "Compliance / OFAC / RFI": [
                    "SWIFT GPI Tracker in Track Payments",
                ],
                "NSF / Limit Enquiry": [
                    "Track Payments",
                ],
            },
        ),
    ),

    # multi_chart_trend family (matches test_final4.ps1 series names exactly)
    SeedSpec(
        "multi_chart_trend_s20", "Service Queries Trend",
        _make_multi_chart_trend(
            "Service Queries Trend",
            [
                (
                    ["Opened", "Resolved", "Average TAT"],
                    [(80, 200), (80, 200), (2, 8)],
                ),
            ],
            "Opened and resolved volumes track closely with TAT around five days.",
        ),
    ),
    SeedSpec(
        "multi_chart_trend_s25", "STP and Non-STP",
        _make_multi_chart_trend(
            "STP and Non-STP",
            [
                (
                    ["STP Instructions", "Repaired (Non-STP)", "STP %"],
                    [(30000, 60000), (1000, 5000), (88, 97)],
                ),
                (
                    ["RFI", "RFI%"],
                    [(50, 200), (1, 6)],
                ),
            ],
            "STP rates remain consistently above 90 per cent with RFI volumes broadly stable.",
        ),
    ),
    SeedSpec(
        "multi_chart_trend_s45", "Channel Mix Trend",
        _make_multi_chart_trend(
            "Channel Mix Trend",
            [
                (
                    ["SWIFT", "H2H", "FLU", "HSBCnet On Screen", "API"],
                    [(1000, 5000), (500, 3000), (200, 1500), (100, 800), (50, 600)],
                ),
                (
                    ["SWIFT", "H2H", "FLU", "HSBCnet on Screen", "API"],
                    [(100000, 500000), (50000, 300000), (20000, 150000), (10000, 80000), (5000, 60000)],
                ),
            ],
            "SWIFT continues to dominate both volume and value across all months.",
        ),
    ),
]


# --------------------------------------------------------------- main


def _build_documents(
    customer_id: str, customer_name: str, period: str, seed: int
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for spec in SEEDS:
        # Per-slide deterministic RNG so a single slide can be re-seeded
        # in isolation without reshuffling every other slide.
        rng = random.Random(f"{seed}:{spec.slide_id}")
        content, metadata = spec.builder(rng, customer_name, period, spec.slide_id)
        doc_id = f"{customer_id}-{spec.slide_id.replace('_', '-')}-001"
        docs.append(
            {
                "id": doc_id,
                "customer_id": customer_id,
                "slide_id": spec.slide_id,
                "content": content,
                "metadata": json.dumps(metadata, separators=(",", ":")),
            }
        )
    return docs


def _make_client(endpoint: str, index: str) -> SearchClient:
    api_key = os.environ.get("SEARCH_ADMIN_KEY")
    if api_key:
        logger.info("Using SEARCH_ADMIN_KEY from environment")
        cred: Any = AzureKeyCredential(api_key)
    else:
        logger.info("Using DefaultAzureCredential")
        cred = DefaultAzureCredential()
    return SearchClient(endpoint=endpoint, index_name=index, credential=cred)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed Azure AI Search index for ASRP PoC.")
    parser.add_argument("--endpoint", default=os.environ.get("SEARCH_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--index", default=os.environ.get("SEARCH_INDEX", DEFAULT_INDEX))
    parser.add_argument("--customer-id", default=DEFAULT_CUSTOMER_ID)
    parser.add_argument("--customer-name", default=DEFAULT_CUSTOMER_NAME)
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument("--seed", type=int, default=20251510)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the documents that would be uploaded but do not call the index.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional path to write the generated documents as JSON for inspection.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    docs = _build_documents(args.customer_id, args.customer_name, args.period, args.seed)
    logger.info("Built %d documents for customer_id=%s", len(docs), args.customer_id)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(docs, f, indent=2)
        logger.info("Wrote preview to %s", args.out)

    if args.dry_run:
        logger.info("Dry run - not uploading.")
        for d in docs:
            logger.info(
                "  id=%s slide_id=%s content_len=%d",
                d["id"], d["slide_id"], len(d["content"]),
            )
        return 0

    client = _make_client(args.endpoint, args.index)
    with client:
        result = client.merge_or_upload_documents(documents=docs)
        ok = sum(1 for r in result if r.succeeded)
        fail = [r for r in result if not r.succeeded]
        logger.info("Upload result: %d succeeded, %d failed", ok, len(fail))
        for r in fail:
            logger.error("  failed: key=%s status=%s error=%s", r.key, r.status_code, r.error_message)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
