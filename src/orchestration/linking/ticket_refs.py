from __future__ import annotations

import re
from typing import Any


def normalize_link_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_ticket_reference(value: Any) -> int | None:
    """Parse a Zendesk ticket id from custom-field values (plain id, #123, or URL)."""
    text = normalize_link_value(value)
    if text is None:
        return None
    if text.isdigit():
        return int(text)
    match = re.search(r"/tickets/(\d+)", text)
    if match:
        return int(match.group(1))
    trailing_digits = re.search(r"(\d+)\s*$", text)
    if trailing_digits:
        return int(trailing_digits.group(1))
    return None
