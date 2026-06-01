from __future__ import annotations

import json
import re
from dataclasses import dataclass

from orchestration.analysis.llm_client import chat_completion_text, truncate_text
from orchestration.analysis.reasons import normalize_reason_key


class TranscriptClassificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscriptReasonAnalysis:
    transcript_summary: str
    primary_reason: str
    secondary_reason: str
    tertiary_reason: str | None
    reduction_hint: str | None


_SYSTEM_PROMPT = (
    "You classify contact-center phone calls from transcripts. "
    "Respond with valid JSON only — no markdown."
)


def _build_classification_prompt(
    *,
    transcript: str,
    segment_summary: str | None,
    client_sentiment: str | None,
    skill_name: str | None,
    agent_name: str | None,
) -> str:
    context_lines: list[str] = []
    if skill_name:
        context_lines.append(f"Skill/queue: {skill_name}")
    if agent_name:
        context_lines.append(f"Agent: {agent_name}")
    if client_sentiment:
        context_lines.append(f"CXone client sentiment: {client_sentiment}")
    if segment_summary:
        context_lines.append(f"CXone auto-summary (may be incomplete): {segment_summary}")

    context_block = "\n".join(context_lines)
    context_section = f"Metadata:\n{context_block}\n\n" if context_block else ""

    return (
        "Analyze this call transcript. Ignore hold music and small talk.\n\n"
        f"{context_section}"
        f"Transcript:\n{transcript}\n\n"
        "Return a JSON object with these keys:\n"
        '- "transcript_summary": 2-4 sentences on what happened and outcome\n'
        '- "primary_reason": broad category (3-6 words), e.g. "Remake order", '
        '"Order status", "Installation help", "Warranty claim"\n'
        '- "secondary_reason": specific intent within the primary category (5-12 words), '
        'e.g. for Remake: "Place new remake order", "Ask remake policy/eligibility", '
        '"Check remake status"\n'
        '- "tertiary_reason": finest useful slice (5-15 words) — what the caller was '
        "trying to accomplish in this call (optional, null if not distinct)\n"
        '- "reduction_hint": one sentence on what product/process/self-service change '
        "could prevent similar calls\n\n"
        "Use consistent, title-case phrasing. Do not invent facts not supported by the transcript."
    )


def parse_transcript_reason_analysis(content: str) -> TranscriptReasonAnalysis:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TranscriptClassificationError("LLM response was not valid JSON") from exc

    if not isinstance(parsed, dict):
        raise TranscriptClassificationError("Expected a JSON object")

    summary = _require_string(parsed, "transcript_summary")
    primary = _require_string(parsed, "primary_reason")
    secondary = _require_string(parsed, "secondary_reason")
    tertiary_raw = parsed.get("tertiary_reason")
    tertiary = str(tertiary_raw).strip() if tertiary_raw else None
    if tertiary and tertiary.lower() in ("null", "none", "n/a"):
        tertiary = None

    hint_raw = parsed.get("reduction_hint")
    reduction_hint = str(hint_raw).strip() if hint_raw else None

    return TranscriptReasonAnalysis(
        transcript_summary=summary,
        primary_reason=primary,
        secondary_reason=secondary,
        tertiary_reason=tertiary,
        reduction_hint=reduction_hint,
    )


def _require_string(parsed: dict, key: str) -> str:
    value = parsed.get(key)
    if not value or not str(value).strip():
        raise TranscriptClassificationError(f"Missing or empty field: {key}")
    return " ".join(str(value).split())


def classify_transcript(
    *,
    transcript_text: str,
    segment_summary: str | None,
    client_sentiment: str | None,
    skill_name: str | None,
    agent_name: str | None,
    api_key: str,
    model: str,
    base_url: str,
    timeout_seconds: float,
    max_transcript_chars: int,
) -> TranscriptReasonAnalysis:
    transcript = transcript_text.strip()
    if not transcript:
        raise TranscriptClassificationError("Empty transcript")

    prompt = _build_classification_prompt(
        transcript=truncate_text(transcript, max_transcript_chars),
        segment_summary=(
            truncate_text(segment_summary, 600) if segment_summary else None
        ),
        client_sentiment=client_sentiment,
        skill_name=skill_name,
        agent_name=agent_name,
    )
    content = chat_completion_text(
        prompt=prompt,
        system_prompt=_SYSTEM_PROMPT,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        temperature=0.2,
    )
    return parse_transcript_reason_analysis(content)


def reason_keys(analysis: TranscriptReasonAnalysis) -> tuple[str, str, str | None]:
    return (
        normalize_reason_key(analysis.primary_reason),
        normalize_reason_key(analysis.secondary_reason),
        normalize_reason_key(analysis.tertiary_reason)
        if analysis.tertiary_reason
        else None,
    )
