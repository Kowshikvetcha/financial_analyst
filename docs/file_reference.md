# File Reference — Deterministic Financial AI Co-Pilot

> Always keep this file up to date after any code changes.

## Project Structure

```
financial_analyst/
├── CLAUDE.md                      # Project context for Claude Code sessions
├── PROGRESS.md                    # Stage-by-stage completion tracker
├── docs/                          # Documentation + architecture diagrams
├── project/
│   ├── Data_Ingestion/            # Phase 1 ingestion engine
│   │   ├── mock_data/             # Generated test CSV files
│   │   ├── pipeline.py            # Orchestrator entry point
│   │   ├── schema.py              # DuckDB schema + helpers
│   │   ├── canonical_fields.py    # Metric registry + alias map
│   │   ├── file_reader.py         # File reading + layout detection
│   │   ├── units.py               # Unit normalisation
│   │   ├── periods.py             # Period string normalisation
│   │   ├── conflict_resolver.py   # Conflict detection + promotion
│   │   ├── generate.py            # Mock data generator
│   │   ├── llm_mapper.py          # Stage 5 — LLM schema mapper
│   │   ├── validation_gate.py     # Stage 6 — Validation gate
│   │   └── onboarding_gate.py     # Stage 7 — Onboarding gate
│   ├── input_files/               # Drop real files here to ingest
│   ├── example_input_files/       # Reference example files (do not modify)
│   └── requirements.txt
├── financial_agent.duckdb         # Stale root-level DB — ignore
└── test.ipynb                     # Empty notebook
```

---

## Core Python Modules (`project/Data_Ingestion/`)

### `pipeline.py`
**Role:** Phase 1 orchestrator — scans `input_files/`, auto-detects everything, runs full ingestion.

**Modes:**
```bash
python pipeline.py               # ingest files from project/input_files/ (fully auto-detected)
python pipeline.py --mock        # run with generated mock test data
python pipeline.py --report-only # print live_facts summary only
```

**Flow:**
1. Scan `input_files/` for CSV/XLSX files
2. Auto-detect entity name per file (from preamble title row or filename)
3. Group files by entity name
4. For each entity: iterate all sheets → register each sheet as a `source_files` row → ingest
5. Detect and interactively resolve conflicts
6. Promote staging → live_facts; compute derived KPIs
7. Print summary

**Key functions:**
- `_group_files_by_entity(files)` — reads each file's preamble to detect entity name
- `_resolve_conflicts_interactive(conn, entity_name, entity_id)` — prompts user for conflict choices; auto-resolves if non-interactive (EOFError)
- `run_pipeline(db_path, mock)` — main entry point
- `print_summary(conn)` — prints live_facts grouped by entity

---

### `schema.py`
**Role:** DuckDB schema definition and database initialisation.

**Tables:**

| Table | Purpose |
|-------|---------|
| `entities` | One row per company |
| `source_files` | One row per file/sheet; includes `sheet_name`; tracks state machine |
| `schema_mappings` | Raw header → canonical field mapping with confidence |
| `staging_facts` | Raw facts before conflict resolution; not queryable |
| `live_facts` | Validated, query-ready facts with stable `fact_id` |
| `conflicts` | Cross-source disagreements awaiting resolution |
| `ingestion_log` | Full audit trail |

**Key functions:**
- `get_connection(db_path)` — DuckDB connection
- `initialise_schema(conn)` — creates all tables + sequences if not exist
- `get_or_create_entity(conn, name)` — upserts company row
- `register_file(conn, entity_id, filename, file_path, file_type, detected_unit, sheet_name)` — inserts source_files row; `sheet_name` used for Excel sheet citation
- `update_file_state(conn, file_id, state)` — advances file state machine

**State machine:** `UPLOADED → SCHEMA_MAPPED → AWAITING_CONFLICT_RESOLUTION → LIVE`
(Note: full enforcement not yet implemented — files go straight to LIVE after pipeline run)

---

### `canonical_fields.py`
**Role:** Master registry of all financial metrics the system understands.

**Contents:**
- `MetricCategory` enum — REVENUE, COST, PROFITABILITY, BALANCE_SHEET, CASH_FLOW, OPERATIONAL
- `UnitType` enum — CURRENCY, PERCENTAGE, COUNT, RATIO
- `CanonicalField` dataclass — name, display_name, category, unit_type, is_derived, derived_from
- `CANONICAL_FIELDS` dict — **58 registered metrics** across all categories
- `ALIAS_MAP` — **~330 raw header variants** → canonical field mappings

**New fields added (vs original):**
`returns_refunds`, `subscription_revenue`, `professional_services_revenue`, `new_arr`, `churned_arr`, `platform_fees`, `logistics_expense`, `packaging_expense`, `rd_expense`, `net_dollar_retention`, `gross_dollar_retention`, `logo_churn_count`

**Key function:**
- `resolve_alias(raw_header)` — returns canonical field name or `None`; strips parenthetical qualifiers (e.g. `"ARR ($000s)"` → `"arr"`) and leading whitespace (indented labels)

**Limitation:** This is a static dictionary. Any header not in `ALIAS_MAP` is silently dropped. The LLM schema mapper (PRD Stage 5) is not yet built.

---

### `file_reader.py`
**Role:** File reading, preamble detection, layout detection, multi-sheet support, fact extraction.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `list_excel_sheets(file_path)` | Returns all sheet names; `['']` for CSV |
| `_find_data_start(file_path, sheet_name)` | Detects header row index by finding the row with the most non-empty cells; returns `(skip_rows, preamble_lines)` |
| `detect_entity_name(file_path, df, preamble)` | Extracts company name from preamble title rows; falls back to filename keyword stripping |
| `detect_layout(df)` | Returns `'wide'` or `'tall'` based on period patterns in headers/first column |
| `_read_file(file_path, sheet_name)` | Reads file with preamble skipped; returns `(DataFrame, unit_str, preamble_lines)` |
| `_sniff_unit(df, preamble)` | Detects unit from preamble + headers; prefers scale words (crore/lakh) over bare currency |
| `extract_wide(df, file_id, entity_id, unit_spec_str)` | Extracts from wide format (rows=metrics, cols=periods); skips `---` separators and bare TOTAL rows |
| `extract_tall(df, file_id, entity_id, unit_spec_str)` | Extracts from tall format (rows=periods, cols=metrics) |
| `write_to_staging(conn, facts)` | Bulk inserts into `staging_facts` |
| `ingest_file(conn, file_path, file_id, entity_id, confirmed_unit, sheet_name)` | Full pipeline for one sheet; returns summary dict |

**Returns from `ingest_file`:**
`{file_id, layout, detected_unit, unit_used, entity_name_detected, facts_staged, rows_processed, unmapped_headers}`

---

### `units.py`
**Role:** Unit detection and normalisation — converts all monetary values to absolute base units.

**Supported units:**
- **INR:** Crore (×10M), Lakh (×100K), Thousand (×1K), Million (×1M), absolute (×1)
- **USD:** Billion (×1B), Million (×1M), Thousand (×1K), absolute (×1)

**Key types:** `UnitSpec`, `NormalisedValue`

**Key functions:**
- `detect_unit(raw_unit_str)` → `UnitSpec`
- `normalise(raw_value, raw_unit_str)` → `NormalisedValue`
- `format_for_display(absolute_value, currency, target_unit)` → formatted string

---

### `periods.py`
**Role:** Period string normalisation to canonical format.

**Canonical formats:**
- `FY24` — Indian annual (Apr 2023–Mar 2024)
- `FY24-M06` — Monthly (September 2023)
- `FY24-Q2` — Quarterly (Jul–Sep 2023)
- `CY2023` — Calendar year

**All supported input patterns:**

| Input | Canonical | Notes |
|-------|-----------|-------|
| `FY24`, `FY2024`, `FY 24` | `FY24` | |
| `FY 2023-24`, `FY2021-22` | `FY24`, `FY22` | Two-year notation — takes end year |
| `FY2024A`, `FY25E`, `FY2026P` | `FY24`, `FY25`, `FY26` | Strips actual/estimate/projected suffix |
| `Q2 FY24`, `FY24-Q2` | `FY24-Q2` | |
| `Q1'22`, `Q3'23` | `FY22-Q1`, `FY23-Q3` | Apostrophe year shorthand |
| `Apr-23`, `April 2024` | `FY24-M01`, `FY25-M01` | Month-year → fiscal month |
| `31.03.24`, `31/03/2024` | `FY24` | Date → fiscal year end |
| `31-Mar-2022`, `31 Mar 2022` | `FY22` | Date → fiscal year |
| `1-Apr-23 to 31-Mar-24` | `FY24` | Range — takes end date |
| `CY2023`, `2023` | `CY2023` | Calendar year |

**Key functions:**
- `parse_period(raw)` → `Optional[PeriodSpec]`
- `period_label(spec)` → human-readable string

---

### `conflict_resolver.py`
**Role:** Conflict detection, user-resolution, and promotion of staging facts to live.

**Key type:** `Conflict` — conflict_id, entity_id, canonical_field, period, options

**Key functions:**
- `detect_conflicts(conn, entity_id)` → `list[Conflict]`
- `save_conflicts(conn, conflicts)` → saves to `conflicts` table
- `resolve_conflict(conn, conflict_id, chosen_staging_id)` — marks RESOLVED
- `promote_to_live(conn, entity_id, entity_slug)` → promotes staging rows; sets `fact_id`
- `compute_derived_kpis(conn, entity_id, entity_slug)` → computes margins and ratios

---

### `generate.py`
**Role:** Mock data generator for `--mock` pipeline runs.

Generates:
- **Glow Naturals** — D2C, wide format, INR Lakhs, 12 months FY24, seasonality
- **Krishnan Engineering** — 2 files with intentional FY24 conflict (27.3 vs 28.1 Cr revenue)

---

### `llm_mapper.py`
**Role:** Stage 5 — LLM schema mapper. Calls Anthropic Claude API to map unmapped raw headers to canonical fields.

**Key types:**
- `SchemaMapping` — raw_header, canonical_field, confidence, reasoning, mapped_by, needs_review

**Key functions:**
- `llm_map_with_cache(unmapped_headers, file_context)` → `list[SchemaMapping]` — checks cache first, calls LLM only for uncached headers
- `get_cached_mapping(raw_header)` → `Optional[str]` — cache lookup
- `register_llm_mapping(raw_header, canonical_field)` — session-level cache registration (in `canonical_fields.py`)
- `enrich_with_llm(unmapped_headers, file_context)` → `dict[str, str]` — high-level wrapper

**Behavior:** After LLM mapping returns high/medium confidence results, pipeline:
1. Registers each mapping in `canonical_fields.register_llm_mapping()` (session cache)
2. Inserts into `schema_mappings` table with `mapped_by='llm'`
3. Re-ingests the file to pick up facts for newly mapped headers

**Requires:** `ANTHROPIC_API_KEY` environment variable. Falls back gracefully if not set.

---

### `validation_gate.py`
**Role:** Stage 6 — Validation gate. Runs deterministic checks on staging facts before promotion.

**Checks:**
- **Sum checks**: revenue_gross - returns_refunds ≈ revenue_net; margin consistency
- **Unit magnitude**: INR values in sensible ranges (catches absolute vs lakh confusion)
- **Period swings**: flags >10x changes between adjacent periods
- **Sign consistency**: revenue should be positive, expenses typically positive

**Key types:**
- `ValidationIssue` — check, severity (error/warning), entity_id, canonical_field, period, value, message, suggestion
- `ValidationReport` — entity_id, issues list, passed_checks; `has_errors`, `has_warnings` properties

**Key functions:**
- `validate_staging_facts(conn, entity_id, file_ids)` → `ValidationReport`
- `print_validation_report(report)` → formatted string

**Behavior:** Soft block — errors pause pipeline and prompt user for choice. Non-interactive runs default to proceed.

---

### `onboarding_gate.py`
**Role:** Stage 7 — Onboarding conversation gate. Presents summary of ingested data and awaits user acknowledgment before LIVE promotion.

**Key functions:**
- `build_onboarding_summary(conn, entity_id, entity_name, file_ids)` → str — human-readable summary with files, facts count, conflicts, sample facts
- `run_onboarding_gate(conn, entity_id, entity_name, file_ids)` → bool — interactive gate; returns True if acknowledged

**Behavior:**
- Prints summary of files, staged facts, conflicts, sample facts
- Prompts `[Y/n]` — user must acknowledge
- Non-interactive (EOFError) auto-proceeds
- User can decline, skipping LIVE promotion for that entity

---

## Example Input Files (`project/example_input_files/`)

Reference files used to validate the ingestion engine. Do not modify.

| File | Entity | Sheets | Key challenges |
|------|--------|--------|----------------|
| `Glow_Naturals_monthly_pnl_2023_24.csv` | Glow Naturals | — | 3-row preamble, INR absolute, notes column |
| `Sharma_Textiles_Financials_FY24_v3_FINAL_revised.xlsx` | M/s Sharma Textiles | P&L, Other Exp Detail, Balance Sheet | Different units per sheet (Lakhs vs Crore), mixed units within a sheet |
| `Engineering_company_3yr_pnl_AS_PROVIDED_by_owner.xlsx` | M/s Krishnan Engineering | 3 yr PnL, Tally export, Top customers | Ledger format (3yr PnL sheet — 0 facts, needs LLM mapper) |
| `ZenithOps_Financials_DataRoom.xlsx` | ZenithOps Inc. | 5 sheets | USD thousands, FY2020A/E/P periods, Q1'22 quarters, SaaS metrics |
| `ZenithOps_CIM_Project_Atlas.docx` | ZenithOps Inc. | — | Phase 2 (qualitative) — not yet handled |

---

## Database

**Active:** `project/Data_Ingestion/financial_agent.duckdb`

Root-level `financial_agent.duckdb` is stale — ignore it.

**Reset:** `rm project/Data_Ingestion/financial_agent.duckdb` (pipeline recreates on next run)

---

## Documentation (`docs/`)

| File | Contents |
|------|---------|
| `Financial_AI_Agent_PRD_v1.2.docx` | Full product requirements — all 5 phases, guardrails, data models, citation envelope spec |
| `Phase_Reference_Report.docx` | Concise phase-by-phase input/output reference |
| `file_reference.md` | This file |
| `financial_ai_architecture_overview.svg` | System architecture diagram |
| `phase1_detailed_flow.svg` | Phase 1 ingestion flow diagram |
| `Simplified_project_architecture.drawio` | DrawIO architecture sketch |

---

## Dependencies (`project/requirements.txt`)

| Package | Version | Use |
|---------|---------|-----|
| `polars` | 1.40.1 | DataFrame engine for file reading and normalisation |
| `duckdb` | 1.5.2 | Local OLAP database |
| `pydantic` | 2.13.3 | Data validation and models |
| `pandas` | 2.2.0 | DuckDB result handling |
| `numpy` | 1.26.4 | Numerical operations |
| `openpyxl` | 3.1.5 | Excel reading (openpyxl engine + sheet enumeration) |
| `fastexcel` | — | Calamine engine required by newer Polars for Excel reading |
