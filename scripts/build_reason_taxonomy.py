#!/usr/bin/env python3
"""Build/refresh the controlled reason taxonomy map (analytics_reason_taxonomy).

Resolves every distinct free-text reason in the data (transcript primary reasons + Zendesk
call reasons) to a canonical label using config/reason_taxonomy.json, then upserts the mapping
table the analytics views join to. No LLM calls — fast and idempotent. Run after editing the
taxonomy config, after a backfill, or rely on the daily pipeline to keep it current.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.analysis.reason_taxonomy import load_reason_taxonomy  # noqa: E402
from orchestration.analysis.reason_taxonomy_builder import build_reason_taxonomy_map  # noqa: E402
from orchestration.config import get_settings  # noqa: E402
from orchestration.db.analytics_views import ensure_analytics_views  # noqa: E402
from orchestration.db.session import get_engine, normalize_database_url  # noqa: E402


@click.command()
@click.option(
    "--config",
    "config_path",
    default="config/reason_taxonomy.json",
    show_default=True,
    help="Taxonomy config file (falls back to the bundled .example if missing).",
)
@click.option(
    "--database-url",
    "--target-url",
    "database_url",
    envvar="TARGET_DATABASE_URL",
    default=None,
    help="Build the map on this DB instead of DATABASE_URL (e.g. Railway public URL).",
)
@click.option(
    "--no-prune",
    is_flag=True,
    default=False,
    help="Keep mappings for reasons no longer present in the data (default: prune them).",
)
@click.option(
    "--refresh-views/--no-refresh-views",
    default=True,
    show_default=True,
    help="Refresh analytics views after building (ensures canonical columns are present).",
)
def main(
    config_path: str,
    database_url: str | None,
    no_prune: bool,
    refresh_views: bool,
) -> None:
    """Populate analytics_reason_taxonomy from the configured vocabulary + observed reasons."""
    load_dotenv(ROOT / ".env")
    settings = get_settings()

    target_url = normalize_database_url(database_url) if database_url else settings.database_url
    if "railway.internal" in target_url:
        raise click.ClickException(
            "The target database URL uses a Railway private hostname "
            "(postgres.railway.internal). Use the public URL (*.proxy.rlwy.net / *.railway.app)."
        )

    from sqlalchemy.engine import make_url

    target_host = make_url(target_url).host or "(local)"
    taxonomy = load_reason_taxonomy(config_path)
    click.echo(
        f"Building reason taxonomy on database host: {target_host} "
        f"({len(taxonomy.categories)} canonical categories)"
    )

    engine = get_engine(target_url)
    if refresh_views:
        ensure_analytics_views(engine)

    result = build_reason_taxonomy_map(engine, taxonomy, prune_missing=not no_prune)

    click.echo(f"Distinct reasons observed: {result.distinct_reasons}")
    click.echo(f"Mapped to a canonical category: {result.mapped_reasons}")
    click.echo(f"Fell back to '{taxonomy.fallback_canonical}': {result.fallback_reasons}")
    if result.fallback_reasons:
        click.echo(
            "Tip: review the fallback reasons and add aliases to "
            f"{config_path} to capture more volume under named categories.",
            err=True,
        )


if __name__ == "__main__":
    main()
