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

from orchestration.config import get_settings  # noqa: E402
from orchestration.db.schema import init_database  # noqa: E402


def main() -> None:
    load_dotenv(ROOT / ".env")
    settings = get_settings()
    init_database(settings.database_url)
    print(f"Database ready: {settings.database_url}")
    print(
        "Tables: cxone_transcripts, cxone_transcript_analysis, zendesk_tickets, "
        "zendesk_ticket_comments, combined_interactions"
    )


if __name__ == "__main__":
    main()
