-- Run once if cxone_transcripts already exists without acd_contact_id / acd_session_id / contact_no:
-- Get-Content scripts/migrate_add_contact_ids.sql | docker exec -i cxone_zendesk_postgres psql -U orchestration -d orchestration

ALTER TABLE cxone_transcripts
    ADD COLUMN IF NOT EXISTS acd_contact_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS acd_session_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS contact_no VARCHAR(64);

ALTER TABLE cxone_transcripts
    ALTER COLUMN contact_id TYPE VARCHAR(255);

UPDATE cxone_transcripts
SET
    acd_contact_id = COALESCE(
        NULLIF(acd_contact_id, ''),
        raw_metadata->'transcript'->'metrics'->>'acdcontactid',
        (raw_metadata->'segment'->>'acdContactId'),
        NULLIF(contact_id, '')
    ),
    acd_session_id = raw_metadata->'transcript'->'metrics'->>'acdsessionid',
    contact_no = COALESCE(
        NULLIF(contact_no, ''),
        raw_metadata->'transcript'->'metrics'->>'contactNo',
        raw_metadata->'segment'->'contactNo'->>0
    ),
    contact_id = COALESCE(
        NULLIF(raw_metadata->'transcript'->'metrics'->>'contactid', ''),
        contact_id
    )
WHERE raw_metadata ? 'transcript';
