-- Run once if cxone_transcripts already exists without call_direction:
-- docker exec -i cxone_zendesk_postgres psql -U orchestration -d orchestration < scripts/migrate_add_call_direction.sql

ALTER TABLE cxone_transcripts
    ADD COLUMN IF NOT EXISTS call_direction VARCHAR(32);
