-- Run if combined_interactions already exists without parent-ticket link columns:
-- Get-Content scripts/migrate_combined_parent_ticket_link.sql | docker exec -i cxone_zendesk_postgres psql -U orchestration -d orchestration

ALTER TABLE combined_interactions
    ADD COLUMN IF NOT EXISTS phone_call_ticket_id BIGINT,
    ADD COLUMN IF NOT EXISTS parent_link_key VARCHAR(512),
    ADD COLUMN IF NOT EXISTS ticket_form_id BIGINT,
    ADD COLUMN IF NOT EXISTS zendesk_phone_call_fields JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_combined_interactions_phone_call_ticket_id
    ON combined_interactions (phone_call_ticket_id);
