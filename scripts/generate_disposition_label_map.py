#!/usr/bin/env python3
"""List disposition codes from combined_interactions and scaffold disposition_label_map.json."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import click
from dotenv import load_dotenv
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.analysis.disposition_labels import (  # noqa: E402
    humanize_disposition_code,
    load_disposition_label_config,
    normalize_disposition_code,
)
from orchestration.analysis.reasons import extract_disposition
from orchestration.config import get_settings  # noqa: E402
from orchestration.db.schema import CombinedInteractionRow  # noqa: E402
from orchestration.db.session import get_session_factory  # noqa: E402


@click.command()
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=ROOT / "config" / "disposition_label_map.json",
    help="Write or update label map JSON (merges with existing labels).",
)
@click.option("--top", default=50, show_default=True, help="Number of disposition codes to include.")
@click.option("--dry-run", is_flag=True, help="Print to stdout only; do not write file.")
def main(output: Path, top: int, dry_run: bool) -> None:
    """Emit disposition codes ranked by volume with suggested human labels."""
    load_dotenv(ROOT / ".env")
    settings = get_settings()
    existing = load_disposition_label_config(output)

    disposition_fields = (
        "cf_disposition",
        "cf_disposition_consumer",
        "cf_disposition_dealer",
        "cf_disposition_consumer_levolor",
        "cf_disposition_customer_levolor",
    )
    counts: Counter[str] = Counter()

    with get_session_factory(settings.database_url)() as session:
        rows = session.scalars(select(CombinedInteractionRow)).all()
        for row in rows:
            promoted = row.zendesk_promoted_fields if isinstance(row.zendesk_promoted_fields, dict) else {}
            code, _label = extract_disposition(
                promoted,
                disposition_fields=disposition_fields,
                label_map=existing.labels,
                fallback_humanize=existing.fallback_humanize,
            )
            if code:
                counts[code] += 1

    labels = dict(existing.labels)
    for code, _count in counts.most_common(top):
        key = normalize_disposition_code(code)
        if key not in labels:
            labels[key] = humanize_disposition_code(code)

    payload = {
        "fallback_humanize": existing.fallback_humanize,
        "labels": dict(sorted(labels.items())),
    }

    if dry_run:
        click.echo(json.dumps(payload, indent=2))
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    click.echo(f"Wrote {output} ({len(labels)} labels, top {top} codes scanned)")


if __name__ == "__main__":
    main()
