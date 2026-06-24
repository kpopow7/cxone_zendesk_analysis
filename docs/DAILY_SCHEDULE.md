# Daily pipeline schedule

Run CXone extract, Zendesk extract, combined dataset update, transcript classification
(reasons + reduction report), and the chatbot RAG knowledge index on a fixed schedule.

## One command (manual or cron)

```powershell
cd c:\Users\kpopo\OneDrive\Documents\cxone_zendesk_analysis

# Yesterday (UTC), all three steps
python scripts/run_daily_pipeline.py

# Business timezone (recommended if "yesterday" should follow US Eastern)
python scripts/run_daily_pipeline.py --timezone America/New_York

# Specific day (re-run / backfill one day)
python scripts/run_daily_pipeline.py --date 2026-05-28 --timezone UTC

# After pipeline, push to Railway for the hosted chatbot
python scripts/run_daily_pipeline.py --sync-railway
```

### What each step uses

| Step | Source | Date window |
|------|--------|-------------|
| CXone | `run_cxone_extract` logic | Target calendar day (`interaction_start`) |
| Zendesk | `run_zendesk_extract` logic | Target day **plus** `--zendesk-lookback-days` (default **2**) for bridge tickets created slightly earlier |
| Combined | `run_build_combined_dataset` | Same day as CXone, **incremental upsert** (no `--rebuild`) |
| Classification | `run_transcript_summary_step` (full report) | Target day; LLM-classifies that day's transcripts into `cxone_transcript_analysis` and persists ranked reasons + recommendations to `analytics_reduction_recommendations` |
| Knowledge index | `build_knowledge_index` | Target day; (re)embeds interactions into `analytics_knowledge_chunks` for chatbot RAG |

Default target day = **yesterday** in the chosen timezone.

> **LLM cost / requirements:** Classification and the knowledge index both call OpenAI and
> need `OPENAI_API_KEY`. If the key is unset they are skipped with a warning. Disable them
> per run with `--skip-classification` / `--skip-knowledge-index`. Classification covers the
> whole target day (already-classified calls are skipped via cache, so daily runs only pay for
> new calls).

### Railway (hosted chatbot) behavior

With `--sync-railway`, after the local load the runner:

1. Syncs the relational tables (`cxone_transcripts`, `zendesk_tickets`, `combined_interactions`) scoped to the target day.
2. Syncs the freshly classified rows (`cxone_transcript_analysis`, `transcript_reduction_reports`, `transcript_reduction_report_reasons`) scoped by `--since`.
3. Builds the RAG knowledge index **directly on the Railway DB** (so each day's content is embedded once, on the DB the chatbot reads).

---

## Windows Task Scheduler (recommended on your PC)

### 1. Prerequisites

- Docker Desktop running (Postgres via `docker compose up -d`)
- `.venv` created and `pip install -r requirements.txt`
- `.env` configured (CXone, Zendesk, `DATABASE_URL`)
- Historical backfill already done once

### 2. Register the task

```powershell
cd c:\Users\kpopo\OneDrive\Documents\cxone_zendesk_analysis

# Daily at 6:00 AM local time, US Eastern "yesterday"
.\scripts\register_daily_task.ps1 -Time "06:00" -Timezone "America/New_York"

# Include Railway sync after local load
.\scripts\register_daily_task.ps1 -Time "06:30" -Timezone "America/New_York" -SyncRailway
```

### 3. Test

```powershell
# Dry run (no DB writes)
.\scripts\run_daily_pipeline.ps1 -DryRun

# Run for real once
.\scripts\run_daily_pipeline.ps1 -Timezone "America/New_York"

# Trigger the scheduled task
Start-ScheduledTask -TaskName "CXoneZendeskDailyPipeline"
```

Logs: `logs/daily_pipeline_YYYYMMDD_HHMMSS.log`

### 4. Manage the task

```powershell
Get-ScheduledTask -TaskName "CXoneZendeskDailyPipeline"
Unregister-ScheduledTask -TaskName "CXoneZendeskDailyPipeline" -Confirm:$false
```

Task Scheduler UI: `taskschd.msc` → Task Scheduler Library → **CXoneZendeskDailyPipeline**

---

## Railway cron (cloud alternative)

If the pipeline runs against **Railway Postgres** (no local Docker):

1. Add a **Cron** service in Railway (or use GitHub Actions).
2. Command:

```bash
python scripts/run_daily_pipeline.py --timezone UTC
```

3. Set all `.env` secrets on the cron service (CXone, Zendesk, `DATABASE_URL`).

For Railway-hosted DB + chatbot, skip `--sync-railway` (data is already there).

---

## GitHub Actions (optional)

Schedule in `.github/workflows/daily_pipeline.yml`:

```yaml
on:
  schedule:
    - cron: "0 11 * * *"  # 06:00 US Eastern (UTC-5) ≈ adjust for DST
  workflow_dispatch:

jobs:
  pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python scripts/run_daily_pipeline.py --timezone America/New_York
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          CXONE_CLIENT_ID: ${{ secrets.CXONE_CLIENT_ID }}
          # ... other secrets
```

---

## Recommended daily order

1. **CXone** list extract for yesterday  
2. **Zendesk** tickets (yesterday + 2-day lookback)  
3. **Combined** incremental upsert for yesterday’s segments  
4. **Classification** of yesterday's transcripts → reasons + reduction report  
5. **Knowledge index** refresh for chatbot RAG  
6. **Optional:** `--sync-railway` if DB is local but chatbot is on Railway  

`run_daily_pipeline.py` runs steps 1–5 automatically (4–5 need `OPENAI_API_KEY`).
Do **not** use `--rebuild` on daily runs; full rebuild is only for initial load or link-rule changes.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Task runs but 0 rows | Check timezone vs when calls actually occurred; try `--date` for a known busy day |
| Zendesk link misses | Increase `--zendesk-lookback-days` (default 2) |
| Postgres connection failed | Start Docker: `docker compose up -d` |
| Task never runs | PC must be on at scheduled time, or enable "Run task as soon as possible after a scheduled start is missed" in Task Scheduler |
| Railway chatbot stale | Add `-SyncRailway` to scheduled task and set `TARGET_DATABASE_URL` in `.env` |
| Classification / index skipped | Set `OPENAI_API_KEY` in `.env` (steps skip with a warning when it's missing) |
| `relation "zendesk_ticket_forms" does not exist` | Initialize schema once: `python -c "import sys; sys.path.insert(0,'src'); from orchestration.db.schema import init_database; from orchestration.config import get_settings; init_database(get_settings().database_url)"` |
