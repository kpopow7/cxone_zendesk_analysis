from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from orchestration.analysis.reason_taxonomy import reason_key_sql
from orchestration.rag.embeddings import embed_texts, vector_literal
from orchestration.rag.filters import RetrievalFilters


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    content: str
    metadata: dict[str, Any]
    source_type: str | None
    skill_name: str | None
    primary_reason: str | None
    secondary_reason: str | None
    interaction_start: object | None
    similarity: float


def _build_filter_clause(
    filters: RetrievalFilters | None,
    *,
    source_type: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Return (extra_joins, where_predicates, params) for the given filters.

    The canonical-reason filter joins the taxonomy map so "remake" matches every phrasing that
    rolls up to the same canonical label, not just the literal word.
    """
    if (filters is None or not filters.has_any) and not source_type:
        return "", "", {}

    joins: list[str] = []
    predicates: list[str] = []
    params: dict[str, Any] = {}

    if source_type:
        predicates.append("k.source_type = :flt_source_type")
        params["flt_source_type"] = source_type

    if filters is None:
        extra_joins = ("\n" + "\n".join(joins)) if joins else ""
        where_predicates = (" AND " + " AND ".join(predicates)) if predicates else ""
        return extra_joins, where_predicates, params

    if filters.skill_name:
        predicates.append("k.skill_name ILIKE :flt_skill")
        params["flt_skill"] = f"%{filters.skill_name}%"
    if filters.start is not None:
        predicates.append("k.interaction_start >= :flt_start")
        params["flt_start"] = filters.start
    if filters.end is not None:
        predicates.append("k.interaction_start <= :flt_end")
        params["flt_end"] = filters.end
    if filters.canonical_reason:
        joins.append(
            "LEFT JOIN analytics_reason_taxonomy AS rt "
            f"ON rt.reason_key = {reason_key_sql('k.primary_reason')}"
        )
        predicates.append("rt.canonical_reason = :flt_reason")
        params["flt_reason"] = filters.canonical_reason

    extra_joins = ("\n" + "\n".join(joins)) if joins else ""
    where_predicates = (" AND " + " AND ".join(predicates)) if predicates else ""
    return extra_joins, where_predicates, params


def retrieve_knowledge_chunks(
    engine: Engine,
    question: str,
    *,
    api_key: str,
    embedding_model: str,
    openai_base_url: str,
    top_k: int = 8,
    min_similarity: float = 0.30,
    timeout_seconds: float = 90.0,
    embed_query: str | None = None,
    filters: RetrievalFilters | None = None,
    source_type: str | None = None,
    fallback_to_unfiltered: bool = True,
) -> list[RetrievedChunk]:
    # embed_query lets the caller include conversation context so follow-up
    # questions ("what about outbound?") still match the established topic.
    text_to_embed = embed_query if embed_query and embed_query.strip() else question
    query_vector = embed_texts(
        [text_to_embed],
        api_key=api_key,
        model=embedding_model,
        base_url=openai_base_url,
        timeout_seconds=timeout_seconds,
    )[0]
    embedding = vector_literal(query_vector)

    chunks = _run_retrieval(
        engine,
        embedding=embedding,
        top_k=top_k,
        min_similarity=min_similarity,
        filters=filters,
        source_type=source_type,
    )
    # If structured filters were too strict (e.g. a skill name the user phrased loosely), fall
    # back to pure similarity rather than returning nothing.
    if not chunks and fallback_to_unfiltered and (filters is not None and filters.has_any):
        chunks = _run_retrieval(
            engine,
            embedding=embedding,
            top_k=top_k,
            min_similarity=min_similarity,
            filters=None,
            source_type=source_type,
        )
    return chunks


def _run_retrieval(
    engine: Engine,
    *,
    embedding: str,
    top_k: int,
    min_similarity: float,
    filters: RetrievalFilters | None,
    source_type: str | None = None,
) -> list[RetrievedChunk]:
    extra_joins, where_predicates, filter_params = _build_filter_clause(
        filters, source_type=source_type
    )
    query = f"""
        SELECT
            k.chunk_id,
            k.content,
            k.metadata,
            k.source_type,
            k.skill_name,
            k.primary_reason,
            k.secondary_reason,
            k.interaction_start,
            1 - (k.embedding <=> CAST(:embedding AS vector)) AS similarity
        FROM analytics_knowledge_chunks AS k{extra_joins}
        WHERE k.embedding IS NOT NULL{where_predicates}
        ORDER BY k.embedding <=> CAST(:embedding AS vector)
        LIMIT :top_k
    """
    params: dict[str, Any] = {"embedding": embedding, "top_k": top_k}
    params.update(filter_params)

    with engine.connect() as connection:
        result = connection.execute(text(query), params)
        rows = result.mappings().all()

    chunks: list[RetrievedChunk] = []
    for row in rows:
        similarity = float(row["similarity"] or 0.0)
        if similarity < min_similarity:
            continue
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
        chunks.append(
            RetrievedChunk(
                chunk_id=str(row["chunk_id"]),
                content=str(row["content"]),
                metadata=metadata,
                source_type=_optional_str(row.get("source_type")),
                skill_name=row["skill_name"],
                primary_reason=row["primary_reason"],
                secondary_reason=row["secondary_reason"],
                interaction_start=row["interaction_start"],
                similarity=similarity,
            )
        )
    return chunks


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def format_chunks_for_llm(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "(no relevant examples found)"

    blocks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        source_label = chunk.source_type or "unknown"
        if source_label == "zendesk_ticket":
            ticket_id = chunk.metadata.get("ticket_id")
            header = (
                f"Example {index} (Zendesk ticket {ticket_id or chunk.chunk_id}, "
                f"similarity {chunk.similarity:.2f})"
            )
        else:
            header = (
                f"Example {index} (call segment {chunk.chunk_id}, "
                f"similarity {chunk.similarity:.2f})"
            )
        if chunk.primary_reason:
            header += f" — {chunk.primary_reason}"
        blocks.append(f"{header}\n{chunk.content}")
    return "\n\n---\n\n".join(blocks)
