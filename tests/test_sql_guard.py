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


def test_allows_analytics_reduction_recommendations() -> None:
    sql = """
    SELECT rank, primary_reason, call_count, share_pct, recommendations_text
    FROM analytics_reduction_recommendations
    ORDER BY rank
    LIMIT 10
    """
    result = validate_sql(sql)
    assert result.ok, result.error


def test_allows_analytics_reason_outcomes() -> None:
    sql = """
    SELECT call_reason, call_count, escalated_pct, repeat_contact_pct, unresolved_pct
    FROM analytics_reason_outcomes
    ORDER BY call_count DESC
    LIMIT 20
    """
    result = validate_sql(sql)
    assert result.ok, result.error


def test_allows_analytics_interaction_outcomes() -> None:
    sql = """
    SELECT primary_reason,
           COUNT(*) AS call_count,
           COUNT(*) FILTER (WHERE is_resolved) AS resolved
    FROM analytics_interaction_outcomes
    WHERE interaction_start >= NOW() - INTERVAL '30 days'
      AND is_repeat_contact
    GROUP BY primary_reason
    ORDER BY call_count DESC
    LIMIT 20
    """
    result = validate_sql(sql)
    assert result.ok, result.error
