# RAG knowledge index for the analytics chatbot

The chatbot uses a **hybrid** approach:

| Question type | Method | Example |
|---------------|--------|---------|
| Counts, rankings, trends | **SQL** (text-to-SQL) | “Top 10 call reasons last week” |
| Context, examples, “why” | **RAG** (semantic search) | “Why do customers call about remakes?” |
| Both | **Hybrid** | “How many remake calls last week and what are callers trying to do?” |

## How data is structured for AI search

Each **call** becomes one searchable **knowledge document** in `analytics_knowledge_chunks`:

```
Contact center call interaction
Call time: 2026-05-28 ...
Skill: HD Brite
Transcript primary reason: Remake order
Transcript secondary reason: Place new remake order
Call summary: Customer called to request a remake ...
Zendesk ticket ID: 12345
Ticket subject: Remake request
Zendesk disposition: Order placed
Transcript excerpt: ...
```

Documents are built from:

- **`analytics_transcript_summaries`** — LLM summary + primary/secondary/tertiary reasons
- **`analytics_interactions`** — Zendesk ticket subject, disposition, call_reason, etc.

That narrative text is embedded with OpenAI (`text-embedding-3-small`) and stored in **pgvector** for cosine similarity search.

## Metadata-filtered retrieval

Pure similarity search can surface semantically-similar calls from the wrong skill or month. To
keep retrieval precise, the chatbot first extracts structured filters from the question and
**restricts** the candidate rows before ranking by similarity:

| Signal | Detected from | Applied as |
|--------|---------------|------------|
| **Date window** | "yesterday", "last week", "last 30 days", "this week", "last month" | `interaction_start` range |
| **Skill** | any known CXone `skill_name` mentioned | `skill_name ILIKE` |
| **Canonical reason** | a reason whose taxonomy alias appears (e.g. "remake") | join to `analytics_reason_taxonomy` on the canonical label |

So "remake complaints last week" retrieves remake-canonical calls from the previous calendar week
— not whatever the embedding happens to match. If the filters are too strict and match nothing,
retrieval **falls back** to pure similarity so you always get an answer. Set
`CHATBOT_RAG_FILTERS_ENABLED=false` to disable and use similarity-only retrieval.

## Trend / comparison & drill-down

Two question shapes get a **vetted SQL template** injected into text-to-SQL so the answers are
reliable rather than ad-hoc:

- **Trend / comparison** ("how did remake calls change vs last week?", "week-over-week") → a single
  query returning the current and prior equal-length periods plus `change` and `pct_change`.
- **Drill-down** ("show me the calls behind that number", "which tickets drove the spike") →
  individual rows (segment_id, time, skill, reason, status, summary), not an aggregate.

## Pipeline (recommended order)

```powershell
# 1. Load enriched transcripts + Zendesk + combined dataset (existing steps)
python scripts/run_cxone_historical_backfill.py --start ... --end ...
python scripts/run_zendesk_extract.py --start ... --end ...
python scripts/run_build_combined_dataset.py --rebuild

# 2. Classify transcripts (Step 4b)
python scripts/run_transcript_summary.py --timeframe last-week

# 3. Sync base tables to Railway
python scripts/sync_to_railway.py --tables combined_interactions,cxone_transcripts,cxone_transcript_analysis

# 4. Build knowledge index ON Railway (avoids syncing large embedding vectors from PC)
#    --target-url points the build at Railway; defaults to TARGET_DATABASE_URL if set in the env.
python scripts/build_knowledge_index.py --timeframe last-week --target-url $env:TARGET_DATABASE_URL
```

> **Which database?** `build_knowledge_index.py` writes only to the database it targets, and
> `analytics_knowledge_chunks` is **not** copied by `sync_to_railway.py`. So you must build the
> index directly on each database where you want RAG. Pass `--target-url` (or `--database-url`)
> to choose; without it, the build uses `DATABASE_URL` (local Docker by default). The script
> prints the target host at startup — confirm it shows `*.proxy.rlwy.net` when building on Railway.

## Build / refresh the index

```powershell
# All classified calls with transcript or ticket context
python scripts/build_knowledge_index.py --timeframe all

# Relative windows (UTC)
python scripts/build_knowledge_index.py --timeframe last-week
python scripts/build_knowledge_index.py --timeframe yesterday
python scripts/build_knowledge_index.py --timeframe last-7-days

# Custom date range (filters on interaction_start; --timeframe is ignored)
python scripts/build_knowledge_index.py --start 2026-03-05 --end 2026-03-11
python scripts/build_knowledge_index.py `
  --start 2026-03-05T00:00:00Z `
  --end 2026-03-11T23:59:59Z

# Pilot
python scripts/build_knowledge_index.py --timeframe yesterday --limit 50

# Build on Railway instead of local (--database-url is an alias of --target-url)
python scripts/build_knowledge_index.py --timeframe all --target-url $env:TARGET_DATABASE_URL
```

**Target database:** without `--target-url`/`--database-url`, the build uses `DATABASE_URL`
(local Docker by default). Use the Railway **public** URL (`*.proxy.rlwy.net`, not
`*.railway.internal`) — the script rejects the private hostname and prints the target host at
startup so you can confirm where the rows are going.

Re-run after new transcript summaries are generated. Unchanged documents are skipped via `content_hash`.

## Railway requirements

1. **pgvector** extension — required only for RAG (`build_knowledge_index.py`), **not** for `sync_to_railway.py`
   - Railway Postgres: open Query tab and run `CREATE EXTENSION IF NOT EXISTS vector;`
   - Local Docker: `docker-compose.yml` uses `pgvector/pgvector:pg16` (recreate container if you upgraded from plain postgres)
2. Analytics views must exist (`analytics_transcript_summaries`, `analytics_interactions`)
3. Chatbot env:
   - `CHATBOT_RAG_ENABLED=true` (default)
   - `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`
   - `OPENAI_API_KEY` (same key as chat completions)

## Verify

```sql
SELECT COUNT(*) FROM analytics_knowledge_chunks;
SELECT chunk_id, skill_name, primary_reason, left(content, 120)
FROM analytics_knowledge_chunks
ORDER BY interaction_start DESC
LIMIT 5;
```

## Configuration (.env)

```env
CHATBOT_RAG_ENABLED=true
CHATBOT_RAG_TOP_K=8
CHATBOT_RAG_MIN_SIMILARITY=0.30
CHATBOT_RAG_FILTERS_ENABLED=true
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Conversation memory (follow-up questions build on earlier context)
CHATBOT_MEMORY_ENABLED=true
CHATBOT_MEMORY_MAX_TURNS=6
CHATBOT_MEMORY_SUMMARY_ENABLED=true
```

Set `CHATBOT_RAG_ENABLED=false` to revert to SQL-only mode.

## Conversation memory

The chatbot remembers the **current conversation** so follow-up questions build on
earlier context (e.g. ask "How many remake calls last week?" then "Why are customers
calling about those?"). Memory is per session and resets when the conversation ends
(page reload / new login). It feeds routing, semantic retrieval, SQL generation, and
the final answer.

| Variable | Default | Purpose |
|----------|---------|---------|
| `CHATBOT_MEMORY_ENABLED` | `true` | Master switch. `false` = each question answered with no prior context. |
| `CHATBOT_MEMORY_MAX_TURNS` | `6` | How many recent turns are kept verbatim in prompts. |
| `CHATBOT_MEMORY_SUMMARY_ENABLED` | `true` | Roll older turns into a compact running summary so long chats stay in budget. Adds one short LLM call only when a conversation grows past `CHATBOT_MEMORY_MAX_TURNS`; set `false` to avoid the extra call. |

## Architecture

```
cxone_transcripts + cxone_transcript_analysis + combined_interactions
        ↓
analytics_transcript_summaries + analytics_interactions (views)
        ↓
build_knowledge_index.py  →  narrative documents  →  OpenAI embeddings
        ↓
analytics_knowledge_chunks (pgvector)
        ↓
Chatbot: route question → SQL | RAG | hybrid → answer
```
