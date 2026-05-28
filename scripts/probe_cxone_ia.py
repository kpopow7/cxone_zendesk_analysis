#!/usr/bin/env python3
"""Debug CXone Interaction Analytics API: auth, URL, params, and raw response shape."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.config import get_settings, parse_iso_datetime  # noqa: E402
from orchestration.cxone.auth import CxoneAuthClient  # noqa: E402
from orchestration.cxone.transcripts import CxoneTranscriptExtractor  # noqa: E402



def _summarize_payload(payload: object) -> dict:
    if isinstance(payload, list):
        return {"type": "list", "length": len(payload), "first_keys": list(payload[0].keys()) if payload else []}
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}

    summary: dict = {"type": "object", "top_level_keys": list(payload.keys())}
    for key, value in payload.items():
        if isinstance(value, list):
            summary[f"{key}_count"] = len(value)
            if value and isinstance(value[0], dict):
                summary[f"{key}_first_keys"] = list(value[0].keys())[:15]
        elif isinstance(value, dict):
            summary[f"{key}_keys"] = list(value.keys())[:15]
            summary[f"{key}_nested_count"] = len(value)
    return summary


def _count_parsed_segments(payload: object) -> int:
    return len(CxoneTranscriptExtractor._extract_segment_list(payload))


def main() -> None:
    import click

    @click.command()
    @click.option("--start", default=None, help="ISO start (optional)")
    @click.option("--end", default=None, help="ISO end (optional)")
    @click.option("--no-date-filter", is_flag=True, help="Call API with no date query params")
    def run(start: str | None, end: str | None, no_date_filter: bool) -> None:
        load_dotenv(ROOT / ".env")
        settings = get_settings()
        auth = CxoneAuthClient(settings)
        session = auth.get_session()

        base = f"{session.api_base_url}{settings.cxone_ia_api_path}"
        url = f"{base}/segments/analyzed"
        headers = {
            "Authorization": f"Bearer {session.access_token}",
            "Accept": "application/json",
        }

        click.echo("=== CXone session ===")
        click.echo(f"tenant_id: {session.tenant_id}")
        click.echo(f"api_base_url: {session.api_base_url}")
        click.echo(f"ia_path: {settings.cxone_ia_api_path}")
        click.echo(f"full_url: {url}")
        click.echo()

        start_dt = parse_iso_datetime(start) if start else None
        end_dt = parse_iso_datetime(end) if end else None

        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            if no_date_filter:
                trials = [({}, "no date filters")]
            elif start_dt and end_dt:
                trials = [
                    (
                        {
                            "pageSize": 10,
                            "dateField": settings.cxone_ia_date_field,
                            "order": settings.cxone_ia_order,
                        },
                        "configured list params (date range applied client-side in extractor)",
                    ),
                ]
            else:
                trials = [({"pageSize": 10}, "pageSize only (no dates)")]

            for params, label in trials:
                click.echo(f"--- Trial: {label} ---")
                click.echo(f"params: {params}")
                response = client.get(url, headers=headers, params=params)
                click.echo(f"status: {response.status_code}")
                if not response.is_success:
                    click.echo(response.text[:500])
                    click.echo()
                    continue
                try:
                    payload = response.json()
                except json.JSONDecodeError:
                    click.echo("response is not JSON")
                    click.echo(response.text[:500])
                    click.echo()
                    continue

                summary = _summarize_payload(payload)
                parsed = _count_parsed_segments(payload)
                click.echo(f"summary: {json.dumps(summary, indent=2)}")
                click.echo(f"segments parsed by extractor: {parsed}")
                if parsed == 0:
                    click.echo("raw sample (first 2000 chars):")
                    raw = json.dumps(payload, indent=2, default=str)
                    click.echo(raw[:2000])
                    if len(raw) > 2000:
                        click.echo("...")
                click.echo()

    run()


if __name__ == "__main__":
    main()
