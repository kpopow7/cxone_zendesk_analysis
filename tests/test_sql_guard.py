from orchestration.chatbot.sql_guard import validate_sql


def test_allows_replace_function_for_inbound_filter() -> None:
    sql = """
    SELECT zendesk_promoted_fields->>'cf_reason_for_contact_consumer' AS reason, COUNT(*) AS n
    FROM analytics_interactions
    WHERE interaction_start >= NOW() - INTERVAL '5 days'
      AND upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%'
    GROUP BY 1
    ORDER BY n DESC
    LIMIT 5
    """
    result = validate_sql(sql)
    assert result.ok, result.error


def test_allows_keywords_inside_string_literals() -> None:
    sql = """
    SELECT ticket_subject
    FROM analytics_interactions
    WHERE ticket_subject = 'please update my account'
    LIMIT 5
    """
    result = validate_sql(sql)
    assert result.ok, result.error


def test_blocks_insert() -> None:
    sql = "INSERT INTO combined_interactions (segment_id) VALUES ('x')"
    result = validate_sql(sql)
    assert not result.ok


def test_blocks_disallowed_table() -> None:
    sql = "SELECT * FROM pg_catalog.pg_user LIMIT 1"
    result = validate_sql(sql)
    assert not result.ok
    assert "not allowed" in (result.error or "").lower()


def test_allows_analytics_transcript_summaries() -> None:
    sql = """
    SELECT primary_reason, secondary_reason, transcript_summary
    FROM analytics_transcript_summaries
    WHERE interaction_start >= NOW() - INTERVAL '7 days'
    ORDER BY interaction_start DESC
    LIMIT 10
    """
    result = validate_sql(sql)
    assert result.ok, result.error
