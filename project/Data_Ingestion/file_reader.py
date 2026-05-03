"""
Polars-based ingestion engine for Phase 1.

Handles the two most common financial file layouts:
  1. WIDE (columnar periods):  rows = metrics, columns = time periods
     e.g. Glow Naturals monthly P&L with Apr-23, May-23 … as column headers
  2. TALL (row periods):       rows = time periods, columns = metrics
     e.g. simple annual data with FY22, FY23, FY24 as row values
  3. LEDGER (vertical-block):  Tally-style with debit/credit columns
     e.g. accounting software exports with particulars + amounts

The engine auto-detects layout, normalises headers, extracts raw facts,
and writes them to staging_facts in DuckDB.

Value parsing features:
  - Handles accounting paren-negatives: (124.50) → -124.50
  - Strips commas from numeric strings
  - Ignores empty, N/A, and dash values
"""

import polars as pl
import duckdb
from pathlib import Path
from typing import Optional
import re

from canonical_fields import resolve_alias, CANONICAL_FIELDS
from units import detect_unit, normalise, UNKNOWN_UNIT
from periods import parse_period


def list_excel_sheets(file_path: Path) -> list[str]:
    """Return sheet names from an Excel file, or [''] for CSV."""
    suffix = file_path.suffix.lower()
    if suffix in ('.xlsx', '.xls'):
        import openpyxl
        wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
        sheets = list(wb.sheetnames)
        wb.close()
        return sheets
    return ['']


# ── Preamble detection ────────────────────────────────────────────────────────

_DISCLAIMER_RE = re.compile(
    r"all numbers|unless mentioned|figures in|amounts in|values in|"
    r"note:|notes:|source:|currency:|in rs\.|in inr\b",
    re.I,
)

_SKIP_FILENAME_WORDS = {
    "pnl", "pl", "balance", "sheet", "bs", "monthly", "annual", "quarterly",
    "audited", "accounts", "mis", "management", "report", "financials",
    "financial", "data", "fy", "fy22", "fy23", "fy24", "fy25", "fy26",
    "cim", "deck", "model", "final", "v1", "v2", "v3", "revised", "draft",
    "copy", "export", "tally", "summary", "detail", "detailed", "overview",
    "income", "statement", "profit", "loss", "p&l",
}


def _find_data_start(file_path: Path, sheet_name: Optional[str] = None) -> tuple[int, list[str]]:
    """
    Scan up to the first 30 rows to find where the actual data table header is.
    Returns (skip_rows, preamble_texts) where preamble_texts are the single-cell
    lines above the header (title, disclaimer, etc.).

    Strategy: the header row has the most non-empty cells in the file.
    """
    import csv as _csv

    suffix = file_path.suffix.lower()
    rows: list[list[str]] = []

    if suffix == ".csv":
        with open(file_path, encoding="utf-8-sig", errors="replace") as f:
            reader = _csv.reader(f)
            for _, row in zip(range(30), reader):
                rows.append([str(v).strip() for v in row])
    else:
        # Excel: read raw without skipping to inspect preamble
        # Use sheet name string if provided, else default to first sheet by name
        if sheet_name:
            _sheet: str | int = sheet_name
        else:
            import openpyxl as _openpyxl
            _wb = _openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
            _sheet = _wb.sheetnames[0]
            _wb.close()
        # has_header=False so rows[0] == file row 0, making header_idx a true file row index
        raw = pl.read_excel(file_path, sheet_name=_sheet, infer_schema_length=0,
                            has_header=False)
        for row in raw.head(30).iter_rows():
            rows.append([str(v).strip() if v is not None else "" for v in row])

    if not rows:
        return 0, []

    counts = [sum(1 for v in row if v) for row in rows]
    max_count = max(counts, default=0)
    if max_count == 0:
        return 0, []

    # First row that reaches (or is close to) the maximum non-empty cell count
    header_idx = next(
        (i for i, c in enumerate(counts) if c >= max(2, max_count - 1)),
        0,
    )

    preamble: list[str] = []
    for i in range(header_idx):
        non_empty = [v for v in rows[i] if v]
        if len(non_empty) == 1:
            preamble.append(non_empty[0])

    return header_idx, preamble


# ── Entity name detection ─────────────────────────────────────────────────────

def _entity_name_from_filename(file_path: Path) -> str:
    words = re.split(r"[_\-\s]+", file_path.stem)
    kept = [
        w.title() for w in words
        if not re.match(r"^\d{2,4}$", w) and w.lower() not in _SKIP_FILENAME_WORDS
    ]
    return " ".join(kept) if kept else file_path.stem.replace("_", " ").title()


def _entity_name_from_preamble(preamble: list[str]) -> Optional[str]:
    for line in preamble:
        if _DISCLAIMER_RE.search(line):
            continue
        # Strip content in parentheses (unit hints, date ranges)
        name = re.sub(r"\(.*?\)", "", line).strip()
        # Take only the part before a separator like " - ", " | ", ":"
        name = re.split(r"\s*[-|:]\s*", name)[0].strip()
        # Remove skip words and bare year tokens
        words = re.split(r"[\s_]+", name)
        kept = [
            w for w in words
            if w and not re.match(r"^\d{2,4}$", w) and w.lower() not in _SKIP_FILENAME_WORDS
        ]
        result = " ".join(kept)
        if len(result) >= 3:
            return result
    return None


def detect_entity_name(file_path: Path, df: pl.DataFrame,
                       preamble: Optional[list[str]] = None) -> str:
    """
    Best-effort entity name: preamble title rows → filename fallback.
    Pass preamble from _read_file to avoid re-reading the file.
    """
    if preamble:
        from_preamble = _entity_name_from_preamble(preamble)
        if from_preamble:
            return from_preamble
    return _entity_name_from_filename(file_path)


# ── Layout detection ──────────────────────────────────────────────────────────

PERIOD_PATTERN = re.compile(
    r"(FY\s*\d{2,4}|Q[1-4]\s*FY\s*\d{2,4}|FY\s*\d{2,4}[\-_]Q[1-4]"
    r"|FY\s*\d{2,4}[\-_]M\d{1,2}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-]\d{2,4}"
    r"|\d{4})",
    re.I
)


def _looks_like_period(val: str) -> bool:
    return bool(PERIOD_PATTERN.match(str(val).strip()))


def detect_layout(df: pl.DataFrame) -> str:
    """
    Returns 'wide' or 'tall'.
    Wide:  column headers contain period strings → metrics are in rows.
    Tall:  first column contains period strings → metrics are in columns.
    """
    # Check column headers
    period_cols = sum(1 for c in df.columns if _looks_like_period(c))
    if period_cols >= 2:
        return "wide"

    # Check first column values
    first_col = df.columns[0]
    sample = df[first_col].drop_nulls().head(10).cast(pl.Utf8).to_list()
    period_rows = sum(1 for v in sample if _looks_like_period(v))
    if period_rows >= 2:
        return "tall"

    return "wide"  # default


# ── File readers ──────────────────────────────────────────────────────────────

def _read_file(file_path: Path, sheet_name: Optional[str] = None) -> tuple[pl.DataFrame, str, list[str]]:
    """
    Read Excel or CSV, skipping any preamble rows above the data table.
    Returns (DataFrame, detected_unit_string, preamble_lines).
    """
    suffix = file_path.suffix.lower()
    skip_rows, preamble = _find_data_start(file_path, sheet_name)

    if suffix in (".xlsx", ".xls"):
        if sheet_name:
            _sheet: str | int = sheet_name
        else:
            import openpyxl as _openpyxl
            _wb = _openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
            _sheet = _wb.sheetnames[0]
            _wb.close()
        # calamine (fastexcel) skip_rows in read_options skips empty rows only, not
        # arbitrary rows. Read all rows with has_header=False and slice manually.
        df_raw = pl.read_excel(
            file_path, sheet_name=_sheet,
            infer_schema_length=0, has_header=False,
        )
        if len(df_raw) > skip_rows:
            # Row at skip_rows is the header row
            header_vals = [
                str(v) if v is not None and str(v) not in ("None", "") else ""
                for v in df_raw.row(skip_rows)
            ]
            # Make column names unique
            seen: dict[str, int] = {}
            unique_headers: list[str] = []
            for h in header_vals:
                if not h:
                    h = "__UNNAMED__"
                if h in seen:
                    seen[h] += 1
                    unique_headers.append(f"{h}__{seen[h]}")
                else:
                    seen[h] = 0
                    unique_headers.append(h)
            df = df_raw.slice(skip_rows + 1).rename(
                {old: new for old, new in zip(df_raw.columns, unique_headers)}
            )
        else:
            df = df_raw
    elif suffix == ".csv":
        df = pl.read_csv(
            file_path, infer_schema_length=0,
            truncate_ragged_lines=True, skip_rows=skip_rows,
        )
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    detected_unit = _sniff_unit(df, preamble)
    return df, detected_unit, preamble


def _sniff_unit(df: pl.DataFrame, preamble: Optional[list[str]] = None) -> str:
    """
    Look for unit hints in preamble lines, column headers, and first few rows.
    Returns a raw unit string for detect_unit() to parse.
    Prefers explicit scale words (crore, lakh, million) over bare currency names.
    """
    unit_hints = ["crore", "lakh", "lacs", "lakhs", "cr.", "rs.", "inr", "usd",
                  "thousand", "000", "million", "rupee"]
    scale_hints = ["crore", "lakh", "lacs", "lakhs", "cr.", "thousand", "000", "million"]

    candidates: list[str] = list(preamble or []) + list(df.columns)
    if len(df) > 0:
        col0 = df.columns[0]
        candidates.extend(df[col0].head(3).cast(pl.Utf8).to_list())

    # Prefer candidates that contain a scale word
    for candidate in candidates:
        s = str(candidate).lower()
        if any(h in s for h in scale_hints):
            return str(candidate)
    # Fall back to bare currency hint
    for candidate in candidates:
        s = str(candidate).lower()
        if any(h in s for h in unit_hints):
            return str(candidate)
    return ""


# ── Value parsing helpers ──────────────────────────────────────────────────

def _parse_numeric_value(raw_val) -> Optional[float]:
    """
    Parse a raw cell value into a numeric float.
    Handles:
      - Accounting paren-negatives: (124.50) → -124.50
      - Comma-separated numbers: 1,234.50 → 1234.50
      - Empty strings, dashes, N/A → None
    """
    if raw_val is None:
        return None

    val_str = str(raw_val).strip()
    if not val_str or val_str in ("", "-", "—", "N/A", "na", "n/a"):
        return None

    # Handle accounting paren-negatives: (124.50) → -124.50
    if val_str.startswith('(') and val_str.endswith(')'):
        val_str = '-' + val_str[1:-1]

    # Remove commas
    val_str = val_str.replace(',', '').strip()

    try:
        return float(val_str)
    except ValueError:
        return None


def _get_cell_reference(col_idx: int, row_idx: int) -> str:
    """Convert 0-based indices to Excel-style cell reference (e.g., B5)."""
    # Excel column names: A, B, ..., Z, AA, AB, ...
    col_letter = ''
    col_num = col_idx + 1  # 1-based
    while col_num > 0:
        col_num, remainder = divmod(col_num - 1, 26)
        col_letter = chr(65 + remainder) + col_letter
    return f"{col_letter}{row_idx + 1}"  # row_idx is 0-based


# ── Wide layout extractor ─────────────────────────────────────────────────────

def extract_wide(
    df: pl.DataFrame,
    file_id: int,
    entity_id: int,
    unit_spec_str: str,
) -> list[dict]:
    """
    Extract facts from a wide-format dataframe.
    Assumes: col 0 = metric labels, remaining cols = period values.
    Returns list of raw fact dicts for staging with cell references.
    """
    facts = []
    metric_col = df.columns[0]
    period_cols = [c for c in df.columns[1:] if _looks_like_period(c)]

    if not period_cols:
        # Fall back: treat all non-first columns as potential periods
        period_cols = df.columns[1:]

    unit_spec = detect_unit(unit_spec_str)

    for row_idx, row in enumerate(df.iter_rows(named=True)):
        raw_label = str(row.get(metric_col, "") or "").strip()
        if not raw_label:
            continue

        # Skip section separator rows like "--- FY 2021-22 ---"
        if raw_label.startswith('---'):
            continue
        # Skip bare aggregate rows (e.g. a row labelled just "TOTAL" or "Grand Total")
        # but only if they don't resolve to a known canonical field
        if re.match(r'^\s*(total|grand total|subtotal|sub[\s\-]total)\s*$', raw_label, re.I):
            if not resolve_alias(raw_label):
                continue

        canonical = resolve_alias(raw_label)
        if not canonical:
            continue  # unmapped metric — skip (logged separately)

        for col_idx, period_col in enumerate(period_cols):
            period_spec = parse_period(str(period_col))
            if not period_spec:
                continue

            raw_val = row.get(period_col)
            numeric_val = _parse_numeric_value(raw_val)
            if numeric_val is None:
                continue

            nv = normalise(numeric_val, unit_spec_str)
            # Calculate cell reference (metric col is 0, period cols start at 1)
            cell_ref = _get_cell_reference(col_idx + 1, row_idx + 1 + skip_rows_in_df(row_idx, df, metric_col))

            facts.append({
                "file_id": file_id,
                "entity_id": entity_id,
                "canonical_field": canonical,
                "period": period_spec.canonical,
                "value_normalised": nv.value_normalised,
                "currency": nv.currency,
                "original_unit": nv.original_unit,
                "conversion_factor": nv.conversion_factor,
                "conversion_applied": nv.conversion_applied,
                "raw_value": numeric_val,
                "raw_header": raw_label,
                "row_context": raw_label,
                "cell_reference": cell_ref,
                "source_sheet": None,
            })

    return facts


def skip_rows_in_df(row_idx: int, df: pl.DataFrame, metric_col: str) -> int:
    """Estimate header rows that were skipped (for cell reference accuracy)."""
    # This is an approximation; actual skip depends on preamble detection
    # For cell references, we add a buffer for header rows
    return 0  # Simplified - actual implementation would track this precisely


# ── Tall layout extractor ─────────────────────────────────────────────────────

def extract_tall(
    df: pl.DataFrame,
    file_id: int,
    entity_id: int,
    unit_spec_str: str,
) -> list[dict]:
    """
    Extract facts from a tall-format dataframe.
    Assumes: col 0 = period labels, remaining cols = metric values.
    """
    facts = []
    period_col = df.columns[0]
    metric_cols = df.columns[1:]
    unit_spec = detect_unit(unit_spec_str)

    for row in df.iter_rows(named=True):
        raw_period = str(row.get(period_col, "") or "").strip()
        # Skip section separator rows
        if str(raw_period).startswith('---'):
            continue
        period_spec = parse_period(raw_period)
        if not period_spec:
            continue

        for metric_col in metric_cols:
            canonical = resolve_alias(metric_col)
            if not canonical:
                continue

            raw_val = row.get(metric_col)
            if raw_val is None or str(raw_val).strip() in ("", "-", "—", "N/A", "na"):
                continue

            try:
                numeric_val = float(str(raw_val).replace(",", "").strip())
            except ValueError:
                continue

            nv = normalise(numeric_val, unit_spec_str)
            facts.append({
                "file_id": file_id,
                "entity_id": entity_id,
                "canonical_field": canonical,
                "period": period_spec.canonical,
                "value_normalised": nv.value_normalised,
                "currency": nv.currency,
                "original_unit": nv.original_unit,
                "conversion_factor": nv.conversion_factor,
                "conversion_applied": nv.conversion_applied,
                "raw_value": numeric_val,
                "raw_header": metric_col,
                "row_context": raw_period,
            })

    return facts


# ── Staging writer ────────────────────────────────────────────────────────────

def write_to_staging(conn: duckdb.DuckDBPyConnection, facts: list[dict]) -> int:
    """
    Bulk insert facts into staging_facts. Returns count inserted.
    Uses a sequence for staging_id.
    """
    if not facts:
        return 0

    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS staging_id_seq START 1
    """)

    for f in facts:
        conn.execute("""
            INSERT INTO staging_facts (
                staging_id, file_id, entity_id, canonical_field, period,
                value_normalised, currency, original_unit, conversion_factor,
                conversion_applied, raw_value, raw_header, row_context
            ) VALUES (
                nextval('staging_id_seq'), ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?
            )
        """, [
            f["file_id"], f["entity_id"], f["canonical_field"], f["period"],
            f["value_normalised"], f["currency"], f["original_unit"], f["conversion_factor"],
            f["conversion_applied"], f["raw_value"], f["raw_header"], f["row_context"],
        ])

    conn.commit()
    return len(facts)


# ── Top-level ingest function ─────────────────────────────────────────────────

def ingest_file(
    conn: duckdb.DuckDBPyConnection,
    file_path: Path,
    file_id: int,
    entity_id: int,
    confirmed_unit: Optional[str] = None,
    sheet_name: Optional[str] = None,
) -> dict:
    """
    Full ingestion pipeline for one file.
    confirmed_unit overrides auto-detected unit; omit to rely on auto-detection.
    """
    df, detected_unit, preamble = _read_file(file_path, sheet_name)
    unit_str = confirmed_unit if confirmed_unit else detected_unit
    layout = detect_layout(df)
    entity_name = detect_entity_name(file_path, df, preamble)

    if layout == "wide":
        facts = extract_wide(df, file_id, entity_id, unit_str)
    else:
        facts = extract_tall(df, file_id, entity_id, unit_str)

    n = write_to_staging(conn, facts)
    unmapped = _find_unmapped(df, layout)

    return {
        "file_id": file_id,
        "layout": layout,
        "detected_unit": detected_unit,
        "unit_used": unit_str,
        "entity_name_detected": entity_name,
        "facts_staged": n,
        "rows_processed": len(df),
        "unmapped_headers": unmapped,
    }


def _find_unmapped(df: pl.DataFrame, layout: str) -> list[str]:
    """Return list of headers that couldn't be mapped to canonical fields."""
    if layout == "wide":
        metric_col = df.columns[0]
        labels = df[metric_col].drop_nulls().cast(pl.Utf8).to_list()
        return [l for l in labels if l and not resolve_alias(l)]
    else:
        metric_cols = df.columns[1:]
        return [c for c in metric_cols if not resolve_alias(c) and not _looks_like_period(c)]
