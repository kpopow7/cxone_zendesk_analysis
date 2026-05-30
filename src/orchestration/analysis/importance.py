from __future__ import annotations

from orchestration.analysis.reasons import (
    InteractionSlice,
    ReasonBucket,
    is_high_priority,
    is_negative_sentiment,
)


def score_reason_bucket(
    *,
    count: int,
    total: int,
    negative_sentiment_count: int,
    high_priority_count: int,
) -> float:
    """0–100 score blending volume, negative sentiment, and ticket priority."""
    if total <= 0 or count <= 0:
        return 0.0

    volume_share = count / total
    negative_rate = negative_sentiment_count / count
    priority_rate = high_priority_count / count

    # Volume dominates; sentiment/priority boost issues that are large *and* painful.
    volume_component = min(volume_share / 0.20, 1.0) * 70.0
    sentiment_component = negative_rate * 15.0 if count >= 5 else negative_rate * 5.0
    priority_component = priority_rate * 15.0
    raw = volume_component + sentiment_component + priority_component
    return round(min(raw, 100.0), 1)


def build_reason_buckets(
    slices: list[InteractionSlice],
    *,
    total_interactions: int,
    top_n: int,
) -> list[ReasonBucket]:
    grouped: dict[str, list[InteractionSlice]] = {}
    for item in slices:
        grouped.setdefault(item.call_reason_key, []).append(item)

    buckets: list[ReasonBucket] = []
    for reason_key, items in grouped.items():
        count = len(items)
        display = max((i.call_reason for i in items), key=len)
        negative = sum(
            1
            for i in items
            if is_negative_sentiment(i.client_sentiment)
            or is_negative_sentiment(i.segment_summary)
        )
        high_priority = sum(1 for i in items if is_high_priority(i.ticket_priority))

        source_counts: dict[str, int] = {}
        for i in items:
            key = i.call_reason_source or "unknown"
            source_counts[key] = source_counts.get(key, 0) + 1

        skill_counts: dict[str, int] = {}
        for i in items:
            if i.skill_name:
                skill_counts[i.skill_name] = skill_counts.get(i.skill_name, 0) + 1

        subjects: list[str] = []
        summaries: list[str] = []
        sample_segment_ids: list[str] = []
        for i in items:
            if i.ticket_subject and i.ticket_subject not in subjects:
                subjects.append(i.ticket_subject)
            if i.segment_summary and i.segment_summary not in summaries:
                summaries.append(i.segment_summary)
            if i.transcript_text.strip() and i.segment_id not in sample_segment_ids:
                sample_segment_ids.append(i.segment_id)

        bucket = ReasonBucket(
            reason_key=reason_key,
            reason=display,
            count=count,
            share_pct=round(100.0 * count / total_interactions, 1) if total_interactions else 0.0,
            importance_score=score_reason_bucket(
                count=count,
                total=total_interactions,
                negative_sentiment_count=negative,
                high_priority_count=high_priority,
            ),
            negative_sentiment_pct=round(100.0 * negative / count, 1) if count else 0.0,
            high_priority_pct=round(100.0 * high_priority / count, 1) if count else 0.0,
            top_skills=sorted(skill_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:5],
            sample_subjects=subjects[:3],
            sample_summaries=summaries[:3],
            sample_segment_ids=sample_segment_ids,
        )
        bucket.source_field_counts.update(source_counts)
        buckets.append(bucket)

    buckets.sort(key=lambda b: (-b.count, -b.importance_score, b.reason))
    return buckets[:top_n]
