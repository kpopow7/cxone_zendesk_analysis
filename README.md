# CXone & Zendesk Data Orchestration

Step-by-step pipeline to extract contact-center data from multiple systems, store it in PostgreSQL, combine it into a unified dataset, and generate insights.

## Pipeline overview

| Step | Source | Table | Date filter | Scripts |
|------|--------|-------|-------------|---------|
| **1** | CXone Interaction Analytics | `cxone_transcripts` | Segment `startTime` (client-side) | `run_cxone_extract.py` (daily), `run_cxone_historical_backfill.py` (one-time), `probe_cxone_ia.py` |
| **2** | Zendesk Support | `zendesk_tickets` | Ticket `created_at` | `run_zendesk_extract.py`, `probe_zendesk.py` |
| **3** | CXone + Zendesk (linked) | `combined_interactions` | CXone `interaction_start` (optional filter) | `run_build_combined_dataset.py` |
| **4** | Combined interactions | (report output) | `interaction_start` presets or custom range | `run_interaction_summary.py` |
| **4b** | CXone transcripts only | `cxone_transcript_analysis` + report | `interaction_start` presets or custom range | `run_transcript_summary.py` |
| **Daily** | All three load steps | Yesterday (configurable TZ) | `run_daily_pipeline.py` ([schedule guide](docs/DAILY_SCHEDULE.md)) |

Both steps use the **same PostgreSQL database** (`DATABASE_URL` in `.env`).

---

## Full pipeline checklist

Use this as a quick reference. **Phase A–B run on local Docker** (`DATABASE_URL`). **Phase C–D push to Railway** (`TARGET_DATABASE_URL`). Details for each step are in the sections below.

### One-time setup

- [ ] Clone repo, create venv, `pip install -r requirements.txt`
- [ ] Copy `.env.example` → `.env` (CXone, Zendesk, `OPENAI_API_KEY`, `DATABASE_URL`)
- [ ] `docker compose up -d` and `python scripts/init_db.py`
- [ ] Copy configs: `zendesk_field_map.json`, `cxone_zendesk_link.json`, `transcript_summary.json` (from `.example` files)
- [ ] Railway: create Postgres + web service; set `TARGET_DATABASE_URL` (public proxy URL) in `.env` — see [docs/CHATBOT_RAILWAY.md](docs/CHATBOT_RAILWAY.md)

### Phase A — Load and combine (local)

Run on **`DATABASE_URL`** (localhost:5433). Order matters for Step 3.

- [ ] **Step 1 — CXone transcripts (with text):** `run_cxone_historical_backfill.py` (one-time; enriched transcripts)
- [ ] **Step 2 — Zendesk tickets:** `run_zendesk_extract.py` (matching date range)
- [ ] **Step 3 — Build combined dataset:** `run_build_combined_dataset.py --rebuild` (use `--batch-size 25` if OOM; optional `--interaction-start` / `--interaction-end`)

Verify locally:

```powershell
docker exec -it cxone_zendesk_postgres psql -U orchestration -d orchestration -c "
SELECT (SELECT COUNT(*) FROM cxone_transcripts) AS cxone,
       (SELECT COUNT(*) FROM zendesk_tickets) AS zendesk,
       (SELECT COUNT(*) FROM combined_interactions) AS combined;"
```

### Phase B — LLM analysis (local, optional)

Requires `OPENAI_API_KEY`.

- [ ] **Step 4 — Ticket-field summary (optional):** `run_interaction_summary.py --timeframe last-week`
- [ ] **Step 4b — Transcript LLM reasons:** `run_transcript_summary.py --timeframe last-week` (pilot with `--limit 10` first)

### Phase C — Sync to Railway

Run from your PC with **`TARGET_DATABASE_URL`** set. Sync order: lighter tables first; use small batches for large tables.

- [ ] `python scripts/run_zendesk_forms_extract.py` (one-time / when forms change — fetches form names)
- [ ] `python scripts/sync_to_railway.py --tables zendesk_tickets`
- [ ] `python scripts/sync_to_railway.py --tables zendesk_ticket_forms`
- [ ] `python scripts/sync_to_railway.py --tables cxone_transcripts --batch-size 10`
- [ ] `python scripts/sync_to_railway.py --tables combined_interactions --batch-size 5`
- [ ] `python scripts/sync_to_railway.py --tables cxone_transcript_analysis` (after Step 4b)

Re-run sync after daily pipeline updates. `sync_to_railway.py` refreshes analytics views on Railway automatically.

- [ ] Verify parity after syncing: `python scripts/check_sync_parity.py` (compares local vs Railway row counts + date ranges; exits non-zero if anything is out of sync)

### Phase D — RAG + chatbot (Railway)

- [ ] On Railway Postgres → Query: `CREATE EXTENSION IF NOT EXISTS vector;`
- [ ] Build the reason taxonomy map **on Railway DB:** `python scripts/build_reason_taxonomy.py --target-url $env:TARGET_DATABASE_URL` (consolidates free-text reasons into canonical categories)
- [ ] Build knowledge index **on Railway DB:** `python scripts/build_knowledge_index.py --timeframe last-week --target-url $env:TARGET_DATABASE_URL` (the table is not synced — build it directly on Railway) — see [docs/RAG.md](docs/RAG.md)
- [ ] Deploy chatbot (Gradio) with `DATABASE_URL` = Railway **private** URL, `OPENAI_API_KEY`, `CHATBOT_*` auth — [docs/CHATBOT_RAILWAY.md](docs/CHATBOT_RAILWAY.md)

### Daily (ongoing)

- [ ] `python scripts/run_daily_pipeline.py` (CXone list + Zendesk + rebuild combined) — [docs/DAILY_SCHEDULE.md](docs/DAILY_SCHEDULE.md)
- [ ] Re-sync to Railway (`--sync-railway` flag or `sync_to_railway.py`)
- [ ] Re-run Step 4b + `build_knowledge_index.py` on new data when you want fresh RAG answers

**Note:** Syncing cxone/zendesk to Railway *before* Step 3 locally is fine — Step 3 only writes to local Postgres. You must run Step 3 locally before syncing `combined_interactions`.

---

## Shared setup (do once)

```powershell
cd c:\Users\kpopo\cxone_zendesk_analysis
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env — CXone credentials (Step 1) and Zendesk credentials (Step 2)

# Start local Postgres
docker compose up -d

# Create all tables (optional — also runs automatically on first extract)
python scripts/init_db.py
```

Default connection (from `.env.example`):

`postgresql+psycopg://orchestration:orchestration@localhost:5433/orchestration`

Docker maps **host port 5433** → container 5432 so it does not clash with a local PostgreSQL install on 5432.

### Project layout

```
docker-compose.yml
config/
  zendesk_field_map.json.example   # Template for promoted custom fields
  reason_taxonomy.json.example     # Controlled reason vocabulary (canonical categories + aliases)
src/orchestration/
  config.py
  models.py
  db/schema.py                     # cxone_transcripts + zendesk_tickets
  cxone/                           # Step 1
  zendesk/                         # Step 2
  sinks/
  steps/
scripts/
  init_db.py
  run_cxone_extract.py             # Step 1 daily CLI (list API)
  run_cxone_historical_backfill.py # Step 1 one-time enriched backfill
  probe_cxone_ia.py
  run_zendesk_extract.py           # Step 2 CLI
  run_build_combined_dataset.py    # Step 3 CLI
  run_interaction_summary.py       # Step 4 CLI
  run_transcript_summary.py        # Step 4b transcript-only LLM reasons
  run_daily_pipeline.py            # Daily CXone + Zendesk + combined (all-in-one)
  run_daily_pipeline.ps1           # Windows runner with logging
  register_daily_task.ps1          # Register Windows Task Scheduler job
  list_call_selection_values.py    # List skills/teams for filter config
  cxone_zendesk_link.json.example  # Step 3 link rules
  interaction_summary.json.example # Step 4 analysis config
  transcript_summary.json.example  # Step 4b transcript LLM analysis
  disposition_label_map.json.example # Step 4 disposition labels
  generate_disposition_label_map.py  # Scaffold disposition labels from DB
  run_zendesk_forms_extract.py     # Sync Zendesk ticket form names (for grouping/filtering)
  sync_to_railway.py               # Copy tables to Railway Postgres
  check_sync_parity.py             # Verify local vs Railway row counts + dates line up
  build_reason_taxonomy.py         # Canonical reason map (free-text → controlled vocabulary)
  build_knowledge_index.py         # RAG embeddings (pgvector)
  railway_analytics_setup.sql      # Analytics view for chatbot
  probe_zendesk.py
chatbot/
  app.py                           # Gradio chatbot (company login)
  Dockerfile                       # Railway deploy
docs/
  CHATBOT_RAILWAY.md               # Railway DB + chatbot setup
  RAG.md                           # Knowledge index + hybrid chatbot
  DAILY_SCHEDULE.md                # Schedule daily extracts + combined update
```

---

## Step 1: CXone call transcripts → PostgreSQL

This step uses the **NICE CXone Interaction Analytics API** to:

1. Authenticate with OAuth2 (password grant + access key)
2. Discover your tenant API base URL
3. List **analyzed segments** in a date range (`GET …/segments/analyzed`) — batched via `pageSize` + cursor pagination
4. Upsert into PostgreSQL (keyed by `segment_id`). Filter inbound/outbound in SQL (`call_direction`, `media_type`).

### Two load patterns (recommended)

| Job | Script | Enrichment | When to use |
|-----|--------|------------|-------------|
| **Historical (one-time)** | `run_cxone_historical_backfill.py` | Full `analyzed-transcript` per segment (concurrent per list page) | Initial backfill of retention window |
| **Daily (ongoing)** | `run_cxone_extract.py` | List API only (fast) | Scheduled job for yesterday / last 24h |

Both upsert on `segment_id`, so re-running a day is safe. Historical chunks commit after each `--chunk-days` window (default 1 day) so a failed run can resume from the last completed day.

**Performance:** NICE has no bulk transcript API. Historical backfill uses `CXONE_TRANSCRIPT_FETCH_CONCURRENCY` (default 8) per list page. Daily extract avoids per-segment calls entirely.

### Prerequisites

| Requirement | Notes |
|-------------|--------|
| CXone app registration | Back-end app with **Interaction Analytics** API scope ([developer portal](https://developer.niceincontact.com/Documentation/GettingStarted)) |
| API user + access key | Role with Interaction Analytics view permissions |
| Interaction Analytics license | Transcripts come from IA / Transcription Hub |
| `.env` | `CXONE_CLIENT_ID`, `CXONE_CLIENT_SECRET`, `CXONE_ACCESS_KEY_ID`, `CXONE_ACCESS_KEY_SECRET` |

### Database table: `cxone_transcripts`

Created automatically on first run or via `init_db.py`. Key columns:

| Column | Notes |
|--------|--------|
| `segment_id` (PK) | Unique segment identifier |
| `contact_id`, `acd_contact_id`, `acd_session_id`, `contact_no` | Contact identifiers |
| `agent_name`, `team_name`, `skill_name`, `ticket_id` | List payload; richer with `--enrich-transcripts` |
| `interaction_start`, `interaction_end` | Call window |
| `call_direction`, `media_type` | e.g. `IN_BOUND`, `PhoneCall` |
| `client_sentiment`, `agent_sentiment`, `segment_summary` | IA analytics |
| `transcript_text` | Turn-by-turn transcript |
| `raw_metadata` | Full segment + transcript JSON (jsonb) |
| `extracted_at`, `created_at`, `updated_at` | Pipeline timestamps |

### Confirm API paths (important)

CXone API paths can vary by version and tenant. After app registration:

1. Open [Interaction Analytics API](https://developer.niceincontact.com/API/InteractionAnalyticsAPI/) on the developer portal
2. Sign in and use **Try it out** on `GET /segments/analyzed`
3. Copy the full URL path (e.g. `/interaction-analytics-gateway/v2/segments/analyzed`)
4. Set `CXONE_IA_API_PATH` in `.env` to the path **without** `/segments/analyzed`
5. Set `CXONE_IA_DATE_FIELD` (usually `startTime`) and `CXONE_IA_ORDER` (`desc` for recent data first)

### Run Step 1

**One-time historical backfill** (enriched — run once before daily loads):

```powershell
# Full retention window, one calendar day per chunk (re-run safe)
python scripts/run_cxone_historical_backfill.py `
  --start 2024-01-01T00:00:00Z `
  --end 2026-05-27T23:59:59Z

# Wider chunks if rate limits are stable (e.g. 7 days at a time)
python scripts/run_cxone_historical_backfill.py `
  --start 2024-01-01T00:00:00Z `
  --end 2026-05-27T23:59:59Z `
  --chunk-days 7

# Smoke test
python scripts/run_cxone_historical_backfill.py `
  --start 2026-05-20T00:00:00Z `
  --end 2026-05-20T23:59:59Z `
  --limit 5 `
  --dry-run
```

**Daily incremental load** (list API only — schedule after backfill completes):

```powershell
# Yesterday (example for Task Scheduler / cron)
python scripts/run_cxone_extract.py `
  --start 2026-05-26T00:00:00Z `
  --end 2026-05-26T23:59:59Z

# Dry run + JSON export
python scripts/run_cxone_extract.py `
  --start 2026-05-26T00:00:00Z `
  --end 2026-05-26T23:59:59Z `
  --dry-run `
  --json-output output/cxone_transcripts.json
```

Optional: run historical backfill again for a single day to refresh enriched fields on rows already loaded by the daily job (`segment_id` upsert overwrites).

Verify:

```powershell
docker exec -it cxone_zendesk_postgres psql -U orchestration -d orchestration -c "SELECT segment_id, agent_name, skill_name, call_direction, left(transcript_text, 80) FROM cxone_transcripts LIMIT 5;"

# Inbound phone only at query time
docker exec -it cxone_zendesk_postgres psql -U orchestration -d orchestration -c "SELECT * FROM cxone_transcripts WHERE media_type = 'PhoneCall' AND upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%';"
```

### Troubleshooting Step 1 (CXone)

**Symptom:** `HTTPStatusError` **404** on `.../segments/analyzed`

**Fix:** In `.env`:

```env
CXONE_IA_API_PATH=/interaction-analytics-gateway/v2
```

**Symptom:** `Segments extracted: 0` but the API works in the portal

Common causes: wrong response parsing (use current code), date range filtered client-side on `startTime`, cursor pagination with `CXONE_IA_ORDER=desc`, or no analyzed calls in range.

**Debug:**

```powershell
python scripts/probe_cxone_ia.py --no-date-filter
python scripts/probe_cxone_ia.py --start 2026-05-20T00:00:00Z --end 2026-05-20T23:59:59Z
```

| Code | Likely cause |
|------|----------------|
| 401 / 403 | Missing IA scope or API user permissions |
| 404 | Wrong path or segment id |
| 429 | Rate limit — narrow date range; retries are automatic |

---

## Step 2: Zendesk tickets → PostgreSQL

This step uses the **Zendesk Support API** to:

1. Authenticate with API token (`email/token` + token)
2. Load ticket field definitions (`GET /api/v2/ticket_fields`)
3. Search tickets by **`created_at`** in your date range (`GET /api/v2/search.json`)
4. Parse standard fields into columns and custom fields into `custom_fields` jsonb (slug keys)
5. Optionally copy selected custom fields into `promoted_fields` via `config/zendesk_field_map.json`
6. Upsert into PostgreSQL (keyed by `ticket_id`)

### Prerequisites

| Requirement | Notes |
|-------------|--------|
| Zendesk Support access | Admin or agent with API access |
| API token | Admin Center → Apps and integrations → APIs → Zendesk API → add token |
| `.env` | `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN` |

### Environment variables (Step 2)

```env
ZENDESK_SUBDOMAIN=yourcompany
ZENDESK_EMAIL=you@company.com
ZENDESK_API_TOKEN=your_api_token
# Optional:
# ZENDESK_API_BASE_URL=https://yourcompany.zendesk.com
# ZENDESK_FIELD_MAP_PATH=config/zendesk_field_map.json
```

### Step 2a: Probe — discover custom fields

Run this **before** your first full extract to see which custom fields exist and which columns to promote.

```powershell
python scripts/probe_zendesk.py --write-example-map
```

This will:

- Verify authentication (`/api/v2/users/me`)
- Print active custom fields: **field id**, **type**, **suggested column** (`cf_*`), **title**
- Write `output/zendesk_ticket_fields.json` (full catalog)
- Write `config/zendesk_field_map.json.example`

Optional — sample tickets in a date range:

```powershell
python scripts/probe_zendesk.py `
  --start 2026-05-01T00:00:00Z `
  --end 2026-05-08T00:00:00Z
```

### Step 2b: Configure promoted custom fields (optional)

1. Copy the example map:

   ```powershell
   copy config\zendesk_field_map.json.example config\zendesk_field_map.json
   ```

2. Edit `config/zendesk_field_map.json` — keep only fields you care about:

   ```json
   {
     "promoted_fields": [
       { "field_id": 1234567890123, "column": "cf_order_number" },
       { "field_id": 9876543210987, "column": "cf_acd_contact_id" }
     ]
   }
   ```

3. On extract, values are stored in:
   - **`promoted_fields`** jsonb (backup / full promoted set)
   - **Dedicated `cf_*` columns** on `zendesk_tickets` (one column per entry in the field map)

Ensure `config/zendesk_field_map.json` exists (copy from `config/zendesk_field_map.json.example`). The extractor also falls back to the `.example` file if the primary map is missing.

After upgrading, run the column migration once, then re-extract:

```powershell
Get-Content scripts/migrate_zendesk_promoted_columns.sql | docker exec -i cxone_zendesk_postgres psql -U orchestration -d orchestration
python scripts/run_zendesk_extract.py --start 2026-05-20T00:00:00Z --end 2026-05-20T23:59:59Z
```

### Step 2c: Extract and load

```powershell
# Dry run → JSON only
python scripts/run_zendesk_extract.py `
  --start 2026-05-01T00:00:00Z `
  --end 2026-05-08T00:00:00Z `
  --dry-run `
  --json-output output/zendesk_tickets.json

# Load PostgreSQL
python scripts/run_zendesk_extract.py `
  --start 2026-05-01T00:00:00Z `
  --end 2026-05-08T00:00:00Z

# Quick test (first 10 tickets)
python scripts/run_zendesk_extract.py `
  --start 2026-05-06T00:00:00Z `
  --end 2026-05-07T00:00:00Z `
  --limit 10 `
  --dry-run
```

Verify:

```powershell
docker exec -it cxone_zendesk_postgres psql -U orchestration -d orchestration -c "SELECT ticket_id, status, created_at, subject FROM zendesk_tickets ORDER BY created_at DESC LIMIT 5;"

docker exec -it cxone_zendesk_postgres psql -U orchestration -d orchestration -c "SELECT ticket_id, custom_fields, promoted_fields FROM zendesk_tickets LIMIT 3;"
```

### Database table: `zendesk_tickets`

| Column | Notes |
|--------|--------|
| `ticket_id` (PK) | Zendesk ticket id |
| `subject`, `description`, `status`, `priority`, `ticket_type` | Standard fields |
| `tags` | jsonb array |
| `created_at`, `updated_at`, `due_at` | Ticket timestamps (filter uses `created_at`) |
| `requester_id`, `assignee_id`, `organization_id`, `group_id`, … | IDs for joins / enrichment |
| `via_channel`, `recipient`, `external_id`, `url` | Metadata |
| `custom_fields` | All custom values, keys = slugified field titles (jsonb) |
| `promoted_fields` | Subset from `zendesk_field_map.json` (jsonb) |
| `cf_*` columns | Same promoted values as queryable TEXT columns (e.g. `cf_account_number`) |
| `raw_metadata` | Full ticket JSON (jsonb) |
| `extracted_at` | Last pipeline run |
| `row_created_at`, `row_updated_at` | First insert / last upsert |

### Ticket comments (later / optional)

When you’re ready to store ticket conversations, the schema and extractor scaffolding is in place:

- Table: `zendesk_ticket_comments`
- Script: `scripts/run_zendesk_comments_extract.py`

It pulls comments for tickets already loaded into `zendesk_tickets` for the same `created_at` range.

```powershell
# Fast bulk mode (default) — Incremental Ticket Event Export
python scripts/run_zendesk_comments_extract.py `
  --start 2026-05-20T00:00:00Z `
  --end 2026-05-20T23:59:59Z

# Slower but simple: /tickets/{id}/comments.json per ticket in DB
python scripts/run_zendesk_comments_extract.py `
  --start 2026-05-20T00:00:00Z `
  --end 2026-05-20T23:59:59Z `
  --mode per-ticket `
  --limit-tickets 50
```

Incremental mode uses `GET /api/v2/incremental/ticket_events` (no `.json` suffix). Requires **Admin** API access on Zendesk.

### Date range behavior

- Filters on ticket **`created_at`** (not `updated_at`).
- Uses Zendesk **Export Search API** (`/api/v2/search/export.json`) with cursor pagination (no 1,000 ticket cap).
- Search query uses `created>` / `created<` with `YYYY-MM-DD` dates (Zendesk rejects `>=` / `<=`).
- Date range is chunked by day; precise times are applied when building records.

### Troubleshooting Step 2 (Zendesk)

| Symptom | Likely cause | Fix |
|---------|----------------|-----|
| `Missing required environment variables` | `.env` not set | Set `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN` |
| 401 Unauthorized | Bad token or email | Regenerate API token; use `{email}/token` format (handled by client) |
| 403 Forbidden | Role cannot use API | Enable token access for your role |
| 422 / search errors | Invalid query | Check ISO dates; ensure `--end` is after `--start` |
| `Invalid search` / 422 | Bad query syntax | Use `created>YYYY-MM-DD` not `created>=`; export uses `filter[type]=ticket` without `type:` in query |
| Fewer tickets than expected | Range or permissions | Check Zendesk UI with same created dates |
| Slow with `--limit` | High volume day | Fixed in current code (stops export pagination early when limit is reached) |

**Re-run / backfill:** Upserts on `ticket_id` — safe to re-run the same date range to refresh rows.

---

## Step 3: Combined dataset (CXone ↔ Zendesk)

Step 3 links each CXone segment to Zendesk using a **two-ticket model**: a **phone-call form** ticket (bridge) and a **parent** ticket (detail). The combined row stores transcript data from CXone and interaction fields from the **parent** ticket.

### How linking works

```text
CXone segment
  contact_id / contact_no
       ↓
Phone-call Zendesk ticket  (cf_call_object_identifier matches)
       ↓
cf_parent_ticket
       ↓
Parent Zendesk ticket  → subject, disposition, summary, etc. land in combined_interactions
```

Copy and edit the link config (falls back to `.example` if missing):

```powershell
copy config\cxone_zendesk_link.json.example config\cxone_zendesk_link.json
```

**Primary path** (`parent_ticket_resolution`):

| Step | Match |
|------|--------|
| 1 | CXone `contact_id` or `contact_no` → phone ticket `cf_call_object_identifier` |
| 2 | Phone ticket `cf_parent_ticket` → parent `ticket_id` |
| 3 | Promoted fields and ticket columns come from the **parent**; phone ticket ids/fields stored separately |

**Fallbacks** (if the bridge path fails): direct `ticket_id`, then `cf_master_call_identifier`.

Optional: set `phone_call_form_ids` in the link config to restrict bridge tickets to specific Zendesk form ids.

Requires promoted Zendesk columns from `config/zendesk_field_map.json` (must include `cf_call_object_identifier` and `cf_parent_ticket`).

### Run Step 3

```powershell
# Create table (if not already)
python scripts/init_db.py

# Dry run: match stats only
python scripts/run_build_combined_dataset.py --dry-run

# Full rebuild (recommended after historical CXone + Zendesk loads)
python scripts/run_build_combined_dataset.py --rebuild

# Incremental: only segments in a call window
python scripts/run_build_combined_dataset.py `
  --interaction-start 2026-05-26T00:00:00Z `
  --interaction-end 2026-05-26T23:59:59Z

# Analysis-ready subset: matched rows only
python scripts/run_build_combined_dataset.py --matched-only --rebuild
```

### Table: `combined_interactions`

| Column group | Examples |
|--------------|----------|
| Link | `segment_id` (PK), `ticket_id` (parent), `phone_call_ticket_id`, `link_method`, `link_key`, `parent_link_key` |
| Call / CXone | `transcript_text`, `segment_summary`, `agent_name`, `call_direction`, `interaction_start` |
| Ticket / Zendesk (parent) | `ticket_subject`, `ticket_status`, `ticket_description`, `ticket_tags` |
| Custom fields | `zendesk_promoted_fields` (parent); `zendesk_phone_call_fields` (bridge ticket) |
| Normalized Zendesk | `call_reason`, `call_reason_code`, `call_reason_source`, `disposition_label`, `disposition_code`, `disposition_source` |
| Provenance | `built_at`, `cxone_extracted_at`, `zendesk_extracted_at` |

Normalized reason/disposition columns are populated at build time from form-specific Zendesk fields using `config/field_normalization.json` (see `config/field_normalization.json.example`). After upgrading, re-run Step 3 with `--rebuild` and re-sync to Railway.

### Example queries for analysis

```sql
-- Match rate (call_object_to_parent = full bridge → parent path)
SELECT link_method, count(*) FROM combined_interactions GROUP BY 1 ORDER BY 2 DESC;

-- Rows where phone ticket matched but parent is missing from zendesk_tickets
SELECT segment_id, phone_call_ticket_id, parent_link_key
FROM combined_interactions
WHERE link_method = 'call_object_parent_not_loaded';

-- Inbound calls with parent-ticket context
SELECT segment_id, ticket_id, phone_call_ticket_id, ticket_subject, agent_name,
       call_reason, disposition_label,
       left(transcript_text, 200) AS transcript_preview
FROM combined_interactions
WHERE link_method = 'call_object_to_parent'
  AND upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%';

-- Top call reasons (uses normalized column)
SELECT call_reason, COUNT(*) AS n
FROM combined_interactions
WHERE call_reason IS NOT NULL
GROUP BY call_reason
ORDER BY n DESC
LIMIT 10;

-- Export-friendly row for LLM summarization (parent ticket fields)
SELECT segment_id, ticket_id, phone_call_ticket_id, ticket_subject, ticket_description,
       segment_summary, transcript_text, zendesk_promoted_fields
FROM combined_interactions
WHERE link_method = 'call_object_to_parent';
```

### Recommended pipeline order

See **[Full pipeline checklist](#full-pipeline-checklist)** above for the complete local → Railway → RAG flow. Short version:

1. **One-time:** `run_cxone_historical_backfill.py` + `run_zendesk_extract.py` (full range) + `run_build_combined_dataset.py --rebuild`
2. **Daily:** `run_daily_pipeline.py` or scheduled task ([docs/DAILY_SCHEDULE.md](docs/DAILY_SCHEDULE.md))
3. **Optional:** `run_interaction_summary.py`, `run_transcript_summary.py`, Railway sync, RAG index, chatbot

---

## Step 4: Interaction summary (top issues & recommendations)

Step 4 reads `combined_interactions`, ranks **call reasons** by volume and an **importance score** (share of calls, negative CXone sentiment, urgent/high Zendesk priority), and prints actionable recommendations to reduce repeat contacts.

### Configure (optional)

```powershell
copy config\interaction_summary.json.example config\interaction_summary.json
copy config\disposition_label_map.json.example config\disposition_label_map.json
```

Edit `call_reason_fields` to match your Zendesk promoted columns (same fields as `zendesk_field_map.json`). Defaults prefer **reason for contact** fields (not disposition codes used as reasons).

**Disposition labels:** Zendesk disposition values are often internal codes (`dispdealer__ordersupport_product_info`). Map them to readable labels in `config/disposition_label_map.json`. Scaffold from your data:

```powershell
python scripts/generate_disposition_label_map.py --top 50
```

Unmapped codes still get a best-effort label when `fallback_humanize` is true.

**Call selection:** Control which rows are analyzed via the `call_selection` block in config (or CLI flags). Discover available values:

```powershell
python scripts/list_call_selection_values.py --timeframe last-week
```

Example `call_selection` in `config/interaction_summary.json` (legacy `inbound_only` / `matched_link_methods` still work if omitted):

```json
"call_selection": {
  "call_direction": "inbound",
  "skills": ["LEV Consumer", "HD Warranty Support"],
  "skills_exclude": [],
  "teams": [],
  "media_types": ["PhoneCall"],
  "link_methods": ["call_object_to_parent"],
  "include_unmatched": false
}
```

| Setting | Purpose |
|---------|---------|
| `call_direction` | `all`, `inbound`, or `outbound` |
| `skills` / `skills_exclude` | Include or exclude by CXone `skill_name` (case-insensitive) |
| `teams` / `teams_exclude` | Filter by `team_name` |
| `media_types` / `media_types_exclude` | Filter by `media_type` (e.g. `PhoneCall`) |
| `link_methods` | Zendesk link methods to include (default: `call_object_to_parent`) |
| `include_unmatched` | Include segments with no ticket match |
| `top_n` | Number of reasons and dispositions in the report |
| `disposition_label_map_path` | JSON map of disposition code → display label |
| `llm_recommendations` | Optional LLM pass over transcript samples |

### Run Step 4

```powershell
# Previous calendar week (Mon–Sun UTC) — default
python scripts/run_interaction_summary.py --timeframe last-week

# Yesterday only
python scripts/run_interaction_summary.py --timeframe yesterday

# Rolling last 7 days
python scripts/run_interaction_summary.py --timeframe last-7-days

# All data in combined_interactions
python scripts/run_interaction_summary.py --timeframe all

# Custom ISO range (overrides preset bounds)
python scripts/run_interaction_summary.py `
  --start 2026-05-20T00:00:00Z `
  --end 2026-05-27T23:59:59Z

# CLI call selection (overrides config for this run)
python scripts/run_interaction_summary.py --timeframe last-week `
  --call-direction inbound `
  --skill "LEV Consumer" `
  --skill "HD Warranty Support" `
  --media-type PhoneCall

# Outbound only, exclude a skill
python scripts/run_interaction_summary.py --timeframe all `
  --call-direction outbound `
  --exclude-skill "LEV Consumer"

# Export for dashboards
python scripts/run_interaction_summary.py --timeframe last-week `
  --json-output output/interaction_summary.json `
  --markdown-output output/interaction_summary.md

# LLM recommendations from transcript samples (top 5 reasons by default)
# Requires OPENAI_API_KEY in .env (OpenAI-compatible chat completions API)
python scripts/run_interaction_summary.py --timeframe last-week --llm-recommendations
```

The CLI prints a human-readable report. JSON includes `top_call_reasons` (counts, importance, `recommendation_source`, recommendations), `top_dispositions` (with `disposition` label and `disposition_code`), link-method breakdown, `insights`, and `llm` metadata.

**Recommendations:** By default, **rule-based** suggestions from reason text (`src/orchestration/analysis/recommendations.py`). With `--llm-recommendations` (or `llm_recommendations.enabled` in config), the top N reasons use **transcript excerpts** and CXone summaries via the OpenAI API; rule-based text is used when the LLM is off or fails for a bucket.

---

## Step 4b: Transcript-only summary (LLM call reasons)

Step 4b analyzes **`cxone_transcripts` only** (no Zendesk ticket fields). Each call transcript is classified by an LLM into:

| Level | Example (remake) |
|-------|------------------|
| **Primary** | Remake order |
| **Secondary** | Place new remake order / Ask remake policy / Check remake status |
| **Tertiary** | Agent-assisted order entry (optional finer slice) |

Each classified call is stored in **`cxone_transcript_analysis`** (one row per `segment_id`: summary, primary/secondary/tertiary reasons, reduction hint). Re-runs skip already-classified segments when `skip_existing` is true. The report ranks primary reasons, shows secondary and tertiary breakdowns, and suggests actions to **reduce call volume** (LLM or rule-based).

**Chatbot / agent queries:** Per-call rows are exposed as the **`analytics_transcript_summaries`** view (joins analysis + `cxone_transcripts` metadata). The hosted chatbot can query primary reasons, sub-reasons, and `transcript_summary` per call. After classifying locally, sync to Railway:

```powershell
python scripts/sync_to_railway.py --tables cxone_transcripts,cxone_transcript_analysis
```

(`sync_to_railway.py` also refreshes analytics views on the target DB.)

### Reason → outcome linkage (`analytics_reason_outcomes` / `analytics_interaction_outcomes`)

Two views connect **why** customers contacted to **what happened**, so "high volume" becomes "high cost / fixable":

- **`analytics_reason_outcomes`** — pre-aggregated per Zendesk `call_reason`: `call_count`, `distinct_callers`, and resolved / unresolved / escalated / repeat-contact counts and percentages. One query ranks reasons by escalation rate, repeat-caller rate, or unresolved rate.
- **`analytics_interaction_outcomes`** — one row per call segment joining the reason (`call_reason` + transcript `primary/secondary/tertiary_reason`) to its outcome columns: `resolution_status` (`resolved`/`unresolved`/`unknown`), `is_resolved`, `is_open`, `is_escalated`, `is_repeat_contact`, `contact_interaction_count`, `prior_contacts_30d`. Group it by `primary_reason`, `skill_name`, `ticket_form_name`, date, etc. to slice outcomes any way.

Definitions: **resolution** = `ticket_status` solved/closed; **escalation** = high/urgent priority **or** a ticket tag containing "escalat" (a tunable heuristic); **repeat contact** = the same caller phone `contact_no` appears more than once (note: `contact_id` is unique per call and does *not* identify a repeat customer).

Both views are derived entirely from already-synced tables (`combined_interactions` + `cxone_transcript_analysis`) and are (re)created by `ensure_analytics_views` — which runs inside the daily pipeline and `sync_to_railway.py` — so **no extra extract or sync step is required**; a normal sync keeps them current. They are also allow-listed in the chatbot SQL guard and documented in its schema prompt.

```sql
-- Reasons that escalate or generate repeat callers the most (min volume 20)
SELECT call_reason, call_count, escalated_pct, repeat_contact_pct, unresolved_pct
FROM analytics_reason_outcomes
WHERE call_count >= 20
ORDER BY repeat_contact_pct DESC
LIMIT 15;
```

### Controlled reason taxonomy (`analytics_reason_taxonomy` + canonical views)

Both the transcript-LLM reasons (`primary_reason`) and the Zendesk reasons (`call_reason`) are free
text, so "Order status", "order status check", and "Where is my order" rank as three separate
reasons. A small, human-editable vocabulary in `config/reason_taxonomy.json` maps every reason onto
a **canonical category** so rankings are trustworthy. The map is deterministic (no LLM) and stored
in `analytics_reason_taxonomy` (free-text key → canonical label).

```powershell
copy config\reason_taxonomy.json.example config\reason_taxonomy.json   # edit categories/aliases to fit your data
python scripts/build_reason_taxonomy.py                                # build/refresh the map (local)
python scripts/build_reason_taxonomy.py --target-url $env:TARGET_DATABASE_URL   # …or on Railway
```

This adds `*_canonical` columns to the analytics views and two new chatbot views:

- **`analytics_canonical_reason_outcomes`** — same outcome rates as `analytics_reason_outcomes` but
  grouped on the canonical reason. **Prefer this for "top/worst reasons" rankings** — phrasing
  variants are consolidated into one row per category.
- **`analytics_reason_reconciliation`** — how often the Zendesk form reason and the transcript reason
  **agree** once both are mapped to the vocabulary. A low `agree_pct` flags miscategorized tickets.
- **`analytics_reason_mismatches`** — the **specific tickets** behind the disagreements (one row per
  mismatched interaction), so a QA analyst can review and re-tag them.

```sql
-- Consolidated top reasons (not fragmented by phrasing)
SELECT canonical_reason, call_count, escalated_pct, repeat_contact_pct
FROM analytics_canonical_reason_outcomes ORDER BY call_count DESC LIMIT 15;

-- Reasons where agent tagging disagrees most with the call transcript
SELECT call_reason_canonical, comparable_calls, agree_pct, disagree_pct
FROM analytics_reason_reconciliation WHERE comparable_calls >= 20 ORDER BY disagree_pct DESC LIMIT 15;

-- The actual mis-tagged tickets to review (confident mismatches)
SELECT segment_id, ticket_id, tagged_reason_canonical, transcript_reason_canonical, ticket_status
FROM analytics_reason_mismatches
WHERE tagged_reason_canonical <> 'Other / Uncategorized'
  AND transcript_reason_canonical <> 'Other / Uncategorized'
ORDER BY interaction_start DESC LIMIT 25;
```

### Tagging-accuracy QA report (P2)

For a one-shot QA summary of how well agent tags match what calls were actually about, run:

```powershell
python scripts/run_tagging_qa.py --timeframe all --min-volume 50
```

It prints overall agreement (and a "confident" figure that excludes taxonomy-fallback rows), the
worst-tagged reasons, the most common mis-tag pairs (tagged → actually about), and a sample of
tickets to review. Read-only; no LLM/creds needed. Add `--json-output qa.json` to save the full
report. Build/refresh the reason taxonomy first (`build_reason_taxonomy.py`) for meaningful labels.

### Multi-channel coverage (P2)

The pipeline is channel-agnostic, but ingestion defaults to phone only. To expand root-cause
analysis to **email / chat / SMS**:

1. **Ingest** the other channels — set `CXONE_MEDIA_TYPES` in `.env` (e.g. `PhoneCall,Email,Chat`,
   or leave it empty to ingest every channel), then re-run the CXone extract. (The old
   `CXONE_PHONE_MEDIA_TYPES` name still works.)
2. **Classify** them — pass `--media-type Email --media-type Chat` to `run_transcript_summary.py`
   (or set `media_types` in `config/transcript_summary.json`). The LLM classification prompt is
   channel-aware and adapts its wording per `media_type` (phone call / email / chat).

The chatbot can already slice any analytics view by `media_type`.

The daily pipeline refreshes the map automatically; re-run `build_reason_taxonomy.py` after editing
the config. New reasons that match no alias fall through to "Other / Uncategorized" — review them and
add aliases to capture more volume under named categories.

### Verify sync parity (`check_sync_parity.py`)

Before trusting the hosted chatbot's answers, confirm the data actually made it to Railway. `scripts/check_sync_parity.py` compares the **source** DB (local `DATABASE_URL`) against the **target** DB (`TARGET_DATABASE_URL`, the public Railway URL) for each table and reports whether **row counts and dates line up**:

- Total row count per table (source vs target, with delta)
- Business-date range — `min`/`max` of `interaction_start` (cxone/combined), `created_at` (zendesk), or `analyzed_at` (transcript analysis)
- Freshness — `max(updated_at / row_updated_at)` on each side
- A **per-day row-count breakdown** so a day that was never synced (or only partially synced) is flagged explicitly

```powershell
# Compare all synced tables (local vs Railway)
python scripts/check_sync_parity.py

# Just the day you synced, listing any days that differ
python scripts/check_sync_parity.py --start 2026-06-22 --end 2026-06-22 --show-days

# Check a single database's internal consistency (point source at target)
python scripts/check_sync_parity.py --target-url $env:DATABASE_URL
```

The command exits non-zero when any table is out of sync, so it can gate a daily cron / CI step. Tables: `combined_interactions`, `zendesk_tickets`, `cxone_transcripts`, `cxone_transcript_analysis` by default (add `zendesk_ticket_comments` via `--tables`). Because the daily sync is date-scoped, global counts only match after a full sync — pass the same `--start/--end` window you synced to compare apples-to-apples.

### Group & filter by Zendesk ticket form type

Tickets can be grouped by their **Zendesk ticket form** (e.g. "Assist (Internal)", "Consumer"). The raw ticket payload only carries the numeric `ticket_form_id`, so the readable name is fetched separately into a small lookup table, `zendesk_ticket_forms` (`form_id → name`):

```powershell
# Fetch form names from Zendesk (/api/v2/ticket_forms) into zendesk_ticket_forms
python scripts/run_zendesk_forms_extract.py            # use --dry-run to preview the list
# Copy the lookup table to Railway so the hosted chatbot can see the names
python scripts/sync_to_railway.py --tables zendesk_ticket_forms
```

The `analytics_interactions` view resolves `ticket_form_id` to `ticket_form_name` via a LEFT JOIN, so **no per-row backfill is needed** — historical rows pick up the name as soon as the lookup table is populated. Forms rarely change; re-run the extract only when you add/rename a form in Zendesk.

**In the chatbot:** when `zendesk_ticket_forms` has data, a **"Ticket form types"** picker appears in the UI. Selecting one or more forms restricts every generated query to `ticket_form_name IN (...)`; leaving it empty includes all forms. Users can also ask grouping questions directly (e.g. "call volume by ticket form type last week"). Set `CHATBOT_FORM_FILTER_ENABLED=false` to hide the picker.

### Configure

```powershell
copy config\transcript_summary.json.example config\transcript_summary.json
```

Set `OPENAI_API_KEY` in `.env`. Tune `call_selection` (direction, skills, `PhoneCall` media type) like Step 4 — link-method filters do not apply here.

If you upgraded from an older version, create the cache table once:

```powershell
python scripts/init_db.py
```

### Run Step 4b

```powershell
# Classify last week's inbound phone transcripts and print report
python scripts/run_transcript_summary.py --timeframe last-week

# Test on 10 calls first (still uses cache for those segment_ids)
python scripts/run_transcript_summary.py --timeframe yesterday --limit 10

# Force re-classification
python scripts/run_transcript_summary.py --timeframe last-week --reanalyze

# Export
python scripts/run_transcript_summary.py --timeframe last-week `
  --json-output output/transcript_summary.json `
  --markdown-output output/transcript_summary.md

# Rule-based reduction tips only (no second LLM pass)
python scripts/run_transcript_summary.py --timeframe last-week --no-reduction-llm

# Custom date range (filters on interaction_start)
python scripts/run_transcript_summary.py `
  --start 2026-03-05T00:00:00Z `
  --end 2026-03-11T23:59:59Z `
  --no-reduction-llm

# Full backfill (auto-enables --chunk-days 7 --batch-size 50 --commit-every 10)
python scripts/run_transcript_summary.py --timeframe all --no-reduction-llm

# Explicit control — progress prints to stderr; commits every 10 classifications
python scripts/run_transcript_summary.py --timeframe all `
  --chunk-days 7 --batch-size 50 --commit-every 10 --no-reduction-llm
```

**Large-window note:** `--timeframe all` auto-enables chunked mode. Progress appears on **stderr** (`[HH:MM:SS] ...`). Postgres is updated every **`--commit-every`** successful classifications (default **10**), not after an entire 500-row batch. Already-classified rows are **skipped in SQL** (`skip_existing`). Validate your OpenAI key at startup — invalid keys fail immediately instead of after hours of 401s.

**Cost note:** Step 4b runs one LLM call per transcript (plus optional reduction calls for top primary reasons). Use `--limit` while tuning prompts, then run the full window.

### RAG index for the chatbot

After transcript summarization, build the semantic search index so the chatbot can answer contextual questions (not just SQL aggregates). See **[docs/RAG.md](docs/RAG.md)**.

```powershell
# Relative window
python scripts/build_knowledge_index.py --timeframe last-week

# Custom date range (filters on interaction_start)
python scripts/build_knowledge_index.py --start 2026-03-05 --end 2026-03-11

# Build on Railway (the analytics_knowledge_chunks table is NOT synced — build it there directly)
python scripts/build_knowledge_index.py --timeframe last-week --target-url $env:TARGET_DATABASE_URL
```

Pass `--target-url` (alias `--database-url`) with the Railway **public** URL to build the index there after syncing the base tables. Without it, the build targets `DATABASE_URL` (local Docker). The script prints the target host at startup.

---

## Troubleshooting PostgreSQL connections

**Symptom:** `password authentication failed for user "orchestration"`

**Common cause:** Another PostgreSQL is already using port **5432** (e.g. `postgresql-x64-18` on Windows). Your app connects to that server, not the Docker container.

1. **See what is listening on 5432** (PowerShell):

   ```powershell
   netstat -ano | findstr ":5432"
   Get-Process -Id <PID> | Select-Object ProcessName, Path
   ```

2. **Fix A — use this project’s port 5433** (recommended):

   ```powershell
   docker compose down
   docker compose up -d
   ```

   Set in `.env`:

   `DATABASE_URL=postgresql+psycopg://orchestration:orchestration@localhost:5433/orchestration`

3. **Fix B — stop local Postgres** (only if you want Docker on 5432):

   ```powershell
   Stop-Service postgresql-x64-18
   ```

4. **Verify Docker credentials work:**

   ```powershell
   docker exec -it cxone_zendesk_postgres psql -U orchestration -d orchestration -c "SELECT 1;"
   ```

5. **List tables:**

   ```powershell
   docker exec -it cxone_zendesk_postgres psql -U orchestration -d orchestration -c "\dt"
   ```

---

## Roadmap

- **Step 1** — CXone transcripts → `cxone_transcripts` (done)
- **Step 2** — Zendesk tickets → `zendesk_tickets` (done)
- **Step 3** — Combined dataset `combined_interactions` (done)
- **Step 4** — Interaction summary on `combined_interactions` (done)
- **Step 4b** — Transcript-only LLM primary/secondary/tertiary reasons on `cxone_transcripts` (done)
- **Step 5** — LLM transcript recommendations in Step 4 (done); optional full-transcript deep-dive agent (planned)
- **Step 6** — Hosted analytics chatbot on Railway with company login ([docs/CHATBOT_RAILWAY.md](docs/CHATBOT_RAILWAY.md))
- **P1 enhancements** — Controlled reason taxonomy + reconciliation (`analytics_canonical_reason_outcomes`, `analytics_reason_reconciliation`), metadata-filtered RAG retrieval (skill / reason / date), and first-class trend-compare + drill-down in the chatbot (done)
- **P2 enhancements** — Channel-agnostic ingest + channel-aware transcript classification (phone / email / chat via `CXONE_MEDIA_TYPES` + `--media-type`), and tagging-accuracy QA (`analytics_reason_mismatches` view + `scripts/run_tagging_qa.py`) to flag miscategorized tickets (done)
