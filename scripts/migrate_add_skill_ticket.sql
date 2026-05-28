-- Run once if cxone_transcripts already exists without skill_name / ticket_id:
-- Get-Content scripts/migrate_add_skill_ticket.sql | docker exec -i cxone_zendesk_postgres psql -U orchestration -d orchestration

ALTER TABLE cxone_transcripts
    ADD COLUMN IF NOT EXISTS skill_name VARCHAR(512),
    ADD COLUMN IF NOT EXISTS ticket_id VARCHAR(255);

-- Backfill from stored raw_metadata (agent/team/skill/ticket live in transcript.metrics)
UPDATE cxone_transcripts
SET
    agent_name = COALESCE(
        NULLIF(agent_name, ''),
        raw_metadata->'transcript'->'metrics'->>'agentname'
    ),
    team_name = COALESCE(
        NULLIF(team_name, ''),
        raw_metadata->'transcript'->'metrics'->>'teamname'
    ),
    skill_name = raw_metadata->'transcript'->'metrics'->>'skillname',
    ticket_id = NULLIF(raw_metadata->'transcript'->'metrics'->>'ticketId', '')
WHERE raw_metadata ? 'transcript';
