"""Local-only emulation of the Copilot Studio Agent's per-slide loop.

This script is for engineering verification of contract shape; it is not
used in production. It calls the deployed ``generate_slide_description``
HTTP function sequentially for each slide, collects responses, and
writes them to ``out/{run_id}.json``.

Usage::

    python tools/run_slide_loop.py \
        --customer-id ACME-001 \
        --run-id local-2026-05-06-001 \
        --prompt-version v1 \
        --schema-version v1 \
        [--slides footprint_v1,payments_stp_v1] \
        [--endpoint https://<func>.azurewebsites.net/api/generate_slide_description] \
        [--user-id me@example.com]

Authentication: when ``ASRP_FUNCTION_KEY`` is set it is sent as
``x-functions-key``; otherwise the request is sent unauthenticated
(suitable for ``func start`` locally).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

# Allow running as a plain script: ``python tools/run_slide_loop.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_FUNC_APP_ROOT = _REPO_ROOT / "asrp_functions"
if str(_FUNC_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_FUNC_APP_ROOT))

from shared.prompt_registry import PromptRegistry  # noqa: E402

# Canonical CSR template order (Technical Appendix §A2.2).
CANONICAL_SLIDE_ORDER: tuple[str, ...] = (
    "footprint_v1",
    "payments_stp_v1",
    "query_analysis_v1",
    "product_updates_v1",
)

DEFAULT_ENDPOINT = "http://localhost:7071/api/generate_slide_description"
DEFAULT_TIMEOUT_SECONDS = 60.0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local emulator for the ASRP per-slide loop."
    )
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prompt-version", required=True)
    parser.add_argument("--schema-version", required=True)
    parser.add_argument(
        "--user-id",
        default=os.environ.get("USER") or os.environ.get("USERNAME") or "local-tester",
    )
    parser.add_argument(
        "--slides",
        default=None,
        help="Optional comma-separated slide_ids to run. Defaults to all "
        "registered prompts in the canonical CSR order.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("ASRP_FUNCTION_URL", DEFAULT_ENDPOINT),
        help="Full URL of the deployed generate_slide_description function.",
    )
    parser.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--out-dir",
        default=str(_REPO_ROOT / "out"),
        help="Directory to write the aggregated payload into.",
    )
    return parser.parse_args(argv)


def _resolve_slide_order(filter_arg: str | None) -> list[str]:
    """Resolve the ordered slide_ids to run.

    The base list is :data:`CANONICAL_SLIDE_ORDER` intersected with the
    prompts actually registered in the local repo, with any extra
    registered slides appended (sorted) at the end.
    """
    registry = PromptRegistry()
    registered = set(registry.list_slide_ids())

    base: list[str] = [s for s in CANONICAL_SLIDE_ORDER if s in registered]
    extras = sorted(registered - set(base))
    available = base + extras

    if filter_arg is None:
        return available

    requested = [s.strip() for s in filter_arg.split(",") if s.strip()]
    unknown = [s for s in requested if s not in registered]
    if unknown:
        raise SystemExit(
            f"Unknown slide_id(s): {', '.join(unknown)}. "
            f"Registered: {', '.join(sorted(registered))}"
        )
    # Preserve user-specified order.
    return requested


def _call_generate(
    *,
    client: httpx.Client,
    endpoint: str,
    customer_id: str,
    slide_id: str,
    run_id: str,
    user_id: str,
    prompt_version: str,
    schema_version: str,
) -> dict[str, Any]:
    """Call the deployed function and return a response dict.

    On any local/transport error, returns a synthetic ``status="failed"``
    payload so the loop continues.
    """
    payload = {
        "customer_id": customer_id,
        "slide_id": slide_id,
        "run_id": run_id,
        "user_id": user_id,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
    }
    headers = {"Content-Type": "application/json"}
    function_key = os.environ.get("ASRP_FUNCTION_KEY")
    if function_key:
        headers["x-functions-key"] = function_key

    try:
        response = client.post(endpoint, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        return {
            "slide_id": slide_id,
            "status": "failed",
            "description": {},
            "data_gaps": [f"transport_error: {type(exc).__name__}: {exc}"],
            "model_metadata": {},
        }

    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        return {
            "slide_id": slide_id,
            "status": "failed",
            "description": {},
            "data_gaps": [f"http_{response.status_code}: {detail}"],
            "model_metadata": {},
        }

    try:
        return response.json()
    except ValueError as exc:
        return {
            "slide_id": slide_id,
            "status": "failed",
            "description": {},
            "data_gaps": [f"non_json_response: {exc}"],
            "model_metadata": {},
        }


def _print_status_table(rows: list[dict[str, Any]]) -> None:
    """Print a fixed-width status table to stdout."""
    headers = ("slide_id", "status", "data_gaps", "model")
    table_rows = [
        (
            str(r.get("slide_id", "")),
            str(r.get("status", "")),
            str(len(r.get("data_gaps") or [])),
            str((r.get("model_metadata") or {}).get("model") or "-"),
        )
        for r in rows
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in table_rows)) if table_rows else len(headers[i])
        for i in range(len(headers))
    ]
    sep = "  "

    def fmt(row: tuple[str, ...]) -> str:
        return sep.join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    sys.stdout.write(fmt(headers) + "\n")
    sys.stdout.write(sep.join("-" * w for w in widths) + "\n")
    for row in table_rows:
        sys.stdout.write(fmt(row) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    slide_ids = _resolve_slide_order(args.slides)

    if not slide_ids:
        sys.stderr.write("No slides resolved to run. Check PromptRegistry contents.\n")
        return 2

    timeout = httpx.Timeout(args.timeout_seconds, connect=10.0)
    responses: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout) as client:
        for slide_id in slide_ids:
            sys.stdout.write(f">> {slide_id}\n")
            sys.stdout.flush()
            result = _call_generate(
                client=client,
                endpoint=args.endpoint,
                customer_id=args.customer_id,
                slide_id=slide_id,
                run_id=args.run_id,
                user_id=args.user_id,
                prompt_version=args.prompt_version,
                schema_version=args.schema_version,
            )
            # Defensive: ensure slide_id is present even on failure paths.
            result.setdefault("slide_id", slide_id)
            responses.append(result)

    _print_status_table(responses)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.run_id}.json"
    aggregate = {
        "run_id": args.run_id,
        "customer_id": args.customer_id,
        "user_id": args.user_id,
        "prompt_version": args.prompt_version,
        "schema_version": args.schema_version,
        "endpoint": args.endpoint,
        "slides": responses,
    }
    out_path.write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
    sys.stdout.write(f"\nWrote {out_path}\n")

    failed = sum(1 for r in responses if r.get("status") == "failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
