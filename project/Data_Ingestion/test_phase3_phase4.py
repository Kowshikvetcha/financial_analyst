from pathlib import Path

from schema import get_connection, initialise_schema, get_or_create_entity, register_file, update_file_state
from phase3_tools import fetch_metric, calculate_variance, calculate_ratio, list_available_metrics, list_sources
from phase4_orchestrator import answer_question


def _seed_minimal(conn):
    eid = get_or_create_entity(conn, "Test Entity")
    fid = register_file(conn, eid, "t.csv", "t.csv", "csv", detected_unit="INR lakh")
    update_file_state(conn, fid, "LIVE")
    conn.execute(
        """
        INSERT INTO live_facts (
          fact_id, entity_id, entity_slug, canonical_field, period, value_normalised, currency,
          original_unit, conversion_factor, conversion_applied, source_file_id, staging_id, is_derived
        ) VALUES
          ('f_test_fy24_revenue_net', ?, 'test_entity', 'revenue_net', 'FY24', 10000000, 'INR', 'INR_lakhs', 100000, TRUE, ?, NULL, FALSE),
          ('f_test_fy23_revenue_net', ?, 'test_entity', 'revenue_net', 'FY23', 8000000, 'INR', 'INR_lakhs', 100000, TRUE, ?, NULL, FALSE),
          ('f_test_fy24_cogs', ?, 'test_entity', 'cogs', 'FY24', 6000000, 'INR', 'INR_lakhs', 100000, TRUE, ?, NULL, FALSE)
        """,
        [eid, fid, eid, fid, eid, fid],
    )
    conn.commit()
    return eid


def test_phase3_tools_basic(tmp_path: Path):
    db = tmp_path / "t.duckdb"
    conn = get_connection(db)
    initialise_schema(conn)
    eid = _seed_minimal(conn)

    fm = fetch_metric(conn, eid, "revenue_net", "FY24")
    assert fm["metric"] == "revenue_net"

    var = calculate_variance(conn, eid, "revenue_net", "FY23", "FY24")
    assert var["delta"] == "2000000.0"

    ratio = calculate_ratio(conn, eid, "cogs", "revenue_net", "FY24")
    assert ratio["ratio"].startswith("0.6")

    metrics = list_available_metrics(conn, eid)
    assert any(m["metric"] == "revenue_net" for m in metrics["metrics"])

    sources = list_sources(conn, eid, "revenue_net", "FY24")
    assert len(sources["sources"]) == 1


def test_phase4_orchestrator_gate(tmp_path: Path):
    db = tmp_path / "t2.duckdb"
    conn = get_connection(db)
    initialise_schema(conn)
    eid = get_or_create_entity(conn, "Awaiting Entity")
    out = answer_question(conn, eid, "what is revenue")
    assert "not LIVE" in out.answer
