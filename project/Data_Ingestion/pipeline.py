"""
Phase 1 pipeline orchestrator.

Drop files into project/input_files/ and run.
Entity names, units, and layout are all auto-detected from file content.

Usage:
    python pipeline.py               # ingest everything in input_files/
    python pipeline.py --mock        # run with generated mock test data
    python pipeline.py --report-only # print live_facts summary only
"""

import sys
import io
import duckdb
from pathlib import Path
from collections import defaultdict

# Ensure UTF-8 output on Windows terminals
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

from schema import (
    get_connection, initialise_schema,
    get_or_create_entity, register_file, update_file_state
)
from file_reader import ingest_file, detect_entity_name, _read_file, list_excel_sheets
from conflict_resolver import (
    detect_conflicts, save_conflicts, resolve_conflict,
    promote_to_live, compute_derived_kpis
)
from units import format_for_display

# Stage 5 — LLM Schema Mapper
from llm_mapper import llm_map_with_cache, get_cached_mapping
# Stage 6 — Validation Gate
from validation_gate import validate_staging_facts, print_validation_report


_SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


def _slugify(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _file_type(path: Path) -> str:
    return path.suffix.lstrip(".").lower()


def _scan_input_files(input_dir: Path) -> list[Path]:
    files = sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS
    )
    return files


def _group_files_by_entity(files: list[Path]) -> dict[str, list[Path]]:
    """
    Auto-detect entity name for each file and group files with the same name.
    Reads just enough of each file to detect the name — does not ingest.
    """
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        try:
            df, _, preamble = _read_file(path)
            name = detect_entity_name(path, df, preamble)
        except Exception as e:
            print(f"  WARNING: could not read {path.name} — {e}")
            continue
        groups[name].append(path)
    return dict(groups)


def _resolve_conflicts_interactive(conn, entity_name: str, entity_id: int) -> None:
    conflicts = detect_conflicts(conn, entity_id)
    if not conflicts:
        return

    cids = save_conflicts(conn, conflicts)
    print(f"\n  {len(conflicts)} conflict(s) detected for {entity_name}.")

    for c, cid in zip(conflicts, cids):
        print(f"\n  CONFLICT: {c.canonical_field}  |  period {c.period}")
        for i, opt in enumerate(c.options):
            print(f"    [{i+1}]  {opt['raw_value']} {opt['original_unit']}"
                  f"  —  {opt['source_filename']}")

        while True:
            try:
                choice = input(f"  Choose [1–{len(c.options)}]: ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(c.options):
                    resolve_conflict(conn, cid, c.options[idx]["staging_id"])
                    print(f"  Resolved.")
                    break
            except EOFError:
                # Non-interactive run — auto-resolve to first option
                resolve_conflict(conn, cid, c.options[0]["staging_id"])
                print(f"  Auto-resolved (non-interactive) → option 1.")
                break
            except (ValueError, KeyboardInterrupt):
                pass
            print(f"  Please enter a number between 1 and {len(c.options)}.")


def run_pipeline(db_path: Path = Path("financial_agent.duckdb"), mock: bool = False) -> None:
    print("\n" + "=" * 60)
    print("  FINANCIAL AI AGENT — Phase 1 Pipeline")
    print("=" * 60)

    conn = get_connection(db_path)
    initialise_schema(conn)

    if mock:
        _run_mock(conn)
    else:
        _run_from_input_files(conn)

    print_summary(conn)
    conn.close()


def _run_from_input_files(conn: duckdb.DuckDBPyConnection) -> None:
    input_dir = Path(__file__).parent.parent / "input_files"

    if not input_dir.exists():
        input_dir.mkdir(parents=True)

    files = _scan_input_files(input_dir)

    if not files:
        print(f"\n  No files found in {input_dir}")
        print("  Drop your CSV or XLSX files there and re-run.")
        return

    print(f"\n  Found {len(files)} file(s) in input_files/. Detecting entities...")

    groups = _group_files_by_entity(files)

    for entity_name, entity_files in groups.items():
        print(f"\n  Entity: \"{entity_name}\"  ({len(entity_files)} file(s))")
        for f in entity_files:
            print(f"    · {f.name}")

    entity_records: list[tuple[int, list[int], str]] = []

    for entity_name, entity_files in groups.items():
        entity_id = get_or_create_entity(conn, entity_name)
        file_ids = []

        for path in entity_files:
            sheets = list_excel_sheets(path)  # [''] for CSV
            for sheet in sheets:
                sheet_label = f" [{sheet}]" if sheet else ""
                display_name = f"{path.name}{sheet_label}"
                print(f"\n── {entity_name}  ·  {display_name} " + "─" * 20)

                file_id = register_file(
                    conn, entity_id, display_name, str(path),
                    _file_type(path), detected_unit="",
                    sheet_name=sheet if sheet else None
                )
                update_file_state(conn, file_id, "SCHEMA_MAPPED")

                try:
                    result = ingest_file(
                        conn, path, file_id, entity_id,
                        sheet_name=sheet if sheet else None
                    )
                except Exception as e:
                    print(f"  ERROR ingesting {display_name}: {e}")
                    continue

                print(f"  Layout:        {result['layout']}")
                print(f"  Unit detected: {result['detected_unit'] or '(none — stored as absolute)'}")
                print(f"  Facts staged:  {result['facts_staged']}")
                if result["unmapped_headers"]:
                    shown = result["unmapped_headers"][:8]
                    print(f"  Unmapped:      {shown}")

                    # Stage 5 — LLM Schema Mapper: attempt to map unmapped headers
                    try:
                        file_ctx = {
                            "entity_name": entity_name,
                            "filename": path.name,
                            "sheet_name": sheet or "N/A",
                            "detected_unit": result["detected_unit"] or "",
                            "layout": result["layout"],
                        }
                        llm_mappings = llm_map_with_cache(result["unmapped_headers"], file_ctx)
                        mapped_count = sum(1 for m in llm_mappings if m.canonical_field)
                        print(f"  LLM mapped:   {mapped_count}/{len(result['unmapped_headers'])} unmapped headers")
                        if mapped_count > 0:
                            for m in llm_mappings:
                                if m.canonical_field and m.confidence in ("high", "medium"):
                                    print(f"    → \"{m.raw_header}\" → {m.canonical_field} ({m.confidence})")
                    except RuntimeError as e:
                        if "ANTHROPIC_API_KEY not set" in str(e):
                            print(f"  LLM mapper skipped: {e}")
                        else:
                            print(f"  LLM mapper error: {e}")
                    except Exception as e:
                        print(f"  LLM mapper error: {e}")

                file_ids.append(file_id)

        entity_records.append((entity_id, file_ids, entity_name))

    print("\n── Conflict Resolution " + "─" * 38)
    for entity_id, _, entity_name in entity_records:
        _resolve_conflicts_interactive(conn, entity_name, entity_id)

    # Stage 6 — Validation Gate: check staging facts before promotion
    print("\n── Stage 6: Validation Gate " + "─" * 34)
    validation_issues_total = 0
    for entity_id, file_ids, entity_name in entity_records:
        report = validate_staging_facts(conn, entity_id, file_ids)
        if report.has_warnings or report.has_errors:
            print(f"  {entity_name}:")
            print(print_validation_report(report))
            validation_issues_total += len(report.issues)
        else:
            print(f"  {entity_name}: all checks passed")
    if validation_issues_total > 0:
        print(f"\n  ⚠ {validation_issues_total} validation issue(s) found — review before trusting data")

    print("\n── Promoting to live_facts " + "─" * 34)
    for entity_id, file_ids, entity_name in entity_records:
        for fid in file_ids:
            update_file_state(conn, fid, "LIVE")
        slug = _slugify(entity_name)
        n = promote_to_live(conn, entity_id, slug)
        d = compute_derived_kpis(conn, entity_id, slug)
        print(f"  {entity_name}: {n} live facts, {d} derived KPIs")


def _run_mock(conn: duckdb.DuckDBPyConnection) -> None:
    mock_dir = Path(__file__).parent / "mock_data"
    mock_dir.mkdir(exist_ok=True)
    from generate import create_glow_naturals, create_krishnan_engineering

    glow_path  = mock_dir / "glow_naturals_monthly_pnl.csv"
    krishnan_1 = mock_dir / "krishnan_audited_accounts.csv"
    krishnan_2 = mock_dir / "krishnan_management_mis.csv"
    create_glow_naturals(glow_path)
    create_krishnan_engineering(krishnan_1, krishnan_2)

    records = []
    for entity_name, paths in [
        ("Glow Naturals",                    [glow_path]),
        ("Krishnan Engineering Works Pvt Ltd", [krishnan_1, krishnan_2]),
    ]:
        entity_id = get_or_create_entity(conn, entity_name)
        file_ids = []
        for path in paths:
            print(f"\n── {entity_name}  ·  {path.name} " + "─" * 20)
            file_id = register_file(conn, entity_id, path.name, str(path), "csv", sheet_name=None)
            update_file_state(conn, file_id, "SCHEMA_MAPPED")
            result = ingest_file(conn, path, file_id, entity_id)
            print(f"  Facts staged: {result['facts_staged']}")
            file_ids.append(file_id)
        records.append((entity_id, file_ids, entity_name))

    print("\n── Conflict Detection (mock) " + "─" * 32)
    for entity_id, _, entity_name in records:
        conflicts = detect_conflicts(conn, entity_id)
        if not conflicts:
            print(f"  {entity_name}: no conflicts")
            continue
        cids = save_conflicts(conn, conflicts)
        for c, cid in zip(conflicts, cids):
            chosen = next(
                (o for o in c.options if "audited" in o["source_filename"]),
                c.options[0]
            )
            resolve_conflict(conn, cid, chosen["staging_id"])
            print(f"  Auto-resolved {c.canonical_field} | {c.period} → {chosen['source_filename']}")

    print("\n── Promoting to live_facts (mock) " + "─" * 27)
    for entity_id, file_ids, entity_name in records:
        for fid in file_ids:
            update_file_state(conn, fid, "LIVE")
        slug = _slugify(entity_name)
        n = promote_to_live(conn, entity_id, slug)
        d = compute_derived_kpis(conn, entity_id, slug)
        print(f"  {entity_name}: {n} live facts, {d} derived KPIs")


def print_summary(conn: duckdb.DuckDBPyConnection) -> None:
    print("\n" + "=" * 60)
    print("  LIVE FACTS SUMMARY")
    print("=" * 60)

    entities = conn.execute("SELECT entity_id, entity_name FROM entities").fetchall()
    for (eid, ename) in entities:
        print(f"\n▶ {ename}")
        rows = conn.execute("""
            SELECT canonical_field, period, value_normalised, currency,
                   original_unit, is_derived
            FROM live_facts
            WHERE entity_id = ?
            ORDER BY canonical_field, period
        """, [eid]).fetchall()

        if not rows:
            print("  (no live facts)")
            continue

        from collections import defaultdict
        by_field: dict[str, list] = defaultdict(list)
        for r in rows:
            by_field[r[0]].append(r)

        for field, field_rows in by_field.items():
            derived_tag = " [derived]" if field_rows[0][5] else ""
            periods_str = ", ".join(
                f"{r[1]}={format_for_display(r[2], r[3])}" for r in field_rows[:3]
            )
            ellipsis = " …" if len(field_rows) > 3 else ""
            print(f"  {field}{derived_tag}: {periods_str}{ellipsis}")

    open_c     = conn.execute("SELECT COUNT(*) FROM conflicts WHERE state='OPEN'").fetchone()[0]
    resolved_c = conn.execute("SELECT COUNT(*) FROM conflicts WHERE state='RESOLVED'").fetchone()[0]
    total      = conn.execute("SELECT COUNT(*) FROM live_facts").fetchone()[0]
    print(f"\n  Conflicts — open: {open_c}, resolved: {resolved_c}")
    print(f"  Total live facts: {total}")
    print("\n✓ Phase 1 pipeline complete.")


if __name__ == "__main__":
    if "--report-only" in sys.argv:
        conn = get_connection()
        print_summary(conn)
        conn.close()
    elif "--mock" in sys.argv:
        run_pipeline(mock=True)
    else:
        run_pipeline(mock=False)
