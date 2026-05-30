from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class TranscriptSample:
    segment_id: str
    excerpt: str
    segment_summary: str | None = None
    client_sentiment: str | None = None


class LlmRecommendationError(RuntimeError):
    pass


def _truncate(text: str, max_chars: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 3].rstrip() + "..."


def _build_prompt(
    *,
    reason: str,
    count: int,
    share_pct: float,
    samples: list[TranscriptSample],
) -> str:
    sample_blocks: list[str] = []
    for index, sample in enumerate(samples, start=1):
        parts = [f"Sample {index} (segment {sample.segment_id}):"]
        if sample.client_sentiment:
            parts.append(f"Sentiment: {sample.client_sentiment}")
        if sample.segment_summary:
            parts.append(f"Summary: {sample.segment_summary}")
        parts.append(f"Transcript excerpt:\n{sample.excerpt}")
        sample_blocks.append("\n".join(parts))

    return (
        "You are a contact-center operations analyst. "
        "Given call reason metrics and transcript excerpts, propose concrete actions "
        "to reduce repeat contacts (self-service, process, training, product fixes).\n\n"
        f"Call reason: {reason}\n"
        f"Volume: {count} calls ({share_pct}% of analyzed volume in this period)\n\n"
        + "\n\n".join(sample_blocks)
        + "\n\n"
        "Return ONLY a JSON array of 3 to 5 strings. Each string is one actionable recommendation. "
        "No markdown, no preamble."
    )


def _parse_recommendations(content: str) -> list[str]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        lines = [
            line.strip(" -\t")
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("{")
        ]
        return [line for line in lines if len(line) > 10][:5]

    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()][:5]

    if isinstance(parsed, dict):
        for key in ("recommendations", "items", "actions"):
            value = parsed.get(key)
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()][:5]

    raise LlmRecommendationError("Could not parse LLM response as a recommendation list")


def generate_llm_recommendations(
    *,
    reason: str,
    count: int,
    share_pct: float,
    samples: list[TranscriptSample],
    api_key: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    timeout_seconds: float = 90.0,
) -> list[str]:
    if not samples:
        raise LlmRecommendationError("No transcript samples available for LLM recommendations")

    prompt = _build_prompt(
        reason=reason,
        count=count,
        share_pct=share_pct,
        samples=samples,
    )
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {
                "role": "system",
                "content": "Respond with valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
    }

    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        body = response.json()

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmRecommendationError("Unexpected LLM API response shape") from exc

    return _parse_recommendations(str(content))


def build_transcript_samples(
    *,
    segment_ids: list[str],
    transcript_by_segment: dict[str, str],
    summary_by_segment: dict[str, str | None],
    sentiment_by_segment: dict[str, str | None],
    max_samples: int,
    max_transcript_chars: int,
    max_summary_chars: int,
) -> list[TranscriptSample]:
    samples: list[TranscriptSample] = []
    for segment_id in segment_ids:
        transcript = transcript_by_segment.get(segment_id, "").strip()
        if not transcript:
            continue
        summary = summary_by_segment.get(segment_id)
        samples.append(
            TranscriptSample(
                segment_id=segment_id,
                excerpt=_truncate(transcript, max_transcript_chars),
                segment_summary=(
                    _truncate(summary, max_summary_chars) if summary else None
                ),
                client_sentiment=sentiment_by_segment.get(segment_id),
            )
        )
        if len(samples) >= max_samples:
            break
    return samples
