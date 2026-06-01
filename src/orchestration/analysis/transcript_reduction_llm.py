from __future__ import annotations

from dataclasses import dataclass

from orchestration.analysis.llm_client import chat_completion_text, truncate_text
from orchestration.analysis.llm_recommendations import _parse_recommendations


class TranscriptReductionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscriptSampleForReduction:
    segment_id: str
    transcript_excerpt: str
    secondary_reason: str
    tertiary_reason: str | None
    transcript_summary: str


def generate_primary_reason_reductions(
    *,
    primary_reason: str,
    count: int,
    share_pct: float,
    secondary_breakdown: list[tuple[str, int, float]],
    samples: list[TranscriptSampleForReduction],
    api_key: str,
    model: str,
    base_url: str,
    timeout_seconds: float,
) -> list[str]:
    if not samples:
        raise TranscriptReductionError("No samples for reduction recommendations")

    secondary_lines = [
        f"  - {label}: {sub_count} calls ({sub_share:.1f}% of this primary reason)"
        for label, sub_count, sub_share in secondary_breakdown[:8]
    ]
    sample_blocks: list[str] = []
    for index, sample in enumerate(samples, start=1):
        parts = [
            f"Sample {index} (segment {sample.segment_id}):",
            f"Secondary: {sample.secondary_reason}",
        ]
        if sample.tertiary_reason:
            parts.append(f"Tertiary: {sample.tertiary_reason}")
        parts.append(f"Summary: {sample.transcript_summary}")
        parts.append(f"Excerpt:\n{sample.transcript_excerpt}")
        sample_blocks.append("\n".join(parts))

    prompt = (
        "You are a contact-center operations analyst. "
        "Calls were classified from transcripts only (no ticket form data).\n\n"
        f"Primary call reason: {primary_reason}\n"
        f"Volume: {count} calls ({share_pct}% of analyzed transcript volume)\n\n"
        "Secondary reason breakdown:\n"
        + ("\n".join(secondary_lines) if secondary_lines else "  (none)")
        + "\n\n"
        + "\n\n".join(sample_blocks)
        + "\n\n"
        "Propose concrete actions to reduce contacts for this primary reason "
        "(self-service, IVR, website, process, training, product fixes). "
        "Address the most common secondary intents.\n\n"
        "Return ONLY a JSON array of 3 to 5 strings. Each string is one actionable recommendation."
    )

    content = chat_completion_text(
        prompt=prompt,
        system_prompt="Respond with valid JSON only.",
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        temperature=0.3,
    )
    try:
        return _parse_recommendations(content)
    except Exception as exc:
        raise TranscriptReductionError(str(exc)) from exc


def samples_from_analyses(
    *,
    segment_ids: list[str],
    transcript_by_segment: dict[str, str],
    analysis_by_segment: dict[str, object],
    max_samples: int,
    max_transcript_chars: int,
) -> list[TranscriptSampleForReduction]:
    from orchestration.analysis.transcript_reason_llm import TranscriptReasonAnalysis

    samples: list[TranscriptSampleForReduction] = []
    for segment_id in segment_ids:
        transcript = transcript_by_segment.get(segment_id, "").strip()
        analysis = analysis_by_segment.get(segment_id)
        if not transcript or not isinstance(analysis, TranscriptReasonAnalysis):
            continue
        samples.append(
            TranscriptSampleForReduction(
                segment_id=segment_id,
                transcript_excerpt=truncate_text(transcript, max_transcript_chars),
                secondary_reason=analysis.secondary_reason,
                tertiary_reason=analysis.tertiary_reason,
                transcript_summary=truncate_text(analysis.transcript_summary, 400),
            )
        )
        if len(samples) >= max_samples:
            break
    return samples
