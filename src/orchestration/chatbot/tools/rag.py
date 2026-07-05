from __future__ import annotations

from typing import Any

from orchestration.analysis.reason_taxonomy import ReasonTaxonomy
from orchestration.chatbot.settings import ChatbotSettings
from orchestration.rag.filters import RetrievalFilters, extract_retrieval_filters
from orchestration.rag.retrieve import RetrievedChunk, format_chunks_for_llm, retrieve_knowledge_chunks
from sqlalchemy.engine import Engine


def search_interactions(
    *,
    engine: Engine,
    settings: ChatbotSettings,
    question: str,
    embed_query: str | None = None,
    known_skills: list[str] | None = None,
    taxonomy: ReasonTaxonomy | None = None,
    skill_name: str | None = None,
    canonical_reason: str | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    """Semantic search over call interaction narratives (RAG)."""
    if not settings.openai_api_key:
        return {"error": "OPENAI_API_KEY required for semantic search.", "chunks": []}

    filters: RetrievalFilters | None = None
    if settings.chatbot_rag_filters_enabled:
        filters = extract_retrieval_filters(
            question,
            known_skills=known_skills or [],
            taxonomy=taxonomy,
        )
        if skill_name:
            filters = RetrievalFilters(
                skill_name=skill_name,
                canonical_reason=filters.canonical_reason if filters else None,
                start=filters.start if filters else None,
                end=filters.end if filters else None,
            )
        if canonical_reason:
            filters = RetrievalFilters(
                skill_name=filters.skill_name if filters else skill_name,
                canonical_reason=canonical_reason,
                start=filters.start if filters else None,
                end=filters.end if filters else None,
            )

    try:
        chunks = retrieve_knowledge_chunks(
            engine,
            question,
            api_key=settings.openai_api_key,
            embedding_model=settings.openai_embedding_model,
            openai_base_url=settings.openai_base_url,
            top_k=top_k or settings.chatbot_rag_top_k,
            min_similarity=settings.chatbot_rag_min_similarity,
            timeout_seconds=settings.request_timeout_seconds,
            embed_query=embed_query,
            filters=filters if filters and filters.has_any else None,
        )
    except Exception as exc:
        return {"error": str(exc), "chunks": []}

    return {
        "chunk_count": len(chunks),
        "filters_applied": filters.describe() if filters and filters.has_any else None,
        "chunks": [_chunk_payload(c) for c in chunks],
        "formatted": format_chunks_for_llm(chunks),
    }


def _chunk_payload(chunk: RetrievedChunk) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "skill_name": chunk.skill_name,
        "primary_reason": chunk.primary_reason,
        "secondary_reason": chunk.secondary_reason,
        "interaction_start": str(chunk.interaction_start) if chunk.interaction_start else None,
        "similarity": round(chunk.similarity, 3),
        "excerpt": chunk.content[:600],
    }
