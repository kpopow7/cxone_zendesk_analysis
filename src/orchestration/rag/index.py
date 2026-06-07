from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from orchestration.db.knowledge_schema import ensure_knowledge_schema
from orchestration.rag.documents import KnowledgeDocument, build_call_interaction_document, metadata_json
from orchestration.rag.embeddings import embed_texts, vector_literal


SOURCE_QUERY = """
SELECT
    COALESCE(s.segment_id, i.segment_id) AS segment_id,
    COALESCE(s.interaction_start, i.interaction_start) AS interaction_start,
    COALESCE(s.call_direction, i.call_direction) AS call_direction,
    COALESCE(s.media_type, i.media_type) AS media_type,
    COALESCE(s.skill_name, i.skill_name) AS skill_name,
    COALESCE(s.team_name, i.team_name) AS team_name,
    COALESCE(s.agent_name, i.agent_name) AS agent_name,
    COALESCE(s.client_sentiment, i.client_sentiment) AS client_sentiment,
    s.transcript_summary,
    s.primary_reason,
    s.secondary_reason,
    s.tertiary_reason,
    s.reduction_hint,
    s.transcript_preview,
    i.segment_summary,
    i.ticket_id,
    i.ticket_subject,
    i.ticket_description,
    i.ticket_status,
    i.ticket_priority,
    i.call_reason,
    i.disposition_label
FROM analytics_transcript_summaries s
FULL OUTER JOIN analytics_interactions i ON i.segment_id = s.segment_id
WHERE COALESCE(s.segment_id, i.segment_id) IS NOT NULL
  AND (
    NULLIF(trim(COALESCE(s.transcript_summary, '')), '') IS NOT NULL
    OR NULLIF(trim(COALESCE(s.transcript_preview, '')), '') IS NOT NULL
    OR NULLIF(trim(COALESCE(i.ticket_subject, '')), '') IS NOT NULL
    OR NULLIF(trim(COALESCE(i.segment_summary, '')), '') IS NOT NULL
  )
"""


@dataclass(frozen=True)
class IndexBuildResult:
    candidates: int
    embedded: int
    skipped_unchanged: int
    errors: int


def build_knowledge_index(
    engine: Engine,
    *,
    api_key: str,
    embedding_model: str,
    openai_base_url: str,
    start: datetime | None = None,
    end: datetime | None = None,
    batch_size: int = 32,
    limit: int | None = None,
    timeout_seconds: float = 90.0,
) -> IndexBuildResult:
    ensure_knowledge_schema(engine, required=True)

    rows = _fetch_source_rows(engine, start=start, end=end, limit=limit)
    documents: list[KnowledgeDocument] = []
    for row in rows:
        document = build_call_interaction_document(dict(row))
        if document is not None:
            documents.append(document)

    existing_hashes = _load_existing_hashes(engine, [doc.chunk_id for doc in documents])
    to_embed: list[KnowledgeDocument] = [
        doc for doc in documents if existing_hashes.get(doc.chunk_id) != doc.content_hash
    ]
    skipped = len(documents) - len(to_embed)
    embedded = 0
    errors = 0

    for offset in range(0, len(to_embed), batch_size):
        batch = to_embed[offset : offset + batch_size]
        try:
            vectors = embed_texts(
                [doc.content for doc in batch],
                api_key=api_key,
                model=embedding_model,
                base_url=openai_base_url,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            errors += len(batch)
            continue

        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            for document, vector in zip(batch, vectors, strict=True):
                connection.execute(
                    text(
                        """
                        INSERT INTO analytics_knowledge_chunks (
                            chunk_id, source_type, source_id, interaction_start,
                            skill_name, primary_reason, secondary_reason,
                            content, content_hash, metadata, embedding, embedded_at, updated_at
                        ) VALUES (
                            :chunk_id, :source_type, :source_id, :interaction_start,
                            :skill_name, :primary_reason, :secondary_reason,
                            :content, :content_hash, CAST(:metadata AS jsonb),
                            CAST(:embedding AS vector), :embedded_at, :embedded_at
                        )
                        ON CONFLICT (chunk_id) DO UPDATE SET
                            source_type = EXCLUDED.source_type,
                            source_id = EXCLUDED.source_id,
                            interaction_start = EXCLUDED.interaction_start,
                            skill_name = EXCLUDED.skill_name,
                            primary_reason = EXCLUDED.primary_reason,
                            secondary_reason = EXCLUDED.secondary_reason,
                            content = EXCLUDED.content,
                            content_hash = EXCLUDED.content_hash,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding,
                            embedded_at = EXCLUDED.embedded_at,
                            updated_at = EXCLUDED.updated_at
                        """
                    ),
                    {
                        "chunk_id": document.chunk_id,
                        "source_type": document.source_type,
                        "source_id": document.source_id,
                        "interaction_start": document.interaction_start,
                        "skill_name": document.skill_name,
                        "primary_reason": document.primary_reason,
                        "secondary_reason": document.secondary_reason,
                        "content": document.content,
                        "content_hash": document.content_hash,
                        "metadata": metadata_json(document),
                        "embedding": vector_literal(vector),
                        "embedded_at": now,
                    },
                )
                embedded += 1

    if embedded >= 100:
        ensure_knowledge_schema(engine, create_vector_index=True)

    return IndexBuildResult(
        candidates=len(documents),
        embedded=embedded,
        skipped_unchanged=skipped,
        errors=errors,
    )


def _fetch_source_rows(
    engine: Engine,
    *,
    start: datetime | None,
    end: datetime | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    query = SOURCE_QUERY
    params: dict[str, Any] = {}
    filters: list[str] = []

    if start is not None:
        filters.append("COALESCE(s.interaction_start, i.interaction_start) >= :start")
        params["start"] = start
    if end is not None:
        filters.append("COALESCE(s.interaction_start, i.interaction_start) <= :end")
        params["end"] = end
    if filters:
        query += " AND " + " AND ".join(filters)
    query += " ORDER BY COALESCE(s.interaction_start, i.interaction_start) DESC NULLS LAST"
    if limit is not None:
        query += " LIMIT :limit"
        params["limit"] = limit

    with engine.connect() as connection:
        result = connection.execute(text(query), params)
        return [dict(row) for row in result.mappings().all()]


def _load_existing_hashes(engine: Engine, chunk_ids: list[str]) -> dict[str, str]:
    if not chunk_ids:
        return {}
    hashes: dict[str, str] = {}
    batch_size = 500
    for offset in range(0, len(chunk_ids), batch_size):
        batch = chunk_ids[offset : offset + batch_size]
        with engine.connect() as connection:
            result = connection.execute(
                text(
                    """
                    SELECT chunk_id, content_hash
                    FROM analytics_knowledge_chunks
                    WHERE chunk_id = ANY(:chunk_ids)
                    """
                ),
                {"chunk_ids": batch},
            )
            for row in result:
                hashes[str(row.chunk_id)] = str(row.content_hash)
    return hashes
