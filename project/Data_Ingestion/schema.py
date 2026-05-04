"""
DuckDB schema for Phase 1.

Tables:
  entities          - one row per company
  source_files      - one row per uploaded file (includes SHA-256 checksum)
  staging_facts     - raw facts before conflict resolution (AWAITING state)
  live_facts        - validated facts in LIVE state (the query layer hits this)
  conflicts         - detected conflicts awaiting user resolution
  schema_mappings   - per-file column → canonical field mapping (with confidence)
  ingestion_log     - full audit trail

Key design rules:
  - All monetary values stored as absolute base units with conversion metadata
  - Fact IDs are stable: f_{entity_slug}_{period}_{canonical_field}
  - State machine: UPLOADED → SCHEMA_MAPPED → AWAITING_CONFLICT_RESOLUTION → AWAITING_ACKNOWLEDGMENT → LIVE
  - SHA-256 checksum computed on file registration for integrity tracking
"""

import hashlib
import duckdb
from pathlib import Path
from typing import Optional


DB_PATH = Path(__file__).parent / "financial_agent.duckdb"


def get_connection(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path))


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hash of a file for integrity tracking."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read()
            if not chunk:
                break
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def initialise_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables if they don't exist. Safe to call repeatedly."""

    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS entity_id_seq START 1;
        CREATE SEQUENCE IF NOT EXISTS file_id_seq START 1;
        CREATE SEQUENCE IF NOT EXISTS fact_id_seq START 1;
        CREATE SEQUENCE IF NOT EXISTS conflict_id_seq START 1;
    """)

    # ── Entities ──────────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            entity_id       INTEGER PRIMARY KEY DEFAULT nextval('entity_id_seq'),
            entity_name     TEXT NOT NULL,
            entity_slug     TEXT NOT NULL UNIQUE,  -- normalised lowercase key
            aliases         TEXT[],                -- other names seen in files
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Source files ──────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_files (
            file_id         INTEGER PRIMARY KEY DEFAULT nextval('file_id_seq'),
            entity_id       INTEGER REFERENCES entities(entity_id),
            filename        TEXT NOT NULL,
            file_path       TEXT NOT NULL,
            file_type       TEXT,                  -- 'xlsx' | 'csv' | 'pdf'
            checksum        TEXT,                  -- SHA-256 hash of file contents
            detected_unit   TEXT,                  -- e.g. 'INR Lakh'
            confirmed_unit  TEXT,                  -- after user confirmation
            sheet_name      TEXT,                  -- Excel sheet name, NULL for CSV
            state           TEXT DEFAULT 'UPLOADED',
                -- UPLOADED → SCHEMA_MAPPED → AWAITING_CONFLICT_RESOLUTION → AWAITING_ACKNOWLEDGMENT → LIVE
            ingested_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            live_at         TIMESTAMP
        )
    """)

    # ── Schema mappings ───────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_mappings (
            mapping_id      INTEGER PRIMARY KEY,
            file_id         INTEGER REFERENCES source_files(file_id),
            raw_header      TEXT NOT NULL,
            canonical_field TEXT,                  -- NULL = unmapped
            confidence      TEXT DEFAULT 'high',   -- 'high' | 'low' | 'manual'
            mapped_by       TEXT DEFAULT 'alias',  -- 'alias' | 'llm' | 'manual'
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Staging facts (pre-validation) ────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS staging_facts (
            staging_id          INTEGER PRIMARY KEY,
            file_id             INTEGER REFERENCES source_files(file_id),
            entity_id           INTEGER REFERENCES entities(entity_id),
            canonical_field     TEXT NOT NULL,
            period              TEXT NOT NULL,      -- canonical period string
            value_normalised    DOUBLE NOT NULL,    -- always in base currency unit
            currency            TEXT NOT NULL,
            original_unit       TEXT NOT NULL,
            conversion_factor   DOUBLE NOT NULL,
            conversion_applied  BOOLEAN NOT NULL,
            raw_value           DOUBLE,             -- original value from file
            raw_header          TEXT,               -- original column header
            row_context         TEXT,               -- surrounding row label/context
            cell_reference      TEXT,               -- e.g. 'B5' for cell-level citation
            source_sheet        TEXT,               -- Excel sheet name for citation
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Live facts (the query-ready table) ───────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS live_facts (
            fact_id             TEXT PRIMARY KEY,
                -- format: f_{entity_slug}_{period}_{canonical_field}
            entity_id           INTEGER REFERENCES entities(entity_id),
            entity_slug         TEXT NOT NULL,
            canonical_field     TEXT NOT NULL,
            period              TEXT NOT NULL,
            value_normalised    DOUBLE NOT NULL,
            currency            TEXT NOT NULL,
            original_unit       TEXT NOT NULL,
            conversion_factor   DOUBLE NOT NULL,
            conversion_applied  BOOLEAN NOT NULL,
            source_file_id      INTEGER REFERENCES source_files(file_id),
            staging_id          INTEGER REFERENCES staging_facts(staging_id),
            cell_reference      TEXT,               -- e.g. 'B5' for cell-level citation
            source_sheet        TEXT,               -- Excel sheet name for citation
            is_derived          BOOLEAN DEFAULT FALSE,
            derived_from        TEXT[],
            authoritative_note  TEXT,               -- e.g. "user selected over file_2"
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Conflicts ─────────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conflicts (
            conflict_id         INTEGER PRIMARY KEY DEFAULT nextval('conflict_id_seq'),
            entity_id           INTEGER REFERENCES entities(entity_id),
            canonical_field     TEXT NOT NULL,
            period              TEXT NOT NULL,
            staging_ids         INTEGER[],          -- all conflicting staging_ids
            values_seen         TEXT,               -- JSON summary of values+sources
            resolution_staging_id INTEGER,          -- which staging_id won
            resolved_by         TEXT,               -- 'user' | 'auto_single_source'
            resolved_at         TIMESTAMP,
            state               TEXT DEFAULT 'OPEN' -- 'OPEN' | 'RESOLVED'
        )
    """)

    # ── Ingestion log ─────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_log (
            log_id      INTEGER PRIMARY KEY,
            file_id     INTEGER REFERENCES source_files(file_id),
            event       TEXT NOT NULL,
            detail      TEXT,
            logged_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Qualitative chunks (Phase 2) ───────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qualitative_chunks (
            chunk_id                INTEGER PRIMARY KEY,
            file_id                 INTEGER REFERENCES source_files(file_id),
            entity_id               INTEGER REFERENCES entities(entity_id),
            chunk_index             INTEGER NOT NULL,
            region_type             TEXT NOT NULL,
                -- footer_notes | inline_annotation | narrative | table
            chunk_type              TEXT NOT NULL,
                -- narrative | footnote | caption | table
            section_path            TEXT,
            linked_fact_ids         TEXT,            -- JSON array
            linked_periods          TEXT,            -- JSON array
            linked_metrics          TEXT,            -- JSON array
            contains_numerical_claim BOOLEAN NOT NULL DEFAULT FALSE,
            numerical_claims        TEXT,             -- JSON array of {number, type, context}
            raw_text                TEXT,
            chroma_document_id      TEXT,            -- NULL for inline annotations
            created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    print("OK DuckDB schema initialised")


def get_or_create_entity(
    conn: duckdb.DuckDBPyConnection,
    entity_name: str,
) -> int:
    """Return entity_id for entity_name, creating if necessary."""
    slug = _slugify(entity_name)
    existing = conn.execute(
        "SELECT entity_id FROM entities WHERE entity_slug = ?", [slug]
    ).fetchone()
    if existing:
        return existing[0]
    conn.execute(
        "INSERT INTO entities (entity_name, entity_slug) VALUES (?, ?)",
        [entity_name, slug]
    )
    conn.commit()
    row = conn.execute(
        "SELECT entity_id FROM entities WHERE entity_slug = ?", [slug]
    ).fetchone()
    return row[0]


def _slugify(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def register_file(
    conn: duckdb.DuckDBPyConnection,
    entity_id: int,
    filename: str,
    file_path: str,
    file_type: str,
    detected_unit: Optional[str] = None,
    sheet_name: Optional[str] = None,
    checksum: Optional[str] = None,
) -> int:
    """Insert a source_files row and return its file_id.
    If file_path exists and checksum not provided, computes SHA-256 automatically.
    """
    # Auto-compute checksum if file exists and checksum not provided
    if checksum is None:
        p = Path(file_path)
        if p.exists():
            checksum = compute_sha256(p)

    conn.execute("""
        INSERT INTO source_files (entity_id, filename, file_path, file_type, checksum, detected_unit, sheet_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [entity_id, filename, file_path, file_type, checksum, detected_unit, sheet_name])
    conn.commit()
    row = conn.execute(
        "SELECT file_id FROM source_files ORDER BY file_id DESC LIMIT 1"
    ).fetchone()
    return row[0]


def update_file_state(
    conn: duckdb.DuckDBPyConnection,
    file_id: int,
    state: str,
) -> None:
    conn.execute(
        "UPDATE source_files SET state = ? WHERE file_id = ?", [state, file_id]
    )
    if state == "LIVE":
        conn.execute(
            "UPDATE source_files SET live_at = CURRENT_TIMESTAMP WHERE file_id = ?",
            [file_id]
        )
    conn.commit()


def insert_schema_mapping(
    conn: duckdb.DuckDBPyConnection,
    file_id: int,
    raw_header: str,
    canonical_field: Optional[str],
    confidence: str = "medium",
    mapped_by: str = "llm",
) -> None:
    """Insert a schema mapping into the schema_mappings table."""
    conn.execute("""
        INSERT INTO schema_mappings (file_id, raw_header, canonical_field, confidence, mapped_by)
        VALUES (?, ?, ?, ?, ?)
    """, [file_id, raw_header, canonical_field, confidence, mapped_by])
    conn.commit()


def get_files_needing_acknowledgment(conn: duckdb.DuckDBPyConnection) -> list[int]:
    """Return list of file_ids in AWAITING_ACKNOWLEDGMENT state."""
    rows = conn.execute(
        "SELECT file_id FROM source_files WHERE state = 'AWAITING_ACKNOWLEDGMENT'"
    ).fetchall()
    return [r[0] for r in rows]


# ── Phase 2 — Qualitative helpers ───────────────────────────────────────────

def register_qualitative_file(
    conn: duckdb.DuckDBPyConnection,
    entity_id: int,
    filename: str,
    file_path: str,
    file_type: str,          # 'pdf' or 'docx'
    checksum: Optional[str] = None,
) -> int:
    """
    Insert a source_files row for a qualitative (PDF/DOCX) file.
    State starts as REGISTERED; updated to LIVE after chunking.
    """
    if checksum is None:
        p = Path(file_path)
        if p.exists():
            checksum = compute_sha256(p)

    conn.execute("""
        INSERT INTO source_files (entity_id, filename, file_path, file_type, checksum, state)
        VALUES (?, ?, ?, ?, ?, 'REGISTERED')
    """, [entity_id, filename, file_path, file_type, checksum])
    conn.commit()
    row = conn.execute(
        "SELECT file_id FROM source_files ORDER BY file_id DESC LIMIT 1"
    ).fetchone()
    return row[0]


def get_qualitative_chunks(
    conn: duckdb.DuckDBPyConnection,
    entity_id: int,
    chunk_ids: Optional[list[int]] = None,
) -> list[dict]:
    """
    Fetch chunk metadata from DuckDB (not the ChromaDB vectors).
    Optionally filter to specific chunk_ids.
    """
    if chunk_ids:
        rows = conn.execute("""
            SELECT chunk_id, file_id, entity_id, chunk_index, region_type, chunk_type,
                   section_path, linked_fact_ids, linked_periods, linked_metrics,
                   contains_numerical_claim, numerical_claims, raw_text, chroma_document_id
            FROM qualitative_chunks
            WHERE entity_id = ? AND chunk_id = ANY(?)
            ORDER BY chunk_index
        """, [entity_id, chunk_ids]).fetchall()
    else:
        rows = conn.execute("""
            SELECT chunk_id, file_id, entity_id, chunk_index, region_type, chunk_type,
                   section_path, linked_fact_ids, linked_periods, linked_metrics,
                   contains_numerical_claim, numerical_claims, raw_text, chroma_document_id
            FROM qualitative_chunks
            WHERE entity_id = ?
            ORDER BY chunk_index
        """, [entity_id]).fetchall()

    columns = [
        "chunk_id", "file_id", "entity_id", "chunk_index", "region_type", "chunk_type",
        "section_path", "linked_fact_ids", "linked_periods", "linked_metrics",
        "contains_numerical_claim", "numerical_claims", "raw_text", "chroma_document_id"
    ]
    return [dict(zip(columns, row)) for row in rows]
