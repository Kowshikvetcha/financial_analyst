from pathlib import Path

from schema import get_connection, initialise_schema, get_or_create_entity, register_file, update_file_state
import phase4_orchestrator as orch


def _seed(conn):
    eid = get_or_create_entity(conn, "Cloud Test Entity")
    fid = register_file(conn, eid, "seed.csv", "seed.csv", "csv", detected_unit="INR lakh")
    update_file_state(conn, fid, "LIVE")
    conn.execute(
        """
        INSERT INTO live_facts (
          fact_id, entity_id, entity_slug, canonical_field, period, value_normalised, currency,
          original_unit, conversion_factor, conversion_applied, source_file_id, staging_id, is_derived
        ) VALUES
          ('f_cloud_fy24_revenue_net', ?, 'cloud_test_entity', 'revenue_net', 'FY24', 10000000, 'INR', 'INR_lakhs', 100000, TRUE, ?, NULL, FALSE),
          ('f_cloud_fy23_revenue_net', ?, 'cloud_test_entity', 'revenue_net', 'FY23', 8000000, 'INR', 'INR_lakhs', 100000, TRUE, ?, NULL, FALSE),
          ('f_cloud_fy24_cogs', ?, 'cloud_test_entity', 'cogs', 'FY24', 6000000, 'INR', 'INR_lakhs', 100000, TRUE, ?, NULL, FALSE)
        """,
        [eid, fid, eid, fid, eid, fid],
    )
    conn.commit()
    return eid


def test_validate_plan_rejects_unknown_tool():
    try:
        orch._validate_plan({"tool": "drop_table", "args": {}})
        assert False, "expected ValueError for unknown tool"
    except ValueError as e:
        assert "not allowed" in str(e)


def test_validate_plan_rejects_missing_args():
    try:
        orch._validate_plan({"tool": "fetch_metric", "args": {"metric": "revenue_net"}})
        assert False, "expected ValueError for missing args"
    except ValueError as e:
        assert "missing args" in str(e)


def test_validate_plan_rejects_unknown_args():
    try:
        orch._validate_plan(
            {
                "tool": "fetch_metric",
                "args": {"metric": "revenue_net", "period": "FY24", "sql": "SELECT * FROM live_facts"},
            }
        )
        assert False, "expected ValueError for unknown args"
    except ValueError as e:
        assert "unknown args" in str(e)


def test_validate_plan_rejects_non_string_arg():
    try:
        orch._validate_plan(
            {
                "tool": "fetch_metric",
                "args": {"metric": "revenue_net", "period": 2024},
            }
        )
        assert False, "expected ValueError for non-string arg"
    except ValueError as e:
        assert "must be string" in str(e)


def test_validate_plan_rejects_empty_string_arg():
    try:
        orch._validate_plan(
            {
                "tool": "fetch_metric",
                "args": {"metric": "revenue_net", "period": "   "},
            }
        )
        assert False, "expected ValueError for empty arg"
    except ValueError as e:
        assert "cannot be empty" in str(e)


def test_validate_plan_rejects_too_many_args():
    args = {f"k{i}": "x" for i in range(13)}
    try:
        orch._validate_plan({"tool": "search_context", "args": args})
        assert False, "expected ValueError for too many args"
    except ValueError as e:
        assert "too large" in str(e)


def test_json_from_maybe_fenced_parses_fenced_json():
    obj = orch._json_from_maybe_fenced("```json\n{\"tool\":\"list_available_metrics\",\"args\":{}}\n```")
    assert obj["tool"] == "list_available_metrics"


def test_extract_content_from_openai_style_response():
    out = {
        "choices": [
            {"message": {"content": "{\"tool\":\"list_available_metrics\",\"args\":{}}"}}
        ]
    }
    c = orch._extract_content_from_provider_response(out)
    assert "list_available_metrics" in c


def test_extract_content_from_anthropic_style_response():
    out = {"content": [{"type": "text", "text": "{\"tool\":\"list_available_metrics\",\"args\":{}}"}]}
    c = orch._extract_content_from_provider_response(out)
    assert "list_available_metrics" in c


def test_cloud_fallback_to_local_router(tmp_path: Path, monkeypatch):
    db = tmp_path / "cloud_fallback.duckdb"
    conn = get_connection(db)
    initialise_schema(conn)
    eid = _seed(conn)

    monkeypatch.setenv("ORCH_CLOUD_ROUTER_ENABLED", "1")

    def _raise(*args, **kwargs):
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(orch, "_call_cloud_planner", _raise)

    out = orch.answer_question(conn, eid, "what is revenue in FY24")
    assert out.tool_name == "fetch_metric"
    assert out.payload.get("metric") == "revenue_net"


def test_execute_plan_runs_whitelisted_tool(tmp_path: Path):
    db = tmp_path / "cloud_execute.duckdb"
    conn = get_connection(db)
    initialise_schema(conn)
    eid = _seed(conn)

    result = orch._execute_plan(
        conn,
        eid,
        "calculate_variance",
        {"metric": "revenue_net", "period_1": "FY23", "period_2": "FY24"},
    )
    assert result.tool_name == "calculate_variance"
    assert "delta" in result.payload
