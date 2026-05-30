from __future__ import annotations

import logging

from orchestration.analysis.config import LlmRecommendationConfig, SummaryAnalysisConfig
from orchestration.analysis.llm_recommendations import (
    LlmRecommendationError,
    build_transcript_samples,
    generate_llm_recommendations,
)
from orchestration.analysis.reasons import InteractionSlice, ReasonBucket, is_negative_sentiment
from orchestration.analysis.recommendations import recommendations_for_reason
from orchestration.config import Settings

logger = logging.getLogger(__name__)


def enrich_buckets_with_llm_recommendations(
    buckets: list[ReasonBucket],
    slices_by_reason_key: dict[str, list[InteractionSlice]],
    *,
    settings: Settings,
    config: SummaryAnalysisConfig,
    use_llm: bool,
) -> dict[str, str | int | bool]:
    """Attach LLM recommendations to top reason buckets when configured and API key is set."""
    meta: dict[str, str | int | bool] = {
        "llm_requested": use_llm,
        "llm_applied": False,
        "llm_reasons_processed": 0,
    }

    if not use_llm:
        return meta

    api_key = settings.openai_api_key
    if not api_key:
        meta["llm_skipped"] = "missing OPENAI_API_KEY"
        return meta

    llm_config = config.llm
    top_buckets = [b for b in buckets if b.reason_key != "(no call reason captured)"][
        : llm_config.top_reasons
    ]

    for bucket in top_buckets:
        reason_slices = slices_by_reason_key.get(bucket.reason_key, [])
        ranked_ids = _rank_segment_ids_for_sampling(reason_slices)
        transcript_by_segment = {s.segment_id: s.transcript_text for s in reason_slices}
        summary_by_segment = {s.segment_id: s.segment_summary for s in reason_slices}
        sentiment_by_segment = {s.segment_id: s.client_sentiment for s in reason_slices}

        samples = build_transcript_samples(
            segment_ids=ranked_ids,
            transcript_by_segment=transcript_by_segment,
            summary_by_segment=summary_by_segment,
            sentiment_by_segment=sentiment_by_segment,
            max_samples=llm_config.transcripts_per_reason,
            max_transcript_chars=llm_config.max_transcript_chars,
            max_summary_chars=llm_config.max_summary_chars,
        )
        if not samples:
            continue

        try:
            llm_recs = generate_llm_recommendations(
                reason=bucket.reason,
                count=bucket.count,
                share_pct=bucket.share_pct,
                samples=samples,
                api_key=api_key,
                model=settings.openai_model,
                base_url=settings.openai_base_url,
                timeout_seconds=settings.request_timeout_seconds,
            )
        except (LlmRecommendationError, Exception) as exc:
            logger.warning("LLM recommendations failed for %s: %s", bucket.reason, exc)
            continue

        if llm_recs:
            bucket.recommendations = llm_recs
            bucket.recommendation_source = "llm"
            meta["llm_applied"] = True
            meta["llm_reasons_processed"] = int(meta["llm_reasons_processed"]) + 1

    return meta


def apply_rule_recommendations(buckets: list[ReasonBucket]) -> None:
    for bucket in buckets:
        if bucket.recommendation_source == "llm" and bucket.recommendations:
            continue
        bucket.recommendations = recommendations_for_reason(bucket.reason)
        bucket.recommendation_source = "rules"


def _rank_segment_ids_for_sampling(slices: list[InteractionSlice]) -> list[str]:
    def sort_key(item: InteractionSlice) -> tuple[int, int, str]:
        negative = int(
            is_negative_sentiment(item.client_sentiment)
            or is_negative_sentiment(item.segment_summary)
        )
        has_transcript = int(bool(item.transcript_text.strip()))
        return (-negative, -has_transcript, item.segment_id)

    return [item.segment_id for item in sorted(slices, key=sort_key)]
