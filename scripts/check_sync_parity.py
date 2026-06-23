#!/usr/bin/env python3
"""Verify that analytic tables are in sync between two Postgres databases.

Compares row counts, business-date ranges, freshness, and per-day row counts
across a source DB (default: local DATABASE_URL) and a target DB (default:
TARGET_DATABASE_URL = Railway). Use it after a sync to confirm the hosted
chatbot is reading complete, up-to-date data.

Examples:
    # Compare local vs Railway across all synced tables
    python scripts/check_sync_parity.py

    # Only the day that was just synced, with per-day detail
    python scripts/check_sync_parity.py --start 2026-06-22 --end 2026-06-22 --show-days

    # Single-database internal check (source == target)
    python scripts/check_sync_parity.py --target-url "$DATABASE_URL"

Exit code is non-zero when any table is out of sync (handy for CI / cron).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

load_dotenv(ROOT / ".env")

from orchestration.analysis.sync_audit import (  # noqa: E402
    AUDIT_SPECS,
    DEFAULT_AUDIT_TABLES,
    TableComparison,
    TableSnapshot,
    all_in_sync,
    audit_sync,
)
from orchestration.analysis.timeframes import parse_window_bound  # noqa: E402
from orchestration.db.session import get_engine, normalize_database_url  # noqa: E402

_STATUS_PREFIX = {
    "OK": "[OK]   ",
    "MISMATCH": "[DIFF] ",
    "MISSING_TABLE": "[MISS] ",
    "ERROR": "[ERR]  ",
}


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.isoformat(sep=" ", timespec="seconds")


def _fmt_range(snapshot: TableSnapshot) -> str:
    if not snapshot.exists:
        return "table missing"
    if snapshot.error:
        return f"error: {snapshot.error[:80]}"
    return f"{_fmt_dt(snapshot.min_date)} .. {_fmt_dt(snapshot.max_date)}"


def _print_comparison(cmp: TableComparison, *, show_days: bool, max_days: int) -> None:
    prefix = _STATUS_PREFIX.get(cmp.status, "       ")
    click.echo(f"{prefix}{cmp.table}")

    if cmp.status == "ERROR":
        for label, snap in (("source", cmp.source), ("target", cmp.target)):
            if snap.error:
                click.echo(f"         {label} error: {snap.error[:200]}")
        return
    if cmp.status == "MISSING_TABLE":
        if not cmp.source.exists:
            click.echo("         source: table does not exist")
        if not cmp.target.exists:
            click.echo("         target: table does not exist")
        return

    delta = cmp.count_delta
    delta_str = f"{delta:+d}" if delta else "match"
    click.echo(
        f"         rows   source={cmp.source.total:<8} "
        f"target={cmp.target.total:<8} delta={delta_str}"
    )
    click.echo(
        f"         dates  source[{_fmt_range(cmp.source)}]"
    )
    click.echo(
        f"                target[{_fmt_range(cmp.target)}]"
    )
    click.echo(
        f"         fresh  source={_fmt_dt(cmp.source.max_updated)} "
        f"target={_fmt_dt(cmp.target.max_updated)}"
    )
    if cmp.source.null_date_count or cmp.target.null_date_count:
        click.echo(
            "         null-dated rows (not date-compared): "
            f"source={cmp.source.null_date_count} target={cmp.target.null_date_count}"
        )

    if cmp.daily_deltas:
        click.echo(
            f"         {len(cmp.daily_deltas)} day(s) differ "
            f"({cmp.missing_in_target_days} short in target, "
            f"{cmp.extra_in_target_days} extra in target)"
        )
        if show_days:
            shown = cmp.daily_deltas[:max_days]
            for d in shown:
                click.echo(
                    f"           {d.day.isoformat()}  "
                    f"source={d.source_count:<6} target={d.target_count:<6} "
                    f"delta={d.delta:+d}"
                )
            if len(cmp.daily_deltas) > max_days:
                click.echo(f"           ... {len(cmp.daily_deltas) - max_days} more day(s)")


@click.command()
@click.option(
    "--source-url",
    envvar="SOURCE_DATABASE_URL",
    default=None,
    help="Source DB (default: DATABASE_URL from .env = local Docker).",
)
@click.option(
    "--target-url",
    envvar="TARGET_DATABASE_URL",
    required=True,
    help="Target DB to compare against (Railway public URL).",
)
@click.option(
    "--tables",
    default=",".join(DEFAULT_AUDIT_TABLES),
    show_default=True,
    help=f"Comma-separated tables to check. Available: {', '.join(AUDIT_SPECS)}.",
)
@click.option("--start", default=None, help="Only compare rows with date >= this (ISO or YYYY-MM-DD).")
@click.option("--end", default=None, help="Only compare rows with date <= this (ISO or YYYY-MM-DD).")
@click.option("--show-days/--no-show-days", default=False, help="List the days that differ.")
@click.option("--max-days", default=20, show_default=True, help="Max differing days to list per table.")
@click.option(
    "--statement-timeout-ms",
    default=60_000,
    show_default=True,
    help="Per-query Postgres statement timeout (ms).",
)
def main(
    source_url: str | None,
    target_url: str,
    tables: str,
    start: str | None,
    end: str | None,
    show_days: bool,
    max_days: int,
    statement_timeout_ms: int,
) -> None:
    """Compare row counts and date alignment across two Postgres databases."""
    if not source_url:
        from orchestration.config import get_settings

        source_url = get_settings().database_url

    source_url = normalize_database_url(source_url)
    target_url = normalize_database_url(target_url)

    if "railway.internal" in target_url:
        raise click.ClickException(
            "TARGET_DATABASE_URL uses a Railway private hostname (postgres.railway.internal), "
            "which only works from services running on Railway. Use the public URL "
            "(host like *.proxy.rlwy.net) from Railway -> Postgres -> Connect."
        )

    table_names = [name.strip() for name in tables.split(",") if name.strip()]
    unknown = [name for name in table_names if name not in AUDIT_SPECS]
    if unknown:
        raise click.ClickException(
            f"Unknown tables: {unknown}. Choose from {list(AUDIT_SPECS)}"
        )

    start_dt = parse_window_bound(start, is_end=False) if start else None
    end_dt = parse_window_bound(end, is_end=True) if end else None
    if start_dt and end_dt and end_dt <= start_dt:
        raise click.ClickException("--end must be after --start.")

    same_db = source_url == target_url
    scope = "single database (internal consistency)" if same_db else "source -> target"
    window = (
        f"{start_dt.isoformat()} .. {end_dt.isoformat()}"
        if (start_dt or end_dt)
        else "all rows"
    )
    click.echo(f"Sync parity check ({scope}) | window: {window}")
    click.echo(f"Tables: {', '.join(table_names)}\n")

    source_engine = get_engine(source_url)
    target_engine = get_engine(target_url)

    comparisons = audit_sync(
        source_engine,
        target_engine,
        table_names=table_names,
        start=start_dt,
        end=end_dt,
        statement_timeout_ms=statement_timeout_ms,
    )

    for cmp in comparisons:
        _print_comparison(cmp, show_days=show_days, max_days=max_days)
        click.echo("")

    ok = all_in_sync(comparisons)
    if ok:
        click.echo("All checked tables are in sync.")
    else:
        bad = [c.table for c in comparisons if c.status != "OK"]
        click.echo(f"Out of sync: {', '.join(bad)}")
        click.echo(
            "Hint: re-run scripts/sync_to_railway.py (optionally with the same "
            "--interaction-start/--interaction-end window) to close the gap."
        )
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
