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


def test_allows_analytics_canonical_reason_outcomes() -> None:
    sql = """
    SELECT canonical_reason, call_count, escalated_pct, repeat_contact_pct
    FROM analytics_canonical_reason_outcomes
    ORDER BY call_count DESC
    LIMIT 15
    """
    result = validate_sql(sql)
    assert result.ok, result.error


def test_allows_analytics_reason_reconciliation() -> None:
    sql = """
    SELECT call_reason_canonical, comparable_calls, agree_pct, disagree_pct
    FROM analytics_reason_reconciliation
    WHERE comparable_calls >= 20
    ORDER BY disagree_pct DESC
    LIMIT 15
    """
    result = validate_sql(sql)
    assert result.ok, result.error


def test_allows_analytics_reason_mismatches() -> None:
    sql = """
    SELECT segment_id, ticket_id, tagged_reason_canonical, transcript_reason_canonical
    FROM analytics_reason_mismatches
    WHERE tagged_reason_canonical <> 'Other / Uncategorized'
    ORDER BY interaction_start DESC
    LIMIT 25
    """
    result = validate_sql(sql)
    assert result.ok, result.error


def test_allows_analytics_reason_taxonomy() -> None:
    sql = """
    SELECT reason_key, canonical_reason, call_count
    FROM analytics_reason_taxonomy
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
