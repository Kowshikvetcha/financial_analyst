<p align="center">
  <h1 align="center">💹 Financial AI Co-Pilot</h1>
  <p align="center">
    <strong>A local-first, zero-hallucination financial intelligence system for Private Equity analysis.</strong>
  </p>
  <p align="center">
    <a href="#-quick-start">Quick Start</a> •
    <a href="#-architecture">Architecture</a> •
    <a href="#-phase-1--data-ingestion-pipeline">Phase 1 Pipeline</a> •
    <a href="#-database-schema">Database Schema</a> •
    <a href="#-roadmap">Roadmap</a>
  </p>
</p>

---

## 📖 Overview

Financial AI Co-Pilot is a **deterministic financial intelligence system** designed for Private Equity analysts. Users upload heterogeneous financial files (CSV, Excel, PDF) and query them via natural language chat — all running locally on your machine.

### Core Invariant

> **The LLM is a router and language formatter only.** All math, unit conversion, SQL retrieval, and data validation is deterministic Python. The LLM never sees raw numbers and never performs arithmetic.

### Key Features

- 🔒 **Zero data leakage** — raw financial data never leaves your machine
- 🧮 **Zero-hallucination math** — all arithmetic is deterministic Python, never LLM-generated
- 📂 **Multi-format ingestion** — CSV, Excel (multi-sheet), with PDF/DOCX planned
- 🔄 **Fully auto-detected** — entity names, units, layouts, and periods require zero configuration
- ⚖️ **Conflict resolution** — cross-source disagreements are surfaced to the user, never silently resolved
- 📊 **Unit-explicit storage** — every fact carries `(value, unit, original_unit, conversion_factor)`
- 🏷️ **58 canonical metrics** with ~330 alias mappings across Indian P&L, SaaS, manufacturing, and D2C verticals

---

## 🏗 Architecture

The system is built across **5 phases**, each with a distinct responsibility:

```
┌─────────────────────────────────────────────────────────────┐
│                    Phase 5: Streamlit Chat UI                │
├─────────────────────────────────────────────────────────────┤
│             Phase 4: LLM Orchestration (Router Only)         │
├─────────────────────────────────────────────────────────────┤
│         Phase 3: 6 Deterministic Python Tools (Only          │
│                  Authorised Path to Data)                     │
├──────────────────────────┬──────────────────────────────────┤
│  Phase 1: Quantitative   │    Phase 2: Qualitative           │
│  Pipeline → DuckDB       │    Pipeline → ChromaDB            │
│  (CSV, Excel)            │    (PDF, DOCX)                    │
└──────────────────────────┴──────────────────────────────────┘
```

| Phase | Role | Status |
|-------|------|--------|
| **1** | Raw file → clean, unit-explicit DuckDB facts | 🔄 Partial |
| **2** | PDF/DOCX → ChromaDB qualitative chunks | ❌ Not started |
| **3** | 6 deterministic Python tools (only path to data) | ❌ Not started |
| **4** | LLM orchestration (routes questions, formats answers) | ❌ Not started |
| **5** | Streamlit chat UI | ❌ Not started |

> See [`PROGRESS.md`](PROGRESS.md) for detailed stage-by-stage status.

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **pip** (package manager)
- *(Optional)* `ANTHROPIC_API_KEY` environment variable for LLM-based schema mapping

### Installation

```bash
# Clone the repository
git clone https://github.com/Kowshikvetcha/financial_analyst.git
cd financial_analyst

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
cd project
pip install -r requirements.txt
```

### Running the Pipeline

```bash
cd project

# Ingest files from project/input_files/ — no config needed
python Data_Ingestion/pipeline.py

# Run with built-in mock test data (great for first-time setup)
python Data_Ingestion/pipeline.py --mock

# Print live facts summary only (no ingestion)
python Data_Ingestion/pipeline.py --report-only
```

### Adding Your Own Files

1. Drop your CSV or XLSX files into `project/input_files/`
2. Run `python Data_Ingestion/pipeline.py`

**Everything is auto-detected:**

| What | How |
|------|-----|
| **Entity name** | From title row in file, or derived from filename (strips `pnl`, `audited`, `monthly`, etc.) |
| **Unit** | From preamble/header rows (`"Rs. Lakhs"`, `"INR Crore"`, `"USD thousands"`) |
| **Layout** | WIDE (rows=metrics, cols=periods) or TALL (rows=periods, cols=metrics) |
| **Periods** | From column headers or first column values |
| **All sheets** | Excel files have every sheet ingested independently with its own unit context |

Files with the same detected entity name are automatically grouped and cross-checked for conflicts.

---

## 📁 Project Structure

```
financial_analyst/
├── README.md                          # This file
├── CLAUDE.md                          # Project context for AI coding sessions
├── PROGRESS.md                        # Stage-by-stage completion tracker
├── .gitignore
│
├── docs/                              # Documentation & architecture diagrams
│   ├── Financial_AI_Agent_PRD_v1.2.docx       # Full product requirements
│   ├── Phase_Reference_Report.docx            # Phase-by-phase I/O reference
│   ├── file_reference.md                      # Detailed file reference
│   ├── financial_ai_architecture_overview.svg  # System architecture diagram
│   ├── phase1_detailed_flow.svg               # Phase 1 ingestion flow
│   └── Simplified_project_architecture.drawio # DrawIO architecture sketch
│
├── project/
│   ├── requirements.txt               # Python dependencies
│   ├── input_files/                   # ← Drop your files here
│   ├── example_input_files/           # Reference examples (do not modify)
│   │   ├── Glow_Naturals_monthly_pnl_2023_24.csv
│   │   ├── Sharma_Textiles_Financials_FY24_v3_FINAL_revised.xlsx
│   │   ├── Engineering_company_3yr_pnl_AS_PROVIDED_by_owner.xlsx
│   │   ├── ZenithOps_Financials_DataRoom.xlsx
│   │   └── ZenithOps_CIM_Project_Atlas.docx
│   │
│   └── Data_Ingestion/               # Phase 1 engine
│       ├── pipeline.py               # Orchestrator entry point
│       ├── schema.py                 # DuckDB schema + helpers
│       ├── canonical_fields.py       # 58 metrics + ~330 alias mappings
│       ├── file_reader.py            # File reading + layout detection
│       ├── units.py                  # Unit normalisation
│       ├── periods.py                # Period string normalisation
│       ├── conflict_resolver.py      # Conflict detection + promotion
│       ├── llm_mapper.py             # Stage 5: LLM schema mapper
│       ├── validation_gate.py        # Stage 6: Validation gate
│       └── generate.py              # Mock data generator
│
└── venv/                             # Python virtual environment
```

---

## 📊 Phase 1 — Data Ingestion Pipeline

Phase 1 is the quantitative ingestion engine. It reads structured financial files, auto-detects everything, normalises units and periods, resolves cross-source conflicts, and stores validated facts in DuckDB.

### Pipeline Flow

```
  ┌─────────────┐     ┌─────────────┐     ┌──────────────┐     ┌──────────────┐
  │  Stage 1    │     │  Stage 3    │     │   Stage 4    │     │   Stage 5    │
  │  File Reg.  │────▶│  Pre-norm.  │────▶│  Unit/Period  │────▶│  Schema Map  │
  │  + Checksum │     │  Preamble   │     │  Detection   │     │  (Alias+LLM) │
  └─────────────┘     │  Skip       │     └──────────────┘     └──────────────┘
                      └─────────────┘                                 │
                                                                      ▼
  ┌─────────────┐     ┌─────────────┐     ┌──────────────┐     ┌──────────────┐
  │  Stage 8    │     │  Stage 7    │     │   Stage 6    │     │   Staging    │
  │  Promote    │◀────│  Onboarding │◀────│  Validation  │◀────│   Facts      │
  │  to Live    │     │  (planned)  │     │  Gate        │     │   Written    │
  └─────────────┘     └─────────────┘     └──────────────┘     └──────────────┘
```

### Module Reference

#### `pipeline.py` — Orchestrator

The main entry point. Scans `input_files/`, auto-detects entities, iterates all sheets per Excel file, runs conflict resolution and validation, then promotes facts to live.

```bash
python pipeline.py               # ingest from input_files/
python pipeline.py --mock        # mock test data
python pipeline.py --report-only # summary only
```

**Key functions:**
| Function | Purpose |
|----------|---------|
| `run_pipeline(db_path, mock)` | Main entry point |
| `_group_files_by_entity(files)` | Reads preamble to detect entity name per file |
| `_resolve_conflicts_interactive(conn, entity_name, entity_id)` | Interactive conflict resolution; auto-resolves in non-interactive mode |
| `print_summary(conn)` | Prints live facts grouped by entity |

#### `schema.py` — Database Schema

Defines the 7-table DuckDB schema, connection management, and entity/file registration with SHA-256 checksums.

**Key functions:**
| Function | Purpose |
|----------|---------|
| `get_connection(db_path)` | Returns DuckDB connection |
| `initialise_schema(conn)` | Creates all tables + sequences |
| `get_or_create_entity(conn, name)` | Upserts company row |
| `register_file(conn, ...)` | Inserts source file with auto SHA-256 |
| `compute_sha256(file_path)` | Computes file integrity checksum |

#### `canonical_fields.py` — Metric Registry

Master registry of all 58 financial metrics the system understands, across 6 categories:

| Category | Example Fields |
|----------|---------------|
| **Revenue** | `revenue_gross`, `revenue_net`, `arr`, `gmv`, `subscription_revenue` |
| **Costs** | `cogs`, `opex`, `salary_expense`, `marketing_expense`, `logistics_expense` |
| **Profitability** | `gross_profit`, `ebitda`, `pat`, `gross_margin_pct`, `ebitda_margin_pct` |
| **Balance Sheet** | `accounts_receivable`, `accounts_payable`, `inventory`, `total_debt` |
| **Cash Flow** | *(to be extended)* |
| **Operational** | `headcount`, `customer_count`, `order_count`, `net_dollar_retention` |

**~330 alias mappings** cover Indian P&L, SaaS, manufacturing, and D2C terminology. The `resolve_alias(raw_header)` function strips parenthetical qualifiers (e.g. `"ARR ($000s)"` → `"arr"`) and leading whitespace.

#### `file_reader.py` — File Reading & Layout Detection

Polars-based file reader with intelligent preamble detection and dual-layout support.

| Feature | Detail |
|---------|--------|
| Preamble skip | Auto-detects header row by finding the row with most non-empty cells |
| Layout detection | WIDE (rows=metrics, cols=periods) vs TALL (rows=periods, cols=metrics) |
| Multi-sheet | Iterates all Excel sheets independently |
| Entity detection | Extracts company name from preamble; falls back to filename keywords |
| Unit sniffing | Prefers scale words (crore/lakh) over bare currency symbols |

#### `units.py` — Unit Normalisation

Converts all monetary values to absolute base units while preserving original metadata.

| Currency | Supported Scales |
|----------|-----------------|
| **INR** | Crore (×10M), Lakh (×100K), Thousand (×1K), Million (×1M), Absolute (×1) |
| **USD** | Billion (×1B), Million (×1M), Thousand (×1K), Absolute (×1) |

**Key types:** `UnitSpec`, `NormalisedValue`

#### `periods.py` — Period Normalisation

Normalises diverse date/period formats to canonical strings:

| Input Format | Canonical Output |
|-------------|-----------------|
| `FY 2023-24`, `FY2021-22` | `FY24`, `FY22` |
| `FY2024A`, `FY25E`, `FY2026P` | `FY24`, `FY25`, `FY26` |
| `Q1'22`, `Q2 FY24` | `FY22-Q1`, `FY24-Q2` |
| `Apr-23`, `April 2024` | `FY24-M01`, `FY25-M01` |
| `31.03.24`, `31-Mar-2022` | `FY24`, `FY22` |
| `1-Apr-23 to 31-Mar-24` | `FY24` |
| `CY2023`, `2023` | `CY2023` |

**Canonical formats:**
- Annual: `FY24` (Indian fiscal year — April 2023 to March 2024)
- Monthly: `FY24-M06` (month 6 = September 2023; April = month 1)
- Quarterly: `FY24-Q2`
- Calendar year: `CY2023`

#### `conflict_resolver.py` — Conflict Detection & Resolution

Detects cross-source disagreements for the same `(entity, field, period)` tuple and surfaces them to the user.

| Function | Purpose |
|----------|---------|
| `detect_conflicts(conn, entity_id)` | Finds (entity, field, period) rows with mismatched values |
| `save_conflicts(conn, conflicts)` | Persists to `conflicts` table |
| `resolve_conflict(conn, conflict_id, chosen_staging_id)` | Marks conflict as RESOLVED |
| `promote_to_live(conn, entity_id, entity_slug)` | Moves staging facts to `live_facts` |
| `compute_derived_kpis(conn, entity_id, entity_slug)` | Calculates `gross_profit`, `gross_margin_pct`, `ebitda_margin_pct`, `pat_margin_pct` |

#### `llm_mapper.py` — LLM Schema Mapper (Stage 5)

When the static alias dictionary can't match a header, this module calls Claude to suggest a mapping. Results are persisted in a JSON cache (`llm_mapping_cache.json`) to avoid redundant API calls.

> **Requires:** `ANTHROPIC_API_KEY` environment variable

| Function | Purpose |
|----------|---------|
| `llm_map_headers(headers, context)` | Calls Claude API for mapping suggestions |
| `llm_map_with_cache(headers, context)` | Cache-first mapping — only calls LLM for uncached headers |
| `get_cached_mapping(raw_header)` | Lookup from persistent JSON cache |

#### `validation_gate.py` — Validation Gate (Stage 6)

Runs deterministic checks before facts are promoted to `live_facts`:

| Check | What It Catches |
|-------|----------------|
| **Sum relations** | `revenue_gross - returns ≈ revenue_net` |
| **Margin consistency** | Derived margins match source fields |
| **Unit magnitude** | Values in sensible ranges (catches absolute vs lakhs confusion) |
| **Period swing** | Revenue doesn't jump >10× between adjacent periods |
| **Sign consistency** | Revenue should be positive, costs consistent |

#### `generate.py` — Mock Data Generator

Generates test data for `--mock` pipeline runs:
- **Glow Naturals** — D2C, wide format, INR Lakhs, 12 months FY24 with seasonality
- **Krishnan Engineering** — 2 files with an intentional FY24 revenue conflict (₹27.3Cr vs ₹28.1Cr)

---

## 🗄 Database Schema

**Active database:** `project/Data_Ingestion/financial_agent.duckdb`

> The root-level `financial_agent.duckdb` is a stale copy — ignore it.

### Entity-Relationship Diagram

```
entities ──┬── source_files (+ sheet_name) ──┬── schema_mappings
           │                                 ├── staging_facts ──► live_facts
           │                                 ├── conflicts
           │                                 └── ingestion_log
           └─────────────────────────────────────────────────────
```

### Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `entities` | One row per company | `entity_id`, `entity_name`, `entity_slug` |
| `source_files` | One row per file/sheet | `file_id`, `entity_id`, `filename`, `checksum`, `sheet_name`, `state` |
| `schema_mappings` | Raw header → canonical field | `file_id`, `raw_header`, `canonical_field`, `confidence`, `mapped_by` |
| `staging_facts` | Raw facts before validation | `staging_id`, `canonical_field`, `period`, `value_normalised`, `currency`, `original_unit` |
| `live_facts` | Query-ready validated facts | `fact_id`, `canonical_field`, `period`, `value_normalised`, `is_derived` |
| `conflicts` | Cross-source disagreements | `conflict_id`, `canonical_field`, `period`, `staging_ids`, `state` |
| `ingestion_log` | Full audit trail | `file_id`, `event`, `detail` |

### Fact ID Format

```
f_{entity_slug}_{period}_{canonical_field}

Example: f_glow_naturals_FY24-M06_revenue_net
```

### File State Machine

```
UPLOADED → SCHEMA_MAPPED → AWAITING_CONFLICT_RESOLUTION → AWAITING_ACKNOWLEDGMENT → LIVE
```

> **Note:** Full enforcement is not yet implemented — files currently go straight to LIVE after pipeline run.

### Reset Database

```bash
# Delete and let pipeline recreate on next run
del project\Data_Ingestion\financial_agent.duckdb    # Windows
# rm project/Data_Ingestion/financial_agent.duckdb   # macOS/Linux
```

---

## 📋 Example Input Files

Located in `project/example_input_files/` — reference files used to validate the ingestion engine:

| Entity | File | Live Facts | Key Challenges |
|--------|------|:----------:|----------------|
| Glow Naturals | `Glow_Naturals_monthly_pnl_2023_24.csv` | 180 | 3-row preamble, INR absolute, notes column |
| M/s Sharma Textiles | `Sharma_Textiles_Financials_FY24_v3_FINAL_revised.xlsx` | 33 | 3 sheets, different units per sheet (Lakhs vs Crore), mixed units within a sheet |
| M/s Krishnan Engineering | `Engineering_company_3yr_pnl_AS_PROVIDED_by_owner.xlsx` | 3 | Tally export, ledger format — needs LLM mapper |
| ZenithOps Inc. | `ZenithOps_Financials_DataRoom.xlsx` | 168 | 5 sheets, USD thousands, FY2020A/E/P periods, SaaS metrics |
| ZenithOps Inc. | `ZenithOps_CIM_Project_Atlas.docx` | — | Phase 2 (qualitative) — not yet handled |

---

## 🔐 Design Principles

These are **non-negotiable** rules enforced across the entire system:

| # | Principle | Rationale |
|---|-----------|-----------|
| 1 | **No LLM math** — all arithmetic is Python (`Decimal` for Phase 3+) | Eliminates hallucinated numbers |
| 2 | **No raw SQL from the LLM** — Phase 3 tools expose parameterised functions only | Prevents injection and ensures auditability |
| 3 | **Unit-explicit storage** — every fact row carries full conversion metadata | No ambiguity about whether `150` means ₹150 or ₹150 Lakhs |
| 4 | **Reconcile-then-answer** — cross-source conflicts surfaced to user | Never silently pick one source over another |
| 5 | **No data leaves the machine** — raw financial data never sent to cloud | Privacy and compliance for PE deal rooms |
| 6 | **State machine enforcement** — facts not queryable until file reaches `LIVE` | Prevents querying unvalidated data |

---

## 🛠 Tech Stack

| Component | Technology | Status |
|-----------|-----------|--------|
| Data processing | Python, [Polars](https://pola.rs/) | ✅ Active |
| Structured storage | [DuckDB](https://duckdb.org/) (local OLAP) | ✅ Active |
| Excel reading | [openpyxl](https://openpyxl.readthedocs.io/) | ✅ Active |
| Data validation | [Pydantic](https://docs.pydantic.dev/) | ✅ Active |
| LLM (schema mapping) | Claude via Anthropic API | 🔄 Partial |
| Vector storage | ChromaDB | ❌ Phase 2 |
| PDF parsing | PyMuPDF | ❌ Phase 2 |
| LLM orchestration | LangChain / AutoGen | ❌ Phase 4 |
| UI | Streamlit | ❌ Phase 5 |

### Dependencies

| Package | Version | Use |
|---------|---------|-----|
| `polars` | 1.40.1 | DataFrame engine for file reading and normalisation |
| `duckdb` | 1.5.2 | Local OLAP database for staging + live facts |
| `openpyxl` | 3.1.5 | Excel (.xlsx) reading backend |
| `pydantic` | 2.13.3 | Data validation and models |
| `pandas` | 2.2.0 | DuckDB result handling via `.df()` |
| `numpy` | 1.26.4 | Numerical operations |

---

## 🗺 Roadmap

### Recommended Build Order

```
 1. Phase 1 Stage 5 — LLM Schema Mapper       ← biggest coverage ceiling
 2. Phase 1 Stage 6 — Validation Gate          ← prevents silently wrong numbers
 3. Phase 3 — Deterministic Tool Surface       ← can test against current live_facts
 4. Phase 1 Stage 7 — Onboarding Conversation  ← needed before Phase 5 end-to-end
 5. Phase 4 — LLM Orchestration                ← wires LLM to Phase 3 tools
 6. Phase 5 — Streamlit UI                     ← plug orchestrator into chat
 7. Phase 2 — Qualitative Pipeline             ← independent, can run in parallel
```

### Phase 3 — Tools to Build

| Tool | Purpose |
|------|---------|
| `fetch_metric(entity_id, metric, period, unit_out)` | Single fact lookup |
| `calculate_variance(entity_id, metric, p1, p2, unit_out)` | Period-over-period change |
| `calculate_ratio(entity_id, numerator, denominator, period, unit_out)` | Margins and ratios |
| `search_context(entity_id, query, period_filter, metric_filter)` | ChromaDB semantic search |
| `list_sources(entity_id, metric, period)` | All sources for a fact |
| `list_available_metrics(entity_id)` | What's queryable for this entity |

All tools will raise typed exceptions (`MetricNotFound`, `AmbiguousEntity`, etc.) on missing data. Every result carries a citation envelope.

### Known Gaps (Phase 1)

- **Stage 5 LLM mapper** — any header not in `ALIAS_MAP` produces 0 facts for that metric
- **Mixed units within a sheet** — e.g. rows in Lakhs mixed with rows in absolute Rs
- **Cell-level citations** — `live_facts` rows don't carry sheet + cell reference yet
- **File state machine** — exists in schema but not enforced; files go straight to LIVE
- **Accounting paren negatives** — `(124.50)` → `-124.50` not yet converted

---

## 🔧 Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | Optional | Enables LLM-based schema mapping for unmapped headers (Stage 5). Without it, only the static alias dictionary is used. |

```bash
# Set on Windows
set ANTHROPIC_API_KEY=sk-ant-...

# Set on macOS/Linux
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## 📄 Documentation

| File | Contents |
|------|----------|
| [`CLAUDE.md`](CLAUDE.md) | Full project context for AI coding sessions |
| [`PROGRESS.md`](PROGRESS.md) | Stage-by-stage completion tracker |
| [`docs/file_reference.md`](docs/file_reference.md) | Detailed file & function reference |
| [`docs/Financial_AI_Agent_PRD_v1.2.docx`](docs/Financial_AI_Agent_PRD_v1.2.docx) | Full product requirements (all 5 phases) |
| [`docs/Phase_Reference_Report.docx`](docs/Phase_Reference_Report.docx) | Phase-by-phase I/O reference |
| [`docs/financial_ai_architecture_overview.svg`](docs/financial_ai_architecture_overview.svg) | System architecture diagram |
| [`docs/phase1_detailed_flow.svg`](docs/phase1_detailed_flow.svg) | Phase 1 ingestion flow diagram |

---

## 📜 License

This project is private and proprietary. All rights reserved.

---

<p align="center">
  <sub>Built for Private Equity analysis — deterministic by design, local by default.</sub>
</p>
