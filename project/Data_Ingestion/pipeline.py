from __future__ import annotations

import sys
import io
import duckdb
from pathlib import Path
from collections import defaultdict

# Ensure UTF-8 output on Windows terminals
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from schema import (
    get_connection,
    initialise_schema,
    get_or_create_entity,
    register_file,
    update_file_state,
    insert_schema_mapping,
)
from canonical_fields import register_llm_mapping
from file_reader import ingest_file, detect_entity_name, _read_file, list_excel_sheets
from conflict_resolver import (
    detect_conflicts,
    save_conflicts,
    resolve_conflict,
    promote_to_live,
    compute_derived_kpis,
)
from units import format_for_display
from llm_mapper import llm_map_with_cache
from validation_gate import validate_staging_facts, print_validation_report

_SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


def _slugify(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _file_type(path: Path) -> str:
    return path.suffix.lstrip(".").lower()


def _scan_input_files(input_dir: Path) -> list[Path]:
    return sorted(
        p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS
    )


def _group_files_by_entity(files: list[Path]) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        try:
            df, _, preamble, _ = _read_file(path)
            name = detect_entity_name(path, df, preamble)
        except Exception as e:
            print(f"  WARNING: could not read {path.name} - {e}")
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
        print(f"\n  CONFLICT: {c.canonical_field} | period {c.period}")
        for i, opt in enumerate(c.options):
            print(f"    [{i+1}] {opt['raw_value']} {opt['original_unit']} - {opt['source_filename']}")

        while True:
            try:
                choice = input(f"  Choose [1-{len(c.options)}]: ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(c.options):
                    resolve_conflict(conn, cid, c.options[idx]["staging_id"])
                    print("  Resolved.")
                    break
            except EOFError:
                resolve_conflict(conn, cid, c.options[0]["staging_id"])
                print("  Auto-resolved (non-interactive) -> option 1.")
                break
            except (ValueError, KeyboardInterrupt):
                pass
            print(f"  Please enter a number between 1 and {len(c.options)}.")


def run_pipeline(mock: bool = False, skip_qualitative: bool = False) -> None:
    print("\n" + "=" * 60)
    print("  FINANCIAL AI AGENT - Phase 1 Pipeline")
    print("=" * 60)

    db_path = Path(__file__).parent / "financial_agent.duckdb"
    conn = get_connection(db_path)
    initialise_schema(conn)

    if mock:
        _run_mock(conn)
    else:
        _run_from_input_files(conn)

    print_summary(conn)

    if not skip_qualitative:
        print("\n" + "=" * 60)
        print("  Phase 2 - Qualitative Context Pipeline")
        print("=" * 60)
        from qualitative import run_qualitative_pipeline

        result = run_qualitative_pipeline(conn)
        print(f"\n  Phase 2 complete: {result['files_processed']} files, {result['chunks_total']} chunks")
        print("  Phase 2 done.")

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
        print(f"\n  Entity: \"{entity_name}\" ({len(entity_files)} file(s))")
        for f in entity_files:
            print(f"    - {f.name}")

    entity_records: list[tuple[int, list[int], str]] = []

    for entity_name, entity_files in groups.items():
        entity_id = get_or_create_entity(conn, entity_name)
        file_ids = []

        for path in entity_files:
            sheets = list_excel_sheets(path)
            for sheet in sheets:
                sheet_label = f" [{sheet}]" if sheet else ""
                display_name = f"{path.name}{sheet_label}"
                print(f"\n-- {entity_name} · {display_name} " + "-" * 20)

                file_id = register_file(
                    conn,
                    entity_id,
                    display_name,
                    str(path),
                    _file_type(path),
                    detected_unit="",
                    sheet_name=sheet if sheet else None,
                )
                update_file_state(conn, file_id, "SCHEMA_MAPPED")

                try:
                    result = ingest_file(conn, path, file_id, entity_id, sheet_name=sheet if sheet else None)
                except Exception as e:
                    print(f"  ERROR ingesting {display_name}: {e}")
                    continue

                print(f"  Layout:        {result['layout']}")
                print(f"  Unit detected: {result['detected_unit'] or '(none - stored as absolute)'}")
                print(f"  Facts staged:  {result['facts_staged']}")

                if result["unmapped_headers"]:
                    shown = result["unmapped_headers"][:8]
                    print(f"  Unmapped:      {shown}")

                    high_confidence_mappings = []
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
                        print(
                            f"  LLM mapped:   {mapped_count}/{len(result['unmapped_headers'])} unmapped headers"
                        )

                        if mapped_count > 0:
                            for m in llm_mappings:
                                if m.canonical_field and m.confidence in ("high", "medium", "cached"):
                                    print(f"    -> \"{m.raw_header}\" -> {m.canonical_field} ({m.confidence})")
                                    register_llm_mapping(m.raw_header, m.canonical_field)
                                    insert_schema_mapping(
                                        conn,
                                        file_id,
                                        m.raw_header,
                                        m.canonical_field,
                                        confidence=m.confidence,
                                        mapped_by="llm",
                                    )
                                    high_confidence_mappings.append(m)
                    except RuntimeError as e:
                        if "ANTHROPIC_API_KEY not set" in str(e):
                            print(f"  LLM mapper skipped: {e}")
                        else:
                            print(f"  LLM mapper error: {e}")
                    except Exception as e:
                        print(f"  LLM mapper error: {e}")

                    if high_confidence_mappings:
                        print(
                            f"  Re-ingesting to capture {len(high_confidence_mappings)} LLM-mapped headers..."
                        )
                        conn.execute("DELETE FROM staging_facts WHERE file_id = ?", [file_id])
                        conn.commit()
                        result = ingest_file(conn, path, file_id, entity_id, sheet_name=sheet if sheet else None)
                        print(f"  Re-ingest: {result['facts_staged']} facts staged")

                file_ids.append(file_id)

        entity_records.append((entity_id, file_ids, entity_name))

    print("\n-- Conflict Resolution " + "-" * 38)
    for entity_id, _, entity_name in entity_records:
        _resolve_conflicts_interactive(conn, entity_name, entity_id)

    for _, file_ids, _ in entity_records:
        for fid in file_ids:
            update_file_state(conn, fid, "AWAITING_ACKNOWLEDGMENT")

    print("\n-- Stage 6: Validation Gate " + "-" * 34)
    validation_reports = []
    for entity_id, file_ids, entity_name in entity_records:
        report = validate_staging_facts(conn, entity_id, file_ids)
        validation_reports.append((entity_id, file_ids, entity_name, report))
        if report.has_warnings or report.has_errors:
            print(f"  {entity_name}:")
            print(print_validation_report(report))
        else:
            print(f"  {entity_name}: all checks passed")

    total_errors = sum(r.has_errors for _, _, _, r in validation_reports)
    if total_errors > 0:
        print(f"\n  WARNING: {total_errors} blocking error(s) found.")
        print("  Options:")
        print("    [1] Proceed anyway (soft block)")
        print("    [2] Abort pipeline")
        try:
            choice = input("  Choose [1/2] (default=1): ").strip()
            if choice == "2":
                print("  Pipeline aborted by user.")
                return
        except EOFError:
            print("  Non-interactive run - proceeding by default.")
        except KeyboardInterrupt:
            print("\n  Pipeline aborted.")
            return

    print("\n-- Stage 7: Onboarding Gate " + "-" * 36)
    for entity_id, file_ids, entity_name in entity_records:
        print(f"  Reviewing {entity_name}...")
        try:
            from onboarding_gate import run_onboarding_gate

            acknowledged = run_onboarding_gate(conn, entity_id, entity_name, file_ids)
            if not acknowledged:
                print(f"  {entity_name}: skipped by user - not promoting to LIVE")
                continue
        except ImportError:
            print("  onboarding_gate.py not found - skipping acknowledgment gate")
        except Exception as e:
            print(f"  Onboarding gate error: {e} - proceeding")

        for fid in file_ids:
            update_file_state(conn, fid, "LIVE")
        slug = _slugify(entity_name)
        n = promote_to_live(conn, entity_id, slug)
        d = compute_derived_kpis(conn, entity_id, slug)
        print(f"  {entity_name}: {n} live facts, {d} derived KPIs -> LIVE")


def _run_mock(conn: duckdb.DuckDBPyConnection) -> None:
    mock_dir = Path(__file__).parent / "mock_data"
    mock_dir.mkdir(exist_ok=True)
    from generate import create_glow_naturals, create_krishnan_engineering

    glow_path = mock_dir / "glow_naturals_monthly_pnl.csv"
    krishnan_1 = mock_dir / "krishnan_audited_accounts.csv"
    krishnan_2 = mock_dir / "krishnan_management_mis.csv"
    create_glow_naturals(glow_path)
    create_krishnan_engineering(krishnan_1, krishnan_2)

    records = []
    for entity_name, paths in [
        ("Glow Naturals", [glow_path]),
        ("Krishnan Engineering Works Pvt Ltd", [krishnan_1, krishnan_2]),
    ]:
        entity_id = get_or_create_entity(conn, entity_name)
        file_ids = []
        for path in paths:
            print(f"\n-- {entity_name} · {path.name} " + "-" * 20)
            file_id = register_file(conn, entity_id, path.name, str(path), "csv", sheet_name=None)
            update_file_state(conn, file_id, "SCHEMA_MAPPED")
            result = ingest_file(conn, path, file_id, entity_id)
            print(f"  Facts staged: {result['facts_staged']}")
            file_ids.append(file_id)
        records.append((entity_id, file_ids, entity_name))

    print("\n-- Conflict Detection (mock) " + "-" * 32)
    for entity_id, _, entity_name in records:
        conflicts = detect_conflicts(conn, entity_id)
        if not conflicts:
            print(f"  {entity_name}: no conflicts")
            continue
        cids = save_conflicts(conn, conflicts)
        for c, cid in zip(conflicts, cids):
            chosen = next((o for o in c.options if "audited" in o["source_filename"]), c.options[0])
            resolve_conflict(conn, cid, chosen["staging_id"])
            print(f"  Auto-resolved {c.canonical_field} | {c.period} -> {chosen['source_filename']}")

    print("\n-- Promoting to live_facts (mock) " + "-" * 27)
    for _, file_ids, _ in records:
        for fid in file_ids:
            update_file_state(conn, fid, "AWAITING_ACKNOWLEDGMENT")

    for entity_id, file_ids, entity_name in records:
        print(f"  {entity_name}: AWAITING_ACKNOWLEDGMENT")
        from onboarding_gate import build_onboarding_summary

        summary = build_onboarding_summary(conn, entity_id, entity_name, file_ids)
        print(summary)
        print("  Non-interactive run - auto-proceeding to LIVE.")

        for fid in file_ids:
            update_file_state(conn, fid, "LIVE")
        slug = _slugify(entity_name)
        n = promote_to_live(conn, entity_id, slug)
        d = compute_derived_kpis(conn, entity_id, slug)
        print(f"  {entity_name}: {n} live facts, {d} derived KPIs -> LIVE")


def print_summary(conn: duckdb.DuckDBPyConnection) -> None:
    print("\n" + "=" * 60)
    print("  LIVE FACTS SUMMARY")
    print("=" * 60)

    entities = conn.execute("SELECT entity_id, entity_name FROM entities").fetchall()
    for (eid, ename) in entities:
        print(f"\n> {ename}")
        rows = conn.execute(
            """
            SELECT canonical_field, period, value_normalised, currency,
                   original_unit, is_derived
            FROM live_facts
            WHERE entity_id = ?
            ORDER BY canonical_field, period
            """,
            [eid],
        ).fetchall()

        if not rows:
            print("  (no live facts)")
            continue

        from collections import defaultdict

        by_field: dict[str, list] = defaultdict(list)
        for r in rows:
            by_field[r[0]].append(r)

        for field, field_rows in by_field.items():
            derived_tag = " [derived]" if field_rows[0][5] else ""
            periods_str = ", ".join(f"{r[1]}={format_for_display(r[2], r[3])}" for r in field_rows[:3])
            ellipsis = " ..." if len(field_rows) > 3 else ""
            print(f"  {field}{derived_tag}: {periods_str}{ellipsis}")

    open_c = conn.execute("SELECT COUNT(*) FROM conflicts WHERE state='OPEN'").fetchone()[0]
    resolved_c = conn.execute("SELECT COUNT(*) FROM conflicts WHERE state='RESOLVED'").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM live_facts").fetchone()[0]
    print(f"\n  Conflicts - open: {open_c}, resolved: {resolved_c}")
    print(f"  Total live facts: {total}")
    print("\nPhase 1 pipeline complete.")


if __name__ == "__main__":
    skip_qualitative = "--skip-qualitative" in sys.argv

    if "--report-only" in sys.argv:
        conn = get_connection()
        print_summary(conn)
        conn.close()
    elif "--mock" in sys.argv:
        run_pipeline(mock=True, skip_qualitative=skip_qualitative)
    else:
        run_pipeline(mock=False, skip_qualitative=skip_qualitative)
