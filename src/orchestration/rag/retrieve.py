from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from orchestration.rag.embeddings import embed_texts, vector_literal


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    content: str
    metadata: dict[str, Any]
    skill_name: str | None
    primary_reason: str | None
    secondary_reason: str | None
    interaction_start: object | None
    similarity: float


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
) -> list[RetrievedChunk]:
    query_vector = embed_texts(
        [question],
        api_key=api_key,
        model=embedding_model,
        base_url=openai_base_url,
        timeout_seconds=timeout_seconds,
    )[0]

    with engine.connect() as connection:
        result = connection.execute(
            text(
                """
                SELECT
                    chunk_id,
                    content,
                    metadata,
                    skill_name,
                    primary_reason,
                    secondary_reason,
                    interaction_start,
                    1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM analytics_knowledge_chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :top_k
                """
            ),
            {
                "embedding": vector_literal(query_vector),
                "top_k": top_k,
            },
        )
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
                skill_name=row["skill_name"],
                primary_reason=row["primary_reason"],
                secondary_reason=row["secondary_reason"],
                interaction_start=row["interaction_start"],
                similarity=similarity,
            )
        )
    return chunks


def format_chunks_for_llm(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "(no relevant call examples found)"

    blocks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        header = (
            f"Example {index} (segment {chunk.chunk_id}, "
            f"similarity {chunk.similarity:.2f})"
        )
        if chunk.primary_reason:
            header += f" — {chunk.primary_reason}"
        blocks.append(f"{header}\n{chunk.content}")
    return "\n\n---\n\n".join(blocks)
