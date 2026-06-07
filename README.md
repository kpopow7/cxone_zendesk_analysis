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
  sync_to_railway.py               # Copy tables to Railway Postgres
  railway_analytics_setup.sql      # Analytics view for chatbot
  probe_zendesk.py
chatbot/
  app.py                           # Gradio chatbot (company login)
  Dockerfile                       # Railway deploy
docs/
  CHATBOT_RAILWAY.md               # Railway DB + chatbot setup
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

1. **One-time:** `run_cxone_historical_backfill.py` + `run_zendesk_extract.py` (full range) + `run_build_combined_dataset.py --rebuild`
2. **Daily:** `run_daily_pipeline.py` or scheduled task ([docs/DAILY_SCHEDULE.md](docs/DAILY_SCHEDULE.md))
3. **Optional:** `run_interaction_summary.py` or Railway chatbot for ad-hoc / NL questions

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
```

**Cost note:** Step 4b runs one LLM call per transcript (plus optional reduction calls for top primary reasons). Use `--limit` while tuning prompts, then run the full window.

### RAG index for the chatbot

After transcript summarization, build the semantic search index so the chatbot can answer contextual questions (not just SQL aggregates). See **[docs/RAG.md](docs/RAG.md)**.

```powershell
python scripts/build_knowledge_index.py --timeframe last-week
```

On Railway, point `DATABASE_URL` at `TARGET_DATABASE_URL` and run the same command after syncing tables.

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
