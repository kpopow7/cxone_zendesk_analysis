#!/usr/bin/env python3
"""Build pgvector knowledge index for chatbot RAG over call summaries + Zendesk context."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.analysis.timeframes import resolve_time_window  # noqa: E402
from orchestration.config import get_settings, parse_iso_datetime  # noqa: E402
from orchestration.db.analytics_views import ensure_analytics_views  # noqa: E402
from orchestration.db.session import get_engine  # noqa: E402
from orchestration.rag.index import build_knowledge_index  # noqa: E402


@click.command()
@click.option(
    "--timeframe",
    "timeframe_preset",
    type=click.Choice(["all", "yesterday", "last-week", "last-7-days"], case_sensitive=False),
    default="all",
)
@click.option("--start", default=None, help="Custom range start (ISO-8601).")
@click.option("--end", default=None, help="Custom range end (ISO-8601).")
@click.option("--limit", type=int, default=None, help="Max calls to index (testing).")
@click.option("--batch-size", type=int, default=32, show_default=True, help="Embedding batch size.")
def main(
    timeframe_preset: str,
    start: str | None,
    end: str | None,
    limit: int | None,
    batch_size: int,
) -> None:
    """Embed call interaction documents for semantic search in the chatbot."""
    load_dotenv(ROOT / ".env")
    settings = get_settings()

    if not settings.openai_api_key:
        raise click.ClickException("OPENAI_API_KEY is required to build embeddings.")

    start_dt = parse_iso_datetime(start) if start else None
    end_dt = parse_iso_datetime(end) if end else None
    if (start and not end) or (end and not start):
        raise click.ClickException("Provide both --start and --end for a custom range.")

    time_window = resolve_time_window(
        preset=timeframe_preset if not (start_dt or end_dt) else timeframe_preset,
        start=start_dt,
        end=end_dt,
    )

    engine = get_engine(settings.database_url)
    click.echo("Ensuring analytics views...")
    ensure_analytics_views(engine)

    click.echo(
        f"Building knowledge index for {time_window.label} "
        f"(batch_size={batch_size})..."
    )
    result = build_knowledge_index(
        engine,
        api_key=settings.openai_api_key,
        embedding_model=settings.openai_embedding_model,
        openai_base_url=settings.openai_base_url,
        start=time_window.start,
        end=time_window.end,
        batch_size=batch_size,
        limit=limit,
        timeout_seconds=settings.request_timeout_seconds,
    )

    click.echo(f"Candidate calls: {result.candidates}")
    click.echo(f"New/updated embeddings: {result.embedded}")
    click.echo(f"Skipped unchanged: {result.skipped_unchanged}")
    if result.errors:
        click.echo(f"Embedding errors: {result.errors}", err=True)
    click.echo(
        "Done. Sync to Railway with:\n"
        "  python scripts/sync_to_railway.py --tables cxone_transcripts,"
        "cxone_transcript_analysis,combined_interactions"
    )


if __name__ == "__main__":
    main()
