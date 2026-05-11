"""End-to-end driver: call /api/slides/generate for every concrete
slide_id, then submit the validated descriptions to /api/deck/assemble
and verify the assembled deck.

Usage::

    python -m tools.run_e2e --customer-id cust-alpine-industries

Authentication uses the function app master key (auto-fetched via
``az functionapp keys list``) unless ``ASRP_FUNCTION_KEY`` is set.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.request import urlretrieve
from uuid import uuid4

import httpx


logger = logging.getLogger("run_e2e")

# All concrete slide_ids seeded in the AI Search index. Order is the
# order they will be sent to /deck/assemble (the assembler uses each
# slide_id to look up its template position).
SLIDE_IDS: list[str] = [
    "service_queries",
    "multi_chart_trend_s20",
    "case_subtype_s22",
    "multi_chart_trend_s25",
    "volume_by_type_s28",
    "volume_by_country_s29",
    "volume_by_type_s31",
    "volume_by_type_s32",
    "volume_with_table_s33",
    "volume_by_type_s34",
    "volume_with_table_s35",
    "volume_with_table_s36",
    "volume_with_table_s37",
    "volume_with_table_s38",
    "volume_with_table_s39",
    "volume_with_table_s40",
    "volume_with_table_s41",
    "volume_by_type_s42",
    "volume_by_country_s43",
    "multi_chart_trend_s45",
    "channel_mix_s46",
    "channel_mix_s47",
    "channel_mix_s48",
    "volume_by_country_s49",
]


DEFAULT_FUNCTION_HOST = "https://func-asrp-poc-sm01.azurewebsites.net"
DEFAULT_FUNCTION_APP = "func-asrp-poc-sm01"
DEFAULT_RG = "rg-asrp-poc-app"


def _get_function_key(app_name: str, rg: str) -> str:
    key = os.environ.get("ASRP_FUNCTION_KEY")
    if key:
        return key
    logger.info("Fetching master key for %s/%s via az CLI", rg, app_name)
    result = subprocess.run(
        [
            "az", "functionapp", "keys", "list",
            "-n", app_name, "-g", rg,
            "--query", "masterKey", "-o", "tsv",
        ],
        capture_output=True, text=True, check=True, shell=True,
    )
    return result.stdout.strip()


def _generate(
    client: httpx.Client,
    host: str,
    headers: dict[str, str],
    *,
    customer_id: str,
    slide_id: str,
    run_id: str,
    user_id: str,
    prompt_version: str,
    schema_version: str,
) -> dict[str, Any]:
    payload = {
        "customer_id": customer_id,
        "slide_id": slide_id,
        "run_id": run_id,
        "user_id": user_id,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
    }
    url = f"{host}/api/slides/generate"
    t0 = time.monotonic()
    r = client.post(url, json=payload, headers=headers)
    dt = time.monotonic() - t0
    if r.status_code != 200:
        logger.error(
            "  generate %s -> http %d (%.1fs): %s",
            slide_id, r.status_code, dt, r.text[:300],
        )
        return {
            "slide_id": slide_id, "status": "failed",
            "description": {}, "data_gaps": [f"http_{r.status_code}"],
            "model_metadata": {},
        }
    body = r.json()
    logger.info(
        "  generate %s -> %s gaps=%d (%.1fs)",
        slide_id, body.get("status"), len(body.get("data_gaps") or []), dt,
    )
    return body


def _assemble(
    client: httpx.Client,
    host: str,
    headers: dict[str, str],
    *,
    customer_id: str,
    customer_name: str,
    period: str,
    run_id: str,
    slides: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "period": period,
        "run_id": run_id,
        "slides": slides,
    }
    url = f"{host}/api/deck/assemble"
    t0 = time.monotonic()
    # Long timeout: assembly is sequential across all slides.
    r = client.post(url, json=payload, headers=headers, timeout=300.0)
    dt = time.monotonic() - t0
    logger.info("assemble -> http %d (%.1fs)", r.status_code, dt)
    if r.status_code != 200:
        logger.error("  body: %s", r.text[:500])
        r.raise_for_status()
    return r.json()


def _verify_deck(deck_path: Path, unzip_dir: Path) -> dict[str, Any]:
    if unzip_dir.exists():
        shutil.rmtree(unzip_dir)
    unzip_dir.mkdir(parents=True)
    with zipfile.ZipFile(deck_path) as z:
        z.extractall(unzip_dir)

    slides_dir = unzip_dir / "ppt" / "slides"
    leftover_account = 0
    leftover_period = 0
    leftover_commentary = 0
    customer_found = 0
    slide_count = 0
    for slide_xml in sorted(slides_dir.glob("slide*.xml")):
        slide_count += 1
        text = slide_xml.read_text(encoding="utf-8")
        if "[MG / Legal Entity" in text or "[MG/Legal Entity" in text:
            leftover_account += 1
        if "[MMM YYYY" in text:
            leftover_period += 1
        if "[  ]" in text:
            leftover_commentary += 1
        if "Alpine Industries" in text:
            customer_found += 1

    return {
        "slide_count": slide_count,
        "customer_token_left": leftover_account,
        "period_token_left": leftover_period,
        "commentary_token_left": leftover_commentary,
        "alpine_industries_found_in": customer_found,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="End-to-end ASRP run.")
    parser.add_argument("--host", default=os.environ.get("ASRP_FUNCTION_HOST", DEFAULT_FUNCTION_HOST))
    parser.add_argument("--function-app", default=DEFAULT_FUNCTION_APP)
    parser.add_argument("--resource-group", default=DEFAULT_RG)
    parser.add_argument("--customer-id", default="cust-alpine-industries")
    parser.add_argument("--customer-name", default="Alpine Industries Ltd")
    parser.add_argument("--period", default="Oct 2024 - Sep 2025")
    parser.add_argument("--prompt-version", default="v1")
    parser.add_argument("--schema-version", default="v1")
    parser.add_argument("--user-id", default=os.environ.get("USERNAME", "local-tester"))
    parser.add_argument("--out-dir", default="./out_e2e")
    parser.add_argument(
        "--slides",
        default=None,
        help="Optional comma-separated subset of slide_ids to run.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"e2e-{uuid4().hex[:8]}"

    slide_ids = SLIDE_IDS
    if args.slides:
        requested = [s.strip() for s in args.slides.split(",") if s.strip()]
        unknown = [s for s in requested if s not in SLIDE_IDS]
        if unknown:
            logger.error("Unknown slide_ids: %s", ", ".join(unknown))
            return 2
        slide_ids = requested

    key = _get_function_key(args.function_app, args.resource_group)
    headers = {"x-functions-key": key, "Content-Type": "application/json"}

    descriptions: list[dict[str, Any]] = []
    failed_generate: list[str] = []
    logger.info("== generate (%d slides) run_id=%s ==", len(slide_ids), run_id)
    with httpx.Client(timeout=120.0) as client:
        for sid in slide_ids:
            r = _generate(
                client, args.host, headers,
                customer_id=args.customer_id, slide_id=sid, run_id=run_id,
                user_id=args.user_id,
                prompt_version=args.prompt_version,
                schema_version=args.schema_version,
            )
            if r.get("status") == "failed":
                failed_generate.append(sid)
                continue
            desc = r.get("description") or {}
            if not isinstance(desc, dict) or not desc:
                failed_generate.append(sid)
                continue
            descriptions.append(desc)

    # Persist generation outputs for inspection.
    (out_dir / f"{run_id}_descriptions.json").write_text(
        json.dumps({"run_id": run_id, "descriptions": descriptions, "failed": failed_generate}, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Generated %d/%d slides ok (failed: %s)",
        len(descriptions), len(slide_ids), failed_generate or "-",
    )

    if not descriptions:
        logger.error("No successful slide descriptions; aborting before assemble.")
        return 1

    logger.info("== assemble ==")
    with httpx.Client(timeout=120.0) as client:
        result = _assemble(
            client, args.host, headers,
            customer_id=args.customer_id, customer_name=args.customer_name,
            period=args.period, run_id=run_id, slides=descriptions,
        )

    (out_dir / f"{run_id}_assemble.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    logger.info(
        "assemble status=%s failed_slide_ids=%s",
        result.get("status"), result.get("failed_slide_ids"),
    )

    deck_url = result.get("deck_blob_url")
    if not deck_url:
        logger.error("No deck_blob_url returned.")
        return 1

    deck_path = out_dir / f"{run_id}.pptx"
    logger.info("Downloading deck to %s", deck_path)
    urlretrieve(deck_url, deck_path)
    unzip_dir = out_dir / f"{run_id}_unzip"
    summary = _verify_deck(deck_path, unzip_dir)
    logger.info("Deck verification: %s", json.dumps(summary))

    rc = 0
    if failed_generate:
        rc = 1
    if result.get("failed_slide_ids"):
        rc = 1
    if summary["commentary_token_left"] or summary["customer_token_left"] or summary["period_token_left"]:
        logger.warning("Leftover tokens detected in assembled deck.")
        rc = 1

    return rc


if __name__ == "__main__":
    sys.exit(main())
