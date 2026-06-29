# Scripts quick reference

One-line-per-script reference for everything in `scripts/`. **Table name** = the table(s) the
script *writes* to (`(none — read-only)` means it only reads / prints / writes files, never a DB
table). **Comments** call out any script that must be run first.

> All scripts load `.env` and use `DATABASE_URL` (local Docker Postgres) unless a `--target-url` /
> `--database-url` is given. Run `python scripts/init_db.py` once before anything else.

## Typical order

`init_db` → `run_cxone_extract` (+ `run_zendesk_extract`) → `run_build_combined_dataset` →
`run_transcript_summary` → `build_knowledge_index` → `sync_to_railway` → `check_sync_parity`.
`run_daily_pipeline` chains the middle of this automatically.

## Reference table

| Script name | Table name (writes to) | Script Options | Comments (dependencies) |
|-------------|------------------------|----------------|--------------------------|
| `init_db.py` | All pipeline tables + analytics views + knowledge schema (`cxone_transcripts`, `cxone_transcript_analysis`, `zendesk_tickets`, `zendesk_ticket_comments`, `zendesk_ticket_forms`, `combined_interactions`, `transcript_reduction_reports`, `transcript_reduction_report_reasons`) | _(none — reads `DATABASE_URL` from `.env`)_ | **Run first.** No dependency. Creates the schema every other script relies on. Safe to re-run (idempotent). |
| `run_cxone_extract.py` | `cxone_transcripts` | `--start`* , `--end`* , `--dry-run`, `--skip-database`, `--limit`, `--json-output` | Needs `init_db` + CXone creds. Independent of Zendesk. Daily extract; run before `run_build_combined_dataset`. |
| `run_cxone_historical_backfill.py` | `cxone_transcripts` (enriched per-segment) | `--start`* , `--end`* , `--chunk-days` (def 1), `--dry-run`, `--skip-database`, `--limit` | One-time enriched historical load (alternative to `run_cxone_extract` for backfills). Needs `init_db` + CXone creds. |
| `run_zendesk_extract.py` | `zendesk_tickets` | `--start`* , `--end`* , `--dry-run`, `--skip-database`, `--limit`, `--json-output` | Needs `init_db` + Zendesk creds. Independent of CXone. Run before `run_build_combined_dataset`. |
| `run_zendesk_forms_extract.py` | `zendesk_ticket_forms` | `--dry-run` | Needs `init_db` + Zendesk creds. Lookup table for form-type grouping. Run anytime; sync to Railway afterward (`sync_to_railway.py --tables zendesk_ticket_forms`). |
| `run_zendesk_comments_extract.py` | `zendesk_ticket_comments` | `--start`* , `--end`* , `--mode` [incremental\|per-ticket], `--dry-run`, `--skip-database`, `--limit-tickets`, `--limit-comments`, `--json-output` | Optional. Needs `init_db` + Zendesk creds. Best run after `run_zendesk_extract`. |
| `run_build_combined_dataset.py` | `combined_interactions` | `--interaction-start`, `--interaction-end`, `--matched-only`, `--rebuild`, `--batch-size` (def 50), `--dry-run`, `--link-config` | **Depends on `run_cxone_extract` AND `run_zendesk_extract`** (both source tables must be populated). Run `run_zendesk_forms_extract` first if you want form names. Use `--rebuild` only for full reloads / link-rule changes, never on daily runs. |
| `run_transcript_summary.py` | `cxone_transcript_analysis`; **with `--full-report`** also `transcript_reduction_reports` + `transcript_reduction_report_reasons` (refreshes analytics views) | `--timeframe` [all\|yesterday\|last-week\|last-7-days], `--start`, `--end`, `--config`, `--json-output`, `--markdown-output`, `--reduction-llm/--no-reduction-llm`, `--reanalyze`, `--limit`, `--batch-size`, `--chunk-days`, `--commit-every`, `--full-report`, `--call-direction`, `--skill`, `--exclude-skill`, `--team`, `--media-type` | **Depends on `cxone_transcripts`** (`run_cxone_extract` / backfill). **Needs `OPENAI_API_KEY`.** `--full-report` is what persists the ranked reasons + recommendations the chatbot reads. |
| `run_interaction_summary.py` | _(none — read-only; prints + optional JSON/Markdown)_ | `--timeframe`, `--start`, `--end`, `--config`, `--json-output`, `--markdown-output`, `--llm-recommendations/--no-llm-recommendations`, `--call-direction`, `--skill`, `--exclude-skill`, `--team`, `--media-type`, `--link-method`, `--include-unmatched` | **Depends on `combined_interactions`.** Read-only Zendesk-reason report. `--llm-recommendations` needs `OPENAI_API_KEY`. |
| `build_knowledge_index.py` | `analytics_knowledge_chunks` (pgvector); ensures analytics views | `--timeframe`, `--start`, `--end`, `--limit`, `--batch-size` (def 32), `--database-url`/`--target-url` | **Depends on `combined_interactions` + `cxone_transcript_analysis`** (via analytics views). **Needs `OPENAI_API_KEY` + pgvector.** Not synced — build directly on Railway with `--target-url` (public URL). |
| `sync_to_railway.py` | Copies tables to the **target** DB and creates/refreshes analytics views there (`combined_interactions`, `zendesk_tickets`, `zendesk_ticket_forms`, `cxone_transcripts`, `cxone_transcript_analysis`, `transcript_reduction_reports`, `transcript_reduction_report_reasons`) | `--source-url`, `--target-url`* , `--tables`, `--batch-size`, `--include-raw-metadata/--omit-raw-metadata`, `--init-schema/--no-init-schema`, `--since`, `--interaction-start`, `--interaction-end`, `--ticket-created-start`, `--ticket-created-end`, `--include-linked-tickets/--no-include-linked-tickets` | **Depends on the local pipeline tables being populated** (extracts + combine, and classify for the reason tables). Does **not** sync `analytics_knowledge_chunks` — build that on the target with `build_knowledge_index.py`. |
| `run_daily_pipeline.py` | Orchestrates: `cxone_transcripts`, `zendesk_tickets`, `combined_interactions`, `cxone_transcript_analysis`, `transcript_reduction_reports`/`_reasons`, `analytics_knowledge_chunks` | `--date`, `--timezone` (def UTC), `--zendesk-lookback-days` (def 2), `--skip-cxone`, `--skip-zendesk`, `--skip-combined`, `--skip-classification`, `--skip-knowledge-index`, `--dry-run`, `--sync-railway` | **One command that runs steps 1–5 in the correct order.** Needs `init_db` once + CXone/Zendesk creds + `OPENAI_API_KEY` (for classification + index). `--sync-railway` needs `TARGET_DATABASE_URL`. |
| `check_sync_parity.py` | _(none — read-only audit)_ | `--source-url`, `--target-url`* , `--tables`, `--start`, `--end`, `--show-days/--no-show-days`, `--max-days` (def 20), `--statement-timeout-ms` (def 60000) | **Run after `sync_to_railway`** to confirm row counts / dates match between local and Railway. Exits non-zero when out of sync (CI/cron friendly). |
| `generate_disposition_label_map.py` | _(none — writes `config/disposition_label_map.json`)_ | `--output`, `--top` (def 50), `--dry-run` | **Depends on `combined_interactions`.** Config helper: scaffolds human labels for disposition codes. Re-run extracts/combine to pick up the new labels. |
| `list_call_selection_values.py` | _(none — read-only; prints)_ | `--timeframe`, `--start`, `--end`, `--top` (def 30) | **Depends on `combined_interactions`.** Helper to discover skills/teams/media types/link methods/directions for building filters. |
| `probe_zendesk.py` | _(none — writes `output/zendesk_ticket_fields.json`, optional `config/zendesk_field_map.json.example`)_ | `--start`, `--end`, `--catalog-output`, `--write-example-map`, `--active-only` | Diagnostic/setup. Needs Zendesk creds. Used when planning promoted custom-field columns. |
| `probe_cxone_ia.py` | _(none — debug API only)_ | `--start`, `--end`, `--no-date-filter` | Diagnostic only. Needs CXone creds. Inspects the CXone Interaction Analytics API response shape. |

`*` = required option.

## PowerShell helpers (Windows)

| Script name | Table name | Script Options | Comments |
|-------------|------------|----------------|----------|
| `run_daily_pipeline.ps1` | _(wrapper — same tables as `run_daily_pipeline.py`)_ | `-DryRun`, `-Timezone`, `-SyncRailway` (passes through to `run_daily_pipeline.py`) | Convenience wrapper that activates the venv and runs the daily pipeline; writes a timestamped log under `logs/`. |
| `register_daily_task.ps1` | _(none — registers a Windows Scheduled Task)_ | `-Time`, `-Timezone`, `-SyncRailway` | Schedules `run_daily_pipeline.ps1` via Task Scheduler. Run once to install the daily job. |

## Related (not a script)

- `scripts/railway_analytics_setup.sql` — raw SQL that (re)creates the analytics views and reduction-report tables on a target DB. `sync_to_railway.py` and `init_db.py` apply the same views automatically; run this manually only for first-time Railway setup or when the view definitions change.
