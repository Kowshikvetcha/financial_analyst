from pathlib import Path

import duckdb

from schema import initialise_schema
from file_reader import (
    _row_level_unit_override,
    ingest_file,
    extract_inline_annotations,
)


def test_row_level_unit_override_parenthetical_absolute():
    u = _row_level_unit_override("Packaging Expense (Rs absolute)", fallback_unit="INR Lakh")
    assert u == "INR absolute"


def test_row_level_unit_override_fallback_rupees_to_absolute():
    u = _row_level_unit_override("Other Expense in Rs", fallback_unit="INR Lakh")
    assert u == "INR absolute"


def test_extract_inline_annotations_numeric_claim_flag():
    import polars as pl

    df = pl.DataFrame(
        {
            "Metric": ["Revenue", "COGS"],
            "Remark": ["Revenue revised to 120", "No issue"],
        }
    )
    chunks = extract_inline_annotations(df, file_id=1, entity_id=1)
    assert len(chunks) == 2
    assert chunks[0]["contains_numerical_claim"] is True
    assert chunks[1]["contains_numerical_claim"] is False


def test_ingest_file_routes_ledger_layout(tmp_path: Path):
    # Build a simple ledger-like CSV where first column header is "Particulars"
    p = tmp_path / "ledger.csv"
    p.write_text(
        "Particulars,FY24\n"
        "Revenue,100\n"
        "Cost of Goods Sold,60\n",
        encoding="utf-8",
    )

    conn = duckdb.connect(":memory:")
    initialise_schema(conn)

    # minimal entity + file setup
    conn.execute("INSERT INTO entities (entity_id, entity_name, entity_slug) VALUES (1, 'Ledger Co', 'ledger_co')")
    conn.execute(
        """
        INSERT INTO source_files (file_id, entity_id, filename, file_path, file_type, state)
        VALUES (1, 1, 'ledger.csv', ?, 'csv', 'SCHEMA_MAPPED')
        """,
        [str(p)],
    )
    conn.commit()

    out = ingest_file(conn, p, file_id=1, entity_id=1)
    assert out["layout"] == "ledger"
    assert out["facts_staged"] >= 1
