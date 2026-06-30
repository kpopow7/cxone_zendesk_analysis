"""Channel / media-type helpers (P2: multi-channel support).

CXone segments carry a ``media_type`` (e.g. ``PhoneCall``, ``Email``, ``Chat``). The analysis
pipeline is channel-agnostic, but the LLM prompts and human-facing text read better when they
name the actual channel ("this chat", "this email") instead of always saying "call". These
helpers normalize the raw media_type into a friendly label and a noun for prompt wording.
"""

from __future__ import annotations

# Maps a normalized media_type token to (interaction noun, transcript noun).
# interaction noun -> "Analyze this {interaction}."   transcript noun -> "{Transcript}:" header.
_CHANNEL_LABELS: dict[str, tuple[str, str]] = {
    "phonecall": ("phone call", "call transcript"),
    "phone": ("phone call", "call transcript"),
    "voice": ("phone call", "call transcript"),
    "call": ("phone call", "call transcript"),
    "email": ("email", "email"),
    "chat": ("chat", "chat transcript"),
    "livechat": ("chat", "chat transcript"),
    "messaging": ("chat", "chat transcript"),
    "sms": ("text message conversation", "message transcript"),
    "voicemail": ("voicemail", "voicemail transcript"),
}

_DEFAULT_LABEL = ("customer interaction", "transcript")


def _normalize(media_type: str | None) -> str:
    return "".join(ch for ch in (media_type or "").lower() if ch.isalnum())


def channel_label(media_type: str | None) -> str:
    """Human noun for the interaction, e.g. "phone call", "email", "chat".

    Falls back to "customer interaction" for unknown / missing media types.
    """
    return _CHANNEL_LABELS.get(_normalize(media_type), _DEFAULT_LABEL)[0]


def transcript_label(media_type: str | None) -> str:
    """Noun for the text body, e.g. "transcript", "email thread", "chat transcript"."""
    return _CHANNEL_LABELS.get(_normalize(media_type), _DEFAULT_LABEL)[1]
