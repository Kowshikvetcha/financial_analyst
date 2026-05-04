"""
Stage 7: Onboarding Conversation Gate.

Before facts are promoted to live, the system presents a summary of:
  - Files ingested
  - Facts extracted per file
  - Conflicts resolved
  - Any validation warnings

User must acknowledge before files transition to LIVE.

Non-interactive (EOFError) runs auto-proceed.
"""

import duckdb
from units import format_for_display


def build_onboarding_summary(
    conn: duckdb.DuckDBPyConnection,
    entity_id: int,
    entity_name: str,
    file_ids: list[int],
) -> str:
    """Build human-readable onboarding summary for an entity."""
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"  ONBOARDING REVIEW — {entity_name}")
    lines.append(f"{'='*60}")

    # Files summary
    for fid in file_ids:
        row = conn.execute("""
            SELECT filename, sheet_name, state, detected_unit
            FROM source_files WHERE file_id = ?
        """, [fid]).fetchone()
        if not row:
            continue
        filename, sheet_name, state, detected_unit = row
        sheet_label = f" [{sheet_name}]" if sheet_name else ""
        unit_label = f" ({detected_unit})" if detected_unit else " (auto-detected unit)"
        lines.append(f"\n  {filename}{sheet_label}{unit_label}")
        lines.append(f"  State: {state}")

        # Facts count
        fact_row = conn.execute("""
            SELECT COUNT(*) FROM staging_facts WHERE file_id = ?
        """, [fid]).fetchone()
        lines.append(f"  Facts staged: {fact_row[0] if fact_row else 0}")

    # Overall staging count
    total_facts = conn.execute("""
        SELECT COUNT(*) FROM staging_facts WHERE entity_id = ?
    """, [entity_id]).fetchone()[0]
    lines.append(f"\n  Total facts for {entity_name}: {total_facts}")

    # Conflicts summary
    conflict_rows = conn.execute("""
        SELECT COUNT(*) FROM conflicts
        WHERE entity_id = ? AND state = 'OPEN'
    """, [entity_id]).fetchone()[0]
    if conflict_rows:
        lines.append(f"  ⚠ {conflict_rows} unresolved conflict(s)")

    resolved_rows = conn.execute("""
        SELECT COUNT(*) FROM conflicts
        WHERE entity_id = ? AND state = 'RESOLVED'
    """, [entity_id]).fetchone()[0]
    if resolved_rows:
        lines.append(f"  {resolved_rows} conflict(s) resolved")

    # Sample facts
    sample = conn.execute("""
        SELECT canonical_field, period, value_normalised, currency, original_unit
        FROM staging_facts
        WHERE entity_id = ?
        ORDER BY canonical_field, period
        LIMIT 10
    """, [entity_id]).fetchall()

    if sample:
        lines.append(f"\n  Sample facts:")
        for s in sample[:8]:
            field, period, val, currency, orig_unit = s
            formatted = format_for_display(val, currency)
            lines.append(f"    · {field} | {period} = {formatted} ({orig_unit})")
        if len(sample) > 8:
            lines.append(f"    … and {len(sample) - 8} more")

    lines.append(f"\n{'='*60}")
    return "\n".join(lines)


def run_onboarding_gate(
    conn: duckdb.DuckDBPyConnection,
    entity_id: int,
    entity_name: str,
    file_ids: list[int],
) -> bool:
    """
    Run interactive onboarding gate. Prints summary and waits for user acknowledgment.

    Returns True if user acknowledges (or non-interactive).
    Returns False if user declines.

    Design: soft gate — user must explicitly acknowledge. Non-interactive auto-proceeds.
    """
    summary = build_onboarding_summary(conn, entity_id, entity_name, file_ids)
    print(summary)

    print("\n  Please review the above summary.")
    print("  Facts will be promoted to LIVE and become queryable.")

    try:
        response = input("\n  Proceed to LIVE? [Y/n]: ").strip().lower()
        if response in ("n", "no"):
            return False
        return True
    except EOFError:
        # Non-interactive run — auto-proceed
        print("  Non-interactive run — auto-proceeding.")
        return True
    except KeyboardInterrupt:
        return False

    return True