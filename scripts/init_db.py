#!/usr/bin/env python3
"""Create PostgreSQL tables for the orchestration pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import text  # noqa: E402

from orchestration.config import get_settings  # noqa: E402
from orchestration.db.schema import init_database  # noqa: E402
from orchestration.db.session import get_engine  # noqa: E402


def _list_objects(database_url: str) -> tuple[list[str], list[str]]:
    """Return (tables, views) in the public schema after init, for an honest report."""
    engine = get_engine(database_url)
    with engine.connect() as connection:
        tables = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name"
                )
            )
        ]
        views = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT table_name FROM information_schema.views "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
            )
        ]
    return tables, views


def main() -> None:
    load_dotenv(ROOT / ".env")
    settings = get_settings()
    init_database(settings.database_url)

    tables, views = _list_objects(settings.database_url)
    print(f"Database ready: {settings.database_url}")
    print(f"\nTables ({len(tables)}):")
    for name in tables:
        print(f"  - {name}")
    print(f"\nViews ({len(views)}):")
    for name in views:
        print(f"  - {name}")
    print(
        "\nNote: analytics_reason_taxonomy is created empty. Populate the canonical map with "
        "`python scripts/build_reason_taxonomy.py` (it needs cxone_transcript_analysis and/or "
        "combined_interactions data first)."
    )


if __name__ == "__main__":
    main()
