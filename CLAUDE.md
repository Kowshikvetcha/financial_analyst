# CLAUDE.md — Deterministic Financial AI Co-Pilot

> Always update this file, `docs/file_reference.md`, and `PROGRESS.md` at the end of every turn where code changes were made.

## What This Project Is

A local-first, zero-hallucination financial intelligence system for Private Equity analysis. Users upload heterogeneous financial files (CSV, Excel, PDF) and query them via natural language chat.

**Core invariant:** The LLM is a router and language formatter only. All math, unit conversion, SQL retrieval, and data validation is deterministic Python. The LLM never sees raw numbers and never performs arithmetic.

## Project Status

Phase 1 (quantitative ingestion pipeline) is complete. Phases 2–5 have not been started.

See [`docs/file_reference.md`](docs/file_reference.md) for a detailed breakdown of every file.
See [`PROGRESS.md`](PROGRESS.md) for stage-by-stage completion status.

## Architecture — 5 Phases

| Phase | Role | Status |
|-------|------|--------|
| 1 | Raw file → clean DuckDB facts | ✅ Complete |
| 2 | PDF/DOCX → ChromaDB qualitative chunks | ❌ Not started |
| 3 | 6 deterministic Python tools (only path to data) | ❌ Not started |
| 4 | LLM orchestration (routes questions, formats answers) | ❌ Not started |
| 5 | Streamlit chat UI | ❌ Not started |

## Phase 1 — What's Built

Entry point: `python project/Data_Ingestion/pipeline.py`

The ingestion pipeline lives in `project/Data_Ingestion/`. Modules:

- `pipeline.py` — orchestrator; auto-detects entity/unit/layout; iterates all sheets per Excel file
- `schema.py` — DuckDB table definitions and helper functions; includes `sheet_name` on source_files; SHA-256 checksum
- `canonical_fields.py` — 58 canonical metric definitions + ~330 alias mappings; session-level LLM alias cache
- `file_reader.py` — Polars-based file reader; preamble skip; WIDE/TALL layout detection; multi-sheet support; cell references
- `units.py` — unit detection and normalisation (INR/USD, Lakhs/Crores/Millions)
- `periods.py` — period string normalisation; handles FY 2023-24, FY24A, Q1'22, 31.03.24, date ranges
- `conflict_resolver.py` — conflict detection, resolution, staging → live promotion, derived KPI computation
- `generate.py` — generates mock test data (Glow Naturals, Krishnan Engineering)
- `llm_mapper.py` — Stage 5 LLM schema mapper; calls Anthropic API for unmapped headers; persistent JSON cache
- `validation_gate.py` — Stage 6 validation gate; sum checks, unit magnitude, period swings, sign consistency; soft block
- `onboarding_gate.py` — Stage 7 onboarding gate; prints summary, awaits user acknowledgment, gates LIVE promotion

## Phase 1 — Remaining Gap

- **Mixed units within a sheet**: e.g. Sharma Textiles "Other Exp Detail" — rows in Lakhs mixed with rows in absolute Rs. not detected.

## Database

Active database: `project/Data_Ingestion/financial_agent.duckdb`

The root-level `financial_agent.duckdb` is a stale copy — ignore it.

### Reset database

```bash
# Delete and let pipeline recreate on next run
rm project/Data_Ingestion/financial_agent.duckdb
```

### 7-Table Schema

```
entities → source_files (+ sheet_name) → schema_mappings
                                       → staging_facts → live_facts
                                       → conflicts
                                       → ingestion_log
```

All monetary values stored as absolute base units with `original_unit` + `conversion_factor` columns.

### Fact ID Format

`f_{entity_slug}_{period}_{canonical_field}`

## Key Design Rules (Non-Negotiable Per PRD)

1. **No LLM math** — all arithmetic is Python (`Decimal`, not `float` for Phase 3+)
2. **No raw SQL from the LLM** — Phase 3 tools expose parameterised functions only
3. **Unit-explicit storage** — every fact row carries `(value, unit, original_unit, conversion_factor, conversion_applied)`
4. **Reconcile-then-answer** — cross-source conflicts surfaced to user, never silently resolved
5. **No data leaves the machine** — raw financial data never sent to cloud
6. **State machine enforcement** — facts not queryable until file reaches `LIVE` state

## Canonical Formats

**Periods:**
- Annual: `FY24` (Indian fiscal year — April 2023 to March 2024)
- Monthly: `FY24-M06` (month 6 = September 2023, April = month 1)
- Quarterly: `FY24-Q2`
- Calendar year: `CY2023`

**Supported input formats → canonical:**
- `FY 2023-24`, `FY2021-22` → `FY24`, `FY22`
- `FY2024A`, `FY2025E`, `FY2026P` → `FY24`, `FY25`, `FY26`
- `Q1'22` → `FY22-Q1`
- `31.03.24`, `31-Mar-2022` → `FY24`, `FY22`
- `1-Apr-23 to 31-Mar-24` → `FY24`

**Units stored:**
- `INR_absolute`, `INR_lakhs`, `INR_crore`
- `USD_absolute`, `USD_thousands`, `USD_millions`, `USD_billions`

## Running Phase 1

```bash
cd project
pip install -r requirements.txt

# Ingest files from project/input_files/ — no config needed, fully auto-detected
python Data_Ingestion/pipeline.py

# Run with built-in mock test data
python Data_Ingestion/pipeline.py --mock

# Print live_facts summary only
python Data_Ingestion/pipeline.py --report-only
```

### Adding your own files

1. Drop CSV or XLSX files into `project/input_files/`
2. Run `python Data_Ingestion/pipeline.py`

Everything is auto-detected:
- **Entity name** — from title row in file, or derived from filename (strips `pnl`, `audited`, `monthly`, etc.)
- **Unit** — from preamble/header rows (`"Rs. Lakhs"`, `"INR Crore"`, `"USD thousands"`)
- **Layout** — WIDE (rows=metrics, cols=periods) or TALL (rows=periods, cols=metrics)
- **Periods** — from column headers or first column values
- **All sheets** — Excel files have every sheet ingested independently with its own unit context

Files with the same detected entity name are grouped and cross-checked for conflicts.

## Example Input Files

Located in `project/example_input_files/`. Results from last run:

| Entity | File | Live Facts | Notes |
|--------|------|-----------|-------|
| Glow Naturals | `Glow_Naturals_monthly_pnl_2023_24.csv` | 180 | 144 raw + 36 derived |
| ZenithOps Inc. | `ZenithOps_Financials_DataRoom.xlsx` | 168 | Summary + SaaS Metrics sheets |
| M/s Sharma Textiles | `Sharma_Textiles_Financials_FY24_v3_FINAL_revised.xlsx` | 33 | 3 sheets |
| M/s Krishnan Engineering | `Engineering_company_3yr_pnl_AS_PROVIDED_by_owner.xlsx` | 3 | Tally export only; ledger sheet needs LLM mapper |
| — | `ZenithOps_CIM_Project_Atlas.docx` | — | Phase 2 (qualitative) — not yet handled |

## Phase 3 — Tools to Build

| Tool | Purpose |
|------|---------|
| `fetch_metric(entity_id, metric, period, unit_out)` | Single fact lookup |
| `calculate_variance(entity_id, metric, period_1, period_2, unit_out)` | Period-over-period change |
| `calculate_ratio(entity_id, numerator, denominator, period, unit_out)` | Margins and ratios |
| `search_context(entity_id, query, period_filter, metric_filter)` | ChromaDB semantic search |
| `list_sources(entity_id, metric, period)` | All sources for a fact |
| `list_available_metrics(entity_id)` | What's queryable for this entity |

All tools raise typed exceptions on missing data. Every result carries a citation envelope.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Data processing | Python, Polars |
| Structured storage | DuckDB (local) |
| Vector storage | ChromaDB (Phase 2, not started) |
| PDF parsing | PyMuPDF (Phase 2, not started) |
| Orchestration | LangChain / AutoGen (Phase 4, not started) |
| LLM | Enterprise cloud API — Claude or GPT-4o |
| UI | Streamlit (Phase 5, not started) |
