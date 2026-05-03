"""
Conflict detection and live fact promotion for Phase 1.

After staging, the pipeline:
  1. Detects conflicts: same (entity, field, period) with different values
  2. Surfaces them for the onboarding conversation to resolve
  3. Promotes resolved/uncontested facts to live_facts
  4. Computes derived KPIs (margins, ratios) deterministically
"""

import json
import duckdb
from dataclasses import dataclass
from typing import Optional

from canonical_fields import CANONICAL_FIELDS


# ── Conflict detection ────────────────────────────────────────────────────────

@dataclass
class Conflict:
    conflict_id: Optional[int]
    entity_id: int
    canonical_field: str
    period: str
    options: list[dict]   # [{staging_id, value_normalised, original_unit, source_filename}]


def detect_conflicts(
    conn: duckdb.DuckDBPyConnection,
    entity_id: int,
    file_ids: Optional[list[int]] = None,
) -> list[Conflict]:
    """
    Find (entity, field, period) triples with >1 distinct value in staging.
    Returns list of Conflict objects for the onboarding conversation.
    """
    file_filter = ""
    params = [entity_id]
    if file_ids:
        placeholders = ", ".join("?" * len(file_ids))
        file_filter = f"AND s.file_id IN ({placeholders})"
        params.extend(file_ids)

    rows = conn.execute(f"""
        SELECT
            s.canonical_field,
            s.period,
            COUNT(DISTINCT s.value_normalised) AS distinct_vals
        FROM staging_facts s
        WHERE s.entity_id = ? {file_filter}
        GROUP BY s.canonical_field, s.period
        HAVING COUNT(DISTINCT s.value_normalised) > 1
    """, params).fetchall()

    conflicts = []
    for (field, period, _) in rows:
        options_rows = conn.execute(f"""
            SELECT
                s.staging_id,
                s.value_normalised,
                s.original_unit,
                s.conversion_factor,
                sf.filename AS source_filename,
                s.raw_value,
                s.raw_header
            FROM staging_facts s
            JOIN source_files sf ON s.file_id = sf.file_id
            WHERE s.entity_id = ? AND s.canonical_field = ? AND s.period = ?
            {file_filter.replace('s.file_id', 'sf.file_id')}
            ORDER BY s.staging_id
        """, [entity_id, field, period] + (file_ids or [])).fetchall()

        options = [
            {
                "staging_id": r[0],
                "value_normalised": r[1],
                "original_unit": r[2],
                "conversion_factor": r[3],
                "source_filename": r[4],
                "raw_value": r[5],
                "raw_header": r[6],
            }
            for r in options_rows
        ]

        conflicts.append(Conflict(
            conflict_id=None,
            entity_id=entity_id,
            canonical_field=field,
            period=period,
            options=options,
        ))

    return conflicts


def save_conflicts(
    conn: duckdb.DuckDBPyConnection,
    conflicts: list[Conflict],
) -> list[int]:
    """Persist conflicts to DB and return their IDs."""
    ids = []
    for c in conflicts:
        conn.execute("""
            INSERT INTO conflicts (entity_id, canonical_field, period, staging_ids, values_seen, state)
            VALUES (?, ?, ?, ?, ?, 'OPEN')
        """, [
            c.entity_id,
            c.canonical_field,
            c.period,
            [o["staging_id"] for o in c.options],
            json.dumps([{"staging_id": o["staging_id"], "value": o["value_normalised"],
                          "source": o["source_filename"]} for o in c.options]),
        ])
        cid = conn.execute(
            "SELECT conflict_id FROM conflicts WHERE entity_id=? AND canonical_field=? AND period=? ORDER BY conflict_id DESC LIMIT 1",
            [c.entity_id, c.canonical_field, c.period]
        ).fetchone()[0]
        ids.append(cid)
    conn.commit()
    return ids


def resolve_conflict(
    conn: duckdb.DuckDBPyConnection,
    conflict_id: int,
    chosen_staging_id: int,
) -> None:
    """Mark a conflict resolved with user's chosen staging_id."""
    conn.execute("""
        UPDATE conflicts
        SET state = 'RESOLVED',
            resolution_staging_id = ?,
            resolved_by = 'user',
            resolved_at = CURRENT_TIMESTAMP
        WHERE conflict_id = ?
    """, [chosen_staging_id, conflict_id])
    conn.commit()


# ── Live fact promotion ───────────────────────────────────────────────────────

def promote_to_live(
    conn: duckdb.DuckDBPyConnection,
    entity_id: int,
    entity_slug: str,
    file_ids: Optional[list[int]] = None,
) -> int:
    """
    For each (entity, field, period) in staging:
      - If no conflict: promote directly
      - If conflict resolved: promote the chosen staging row
    Returns count of live facts written.
    """
    file_filter = ""
    params_base = [entity_id]
    if file_ids:
        placeholders = ", ".join("?" * len(file_ids))
        file_filter = f"AND file_id IN ({placeholders})"
        params_base.extend(file_ids)

    # Get all resolvable staging facts
    staging_rows = conn.execute(f"""
        SELECT
            s.staging_id,
            s.canonical_field,
            s.period,
            s.value_normalised,
            s.currency,
            s.original_unit,
            s.conversion_factor,
            s.conversion_applied,
            s.file_id,
            s.raw_header
        FROM staging_facts s
        WHERE s.entity_id = ? {file_filter}
    """, params_base).fetchall()

    # Index resolved conflicts: (field, period) → winning staging_id
    resolved = conn.execute("""
        SELECT canonical_field, period, resolution_staging_id
        FROM conflicts
        WHERE entity_id = ? AND state = 'RESOLVED'
    """, [entity_id]).fetchall()
    resolved_map = {(r[0], r[1]): r[2] for r in resolved}

    # Index all conflicts: (field, period) → True if any open conflict
    open_conflicts = conn.execute("""
        SELECT canonical_field, period
        FROM conflicts
        WHERE entity_id = ? AND state = 'OPEN'
    """, [entity_id]).fetchall()
    open_set = {(r[0], r[1]) for r in open_conflicts}

    # For multi-value fields: pick which staging row to promote
    # Group staging rows by (field, period)
    from collections import defaultdict
    grouped: dict[tuple, list] = defaultdict(list)
    for row in staging_rows:
        key = (row[1], row[2])  # (field, period)
        grouped[key].append(row)

    promoted = 0
    for (field, period), rows in grouped.items():
        if (field, period) in open_set:
            continue  # still needs resolution

        if len(rows) == 1:
            chosen = rows[0]
        else:
            # Pick the resolved one
            winning_id = resolved_map.get((field, period))
            chosen = next((r for r in rows if r[0] == winning_id), rows[0])

        (staging_id, canonical_field, period_str, value_normalised, currency,
         original_unit, conversion_factor, conversion_applied, source_file_id, raw_header) = chosen

        fact_id = f"f_{entity_slug}_{period_str}_{canonical_field}"
        note = None
        if len(rows) > 1:
            note = f"Conflict resolved: user selected staging_id={staging_id}"

        conn.execute("""
            INSERT OR REPLACE INTO live_facts (
                fact_id, entity_id, entity_slug, canonical_field, period,
                value_normalised, currency, original_unit, conversion_factor,
                conversion_applied, source_file_id, staging_id,
                is_derived, authoritative_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE, ?)
        """, [
            fact_id, entity_id, entity_slug, canonical_field, period_str,
            value_normalised, currency, original_unit, conversion_factor,
            conversion_applied, source_file_id, staging_id, note,
        ])
        promoted += 1

    conn.commit()
    return promoted


# ── Derived KPI computation ───────────────────────────────────────────────────

def compute_derived_kpis(
    conn: duckdb.DuckDBPyConnection,
    entity_id: int,
    entity_slug: str,
) -> int:
    """
    Deterministically compute derived KPIs from live facts.
    Only runs on fields where is_derived=True in CANONICAL_FIELDS.
    Returns count of derived facts written.
    """
    derived_count = 0

    # gross_profit = revenue_net - cogs
    derived_count += _compute_binary(
        conn, entity_id, entity_slug,
        result_field="gross_profit",
        field_a="revenue_net", field_b="cogs", operation="subtract"
    )

    # gross_margin_pct = gross_profit / revenue_net * 100
    derived_count += _compute_ratio_pct(
        conn, entity_id, entity_slug,
        result_field="gross_margin_pct",
        numerator="gross_profit", denominator="revenue_net"
    )

    # ebitda_margin_pct = ebitda / revenue_net * 100
    derived_count += _compute_ratio_pct(
        conn, entity_id, entity_slug,
        result_field="ebitda_margin_pct",
        numerator="ebitda", denominator="revenue_net"
    )

    # pat_margin_pct = pat / revenue_net * 100
    derived_count += _compute_ratio_pct(
        conn, entity_id, entity_slug,
        result_field="pat_margin_pct",
        numerator="pat", denominator="revenue_net"
    )

    conn.commit()
    return derived_count


def _compute_binary(
    conn, entity_id, entity_slug, result_field, field_a, field_b, operation
) -> int:
    rows = conn.execute("""
        SELECT a.period, a.value_normalised, b.value_normalised, a.currency,
               a.original_unit, a.source_file_id
        FROM live_facts a
        JOIN live_facts b ON a.entity_id = b.entity_id AND a.period = b.period
        WHERE a.entity_id = ?
          AND a.canonical_field = ?
          AND b.canonical_field = ?
          AND a.is_derived = FALSE
          AND b.is_derived = FALSE
    """, [entity_id, field_a, field_b]).fetchall()

    count = 0
    for (period, val_a, val_b, currency, orig_unit, file_id) in rows:
        if operation == "subtract":
            result = val_a - val_b
        elif operation == "add":
            result = val_a + val_b
        else:
            continue

        fact_id = f"f_{entity_slug}_{period}_{result_field}"
        conn.execute("""
            INSERT OR REPLACE INTO live_facts (
                fact_id, entity_id, entity_slug, canonical_field, period,
                value_normalised, currency, original_unit, conversion_factor,
                conversion_applied, source_file_id, is_derived, derived_from
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0, FALSE, ?, TRUE, ?)
        """, [fact_id, entity_id, entity_slug, result_field, period,
              result, currency, orig_unit, file_id, [field_a, field_b]])
        count += 1
    return count


def _compute_ratio_pct(
    conn, entity_id, entity_slug, result_field, numerator, denominator
) -> int:
    rows = conn.execute("""
        SELECT a.period, a.value_normalised, b.value_normalised, a.source_file_id
        FROM live_facts a
        JOIN live_facts b ON a.entity_id = b.entity_id AND a.period = b.period
        WHERE a.entity_id = ?
          AND a.canonical_field = ?
          AND b.canonical_field = ?
    """, [entity_id, numerator, denominator]).fetchall()

    count = 0
    for (period, num_val, den_val, file_id) in rows:
        if den_val == 0:
            continue
        pct = (num_val / den_val) * 100.0
        fact_id = f"f_{entity_slug}_{period}_{result_field}"
        conn.execute("""
            INSERT OR REPLACE INTO live_facts (
                fact_id, entity_id, entity_slug, canonical_field, period,
                value_normalised, currency, original_unit, conversion_factor,
                conversion_applied, source_file_id, is_derived, derived_from
            ) VALUES (?, ?, ?, ?, ?, ?, 'PERCENTAGE', '%', 1.0, FALSE, ?, TRUE, ?)
        """, [fact_id, entity_id, entity_slug, result_field, period,
              pct, file_id, [numerator, denominator]])
        count += 1
    return count
