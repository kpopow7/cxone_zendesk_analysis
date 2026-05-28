-- Add promoted custom field columns to zendesk_tickets (idempotent).
-- Get-Content scripts/migrate_zendesk_promoted_columns.sql | docker exec -i cxone_zendesk_postgres psql -U orchestration -d orchestration

ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_account_number TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_call_object_identifier TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_disposition TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_disposition_dealer TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_disposition_consumer TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_disposition_consumer_levolor TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_disposition_customer_levolor TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_i_need_help_with TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_intent TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_master_call_identifier TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_parent_ticket TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_po_number TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_product_order_ids TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_reason_for_contact_installerdealer TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_reason_for_contact_consumer TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_reason_for_contact_customer_levolor TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_sales_area TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_sentiment TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_summary TEXT;
ALTER TABLE zendesk_tickets ADD COLUMN IF NOT EXISTS cf_total_time_spent_sec TEXT;

-- Backfill physical columns from promoted_fields jsonb (after re-extract, or run this once)
UPDATE zendesk_tickets
SET
    cf_account_number = promoted_fields->>'cf_account_number',
    cf_call_object_identifier = promoted_fields->>'cf_call_object_identifier',
    cf_disposition = promoted_fields->>'cf_disposition',
    cf_disposition_dealer = promoted_fields->>'cf_disposition_dealer',
    cf_disposition_consumer = promoted_fields->>'cf_disposition_consumer',
    cf_disposition_consumer_levolor = promoted_fields->>'cf_disposition_consumer_levolor',
    cf_disposition_customer_levolor = promoted_fields->>'cf_disposition_customer_levolor',
    cf_i_need_help_with = promoted_fields->>'cf_i_need_help_with',
    cf_intent = promoted_fields->>'cf_intent',
    cf_master_call_identifier = promoted_fields->>'cf_master_call_identifier',
    cf_parent_ticket = promoted_fields->>'cf_parent_ticket',
    cf_po_number = promoted_fields->>'cf_po_number',
    cf_product_order_ids = promoted_fields->>'cf_product_order_ids',
    cf_reason_for_contact_installerdealer = promoted_fields->>'cf_reason_for_contact_installerdealer',
    cf_reason_for_contact_consumer = promoted_fields->>'cf_reason_for_contact_consumer',
    cf_reason_for_contact_customer_levolor = promoted_fields->>'cf_reason_for_contact_customer_levolor',
    cf_sales_area = promoted_fields->>'cf_sales_area',
    cf_sentiment = promoted_fields->>'cf_sentiment',
    cf_summary = promoted_fields->>'cf_summary',
    cf_total_time_spent_sec = promoted_fields->>'cf_total_time_spent_sec'
WHERE promoted_fields IS NOT NULL AND promoted_fields <> '{}'::jsonb;
