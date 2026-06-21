from __future__ import annotations

import logging

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql import text

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 1536

KNOWLEDGE_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector"

KNOWLEDGE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS analytics_knowledge_chunks (
    chunk_id VARCHAR(255) PRIMARY KEY,
    source_type VARCHAR(64) NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    interaction_start TIMESTAMPTZ,
    skill_name VARCHAR(512),
    primary_reason VARCHAR(512),
    secondary_reason VARCHAR(512),
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    embedding vector({EMBEDDING_DIMENSIONS}),
    embedded_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

KNOWLEDGE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_interaction_start
ON analytics_knowledge_chunks (interaction_start DESC)
"""

KNOWLEDGE_VECTOR_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding
ON analytics_knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100)
"""

# Building an ivfflat index needs more memory than the default maintenance_work_mem
# (often 64 MB) allows. Raise it for the index-build transaction only (session-scoped
# SET LOCAL — never changes the server-wide setting).
VECTOR_INDEX_MAINTENANCE_WORK_MEM = "256MB"


class KnowledgeSchemaError(RuntimeError):
    pass


def pgvector_available(engine: Engine) -> bool:
    """Return True if the pgvector extension can be installed on this Postgres instance."""
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
            ).first()
            return row is not None
    except DBAPIError:
        return False


def ensure_knowledge_schema(
    engine: Engine,
    *,
    create_vector_index: bool = False,
    required: bool = False,
) -> bool:
    """Create pgvector extension and analytics_knowledge_chunks table.

    Returns True when the knowledge table is ready. When pgvector is unavailable and
    ``required`` is False, returns False without raising (safe for sync/init_db).
    """
    if not pgvector_available(engine):
        message = (
            "pgvector extension is not available on this PostgreSQL instance. "
            "RAG indexing requires pgvector.\n\n"
            "Local Docker: recreate the DB container with the pgvector image:\n"
            "  docker compose down\n"
            "  docker compose up -d\n"
            "(docker-compose.yml uses pgvector/pgvector:pg16)\n\n"
            "Railway: enable pgvector on your Postgres service, then run "
            "CREATE EXTENSION IF NOT EXISTS vector; in the Query tab."
        )
        if required:
            raise KnowledgeSchemaError(message)
        logger.info("Skipping knowledge schema setup (pgvector not available).")
        return False

    with engine.begin() as connection:
        connection.execute(text(KNOWLEDGE_EXTENSION_SQL))
        connection.execute(text(KNOWLEDGE_TABLE_SQL))
        connection.execute(text(KNOWLEDGE_INDEX_SQL))

    if create_vector_index and _knowledge_row_count(engine) >= 100:
        _create_vector_index(engine)

    return True


def _create_vector_index(engine: Engine) -> None:
    """Create the ivfflat embedding index, raising maintenance_work_mem for the build.

    SET LOCAL applies only to this transaction, so the server-wide configuration is
    untouched. If the instance refuses the requested value (or the build still runs
    out of memory) the original error is re-raised with actionable guidance.
    """
    try:
        with engine.begin() as connection:
            connection.execute(
                text(f"SET LOCAL maintenance_work_mem = '{VECTOR_INDEX_MAINTENANCE_WORK_MEM}'")
            )
            connection.execute(text(KNOWLEDGE_VECTOR_INDEX_SQL))
    except DBAPIError as exc:
        if "maintenance_work_mem" in str(exc).lower():
            raise KnowledgeSchemaError(
                "Building the pgvector ivfflat index ran out of memory even after "
                f"raising maintenance_work_mem to {VECTOR_INDEX_MAINTENANCE_WORK_MEM} "
                "for the build.\n\n"
                "Options:\n"
                "  - Increase maintenance_work_mem on the Postgres instance "
                "(e.g. ALTER SYSTEM SET maintenance_work_mem = '256MB'; then reload), or\n"
                "  - Raise VECTOR_INDEX_MAINTENANCE_WORK_MEM in "
                "src/orchestration/db/knowledge_schema.py, or\n"
                "  - Lower the ivfflat 'lists' value in KNOWLEDGE_VECTOR_INDEX_SQL.\n\n"
                "Note: embeddings are already stored; the index is an optional "
                "performance optimization for similarity search."
            ) from exc
        raise


def _knowledge_row_count(engine: Engine) -> int:
    inspector = inspect(engine)
    if "analytics_knowledge_chunks" not in inspector.get_table_names():
        return 0
    with engine.connect() as connection:
        result = connection.execute(text("SELECT COUNT(*) FROM analytics_knowledge_chunks"))
        return int(result.scalar_one())
