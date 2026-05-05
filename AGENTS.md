# AGENTS.md - Deterministic Financial AI Co-Pilot

> Always update this file, `README.md`, `docs/file_reference.md`, and `PROGRESS.md` at the end of every turn where code changes were made.

## What This Project Is

A local-first, deterministic financial intelligence system for Private Equity analysis.
Users upload heterogeneous financial files (CSV, Excel, PDF, DOCX) and query data through deterministic tools.

Core invariant:
- The LLM is a router and language formatter only.
- All math, unit conversion, SQL retrieval, and validation are deterministic Python.
- The LLM must never perform arithmetic over raw values.

## Project Status

| Phase | Role | Status |
|-------|------|--------|
| 1 | Raw file -> clean DuckDB facts | Complete (with known ingestion gaps) |
| 2 | PDF/DOCX -> ChromaDB qualitative chunks | Complete |
| 3 | 6 deterministic Python tools (only path to data) | Complete |
| 4 | LLM orchestration (routes questions, formats answers) | In progress |
| 5 | Streamlit chat UI | In progress |

## Architecture - 5 Phases

1. Quantitative ingestion (CSV/XLSX) to DuckDB staging/live facts.
2. Qualitative ingestion (PDF/DOCX) to chunk metadata + ChromaDB.
3. Deterministic tool surface for all retrieval and computation.
4. Orchestration layer that routes user intent to Phase 3 tools.
5. Streamlit UI for upload, state tracking, and question workflows.

## Phase 1 - Quantitative Pipeline (Implemented)

Entry point:
- `python project/Data_Ingestion/pipeline.py`

Key modules:
- `pipeline.py` - orchestrator, entity grouping, conflict flow, validation and onboarding gates.
- `schema.py` - DuckDB schema and DB helpers.
- `canonical_fields.py` - canonical metric registry + aliases + session LLM alias cache.
- `file_reader.py` - preamble detection, layout detection (wide/tall/ledger), extraction to staging facts.
- `units.py` - currency and scale normalization.
- `periods.py` - canonical period parser.
- `conflict_resolver.py` - conflict detection/resolution, live promotion, derived KPI compute.
- `llm_mapper.py` - optional schema mapping via Anthropic for unmapped headers.
- `validation_gate.py` - deterministic validation checks before promotion.
- `onboarding_gate.py` - acknowledgement step before LIVE promotion.

Recent hardening in `file_reader.py`:
- ledger/tally detection is now activated in `ingest_file` routing.
- stronger row-level mixed-unit inference (including parenthetical/absolute hints).
- inline annotation claim detection bug path fixed.

## Phase 2 - Qualitative Pipeline (Implemented)

Key modules:
- `qualitative.py` - PDF/DOCX extraction, chunking, claim detection, linking, ChromaDB persistence.
- `embeddings.py` - local sentence-transformer embedding model wrapper.
- `test_qualitative.py` - unit tests (22 passing in current baseline).

## Phase 3 - Deterministic Tool Surface (Implemented)

In `phase3_tools.py`:
- `fetch_metric(entity_id, metric, period, unit_out)`
- `calculate_variance(entity_id, metric, period_1, period_2, unit_out)`
- `calculate_ratio(entity_id, numerator, denominator, period, unit_out)`
- `search_context(entity_id, query, period_filter, metric_filter)`
- `list_sources(entity_id, metric, period)`
- `list_available_metrics(entity_id)`

Behavior:
- Typed exceptions for missing/ambiguous states.
- Deterministic `Decimal` arithmetic.
- Citation payloads for tool outputs.

## Phase 4 - Orchestration (Partially Implemented)

In `phase4_orchestrator.py`:
- deterministic routing heuristics for fetch/variance/ratio/source/context/list-metrics.
- LIVE state gate before query execution.
- basic period and metric extraction from free text.
- tool audit log (`tool_audit_log.jsonl`).
- qualitative numerical-claim cross-check flags (`needs_verification`) when chunk claims are not linked to facts.

Partially complete:\n- Optional cloud planner mode is implemented behind env flag (`ORCH_CLOUD_ROUTER_ENABLED`) with strict JSON tool plan validation, whitelist enforcement, and deterministic tool-only execution.\n\nNot yet complete:\n- Production provider adapters and richer response normalization across providers.\n- Additional hardening tests for malformed planner responses.

## Phase 5 - Streamlit UI (Partially Implemented)

In `project/app.py`:
- background job queue for structured ingestion (`ThreadPoolExecutor`).
- upload/process flow for structured files (queued worker execution).
- job status panel with refresh and success/failure visibility.
- entity LIVE status display.
- guided deterministic tool tabs (fetch, variance, ratio, sources, context).
- free-text deterministic router tab.
- citation display and show-your-work expanders.

Not yet complete:
- full onboarding conversation UX and state timelines in-app.
- full qualitative upload path in-app (currently suggested through pipeline flow).

## Database

Active DB:
- `project/Data_Ingestion/financial_agent.duckdb`

Core schema tables:
- `entities`
- `source_files`
- `schema_mappings`
- `staging_facts`
- `live_facts`
- `conflicts`
- `ingestion_log`
- `qualitative_chunks`

## Non-Negotiable Design Rules

1. No LLM math.
2. No raw SQL from LLM.
3. Unit-explicit storage (`value`, `currency`, `original_unit`, `conversion_factor`, `conversion_applied`).
4. Reconcile then answer (surface conflicts, never silent override).
5. Local-first data handling (no raw financial data exfiltration).
6. Query only LIVE-state data.

## Known Gaps

1. Mixed units within a single sheet are improved but still have edge cases.
2. Region enumeration/bifurcation for mixed-structure documents can be expanded.
3. Ledger/tally coverage is improved but can be further hardened.
4. Phase 4 cloud LLM orchestration is not wired yet.
5. Phase 5 still needs full onboarding UX and richer state messaging.

## Running

```bash
cd project
pip install -r requirements.txt

# Full ingestion flow
python Data_Ingestion/pipeline.py

# Mock flow
python Data_Ingestion/pipeline.py --mock

# Live facts summary only
python Data_Ingestion/pipeline.py --report-only

# Streamlit UI
streamlit run app.py
```



## Test Coverage Additions

- project/Data_Ingestion/test_phase4_cloud_plan_validation.py validates:
  - unknown tool rejection
  - missing required arg rejection
  - unknown arg rejection
  - fenced JSON plan parsing
  - cloud planner failure fallback to local deterministic routing
  - deterministic execution of validated plans


- project/Data_Ingestion/test_file_reader_hardening.py validates mixed-unit overrides, annotation claim flags, and ledger-route activation in ingestion.

Verification addendum (2026-05-06):
- test_phase4_cloud_plan_validation.py: 11 passed
- test_file_reader_hardening.py: 4 passed


README.md now contains a detailed step-by-step run guide for setup, ingestion, Streamlit usage, optional cloud planner configuration, test execution, and troubleshooting.

