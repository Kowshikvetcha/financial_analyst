# Project Progress - Deterministic Financial AI Co-Pilot

Last updated: 2026-05-06

## Legend

- Complete
- In progress
- Not started

## Phase 1 - Quantitative Pipeline

Overall: Complete (with known ingestion gaps)

### Completed

- DuckDB schema with source lineage and file states.
- CSV and Excel ingestion (including multi-sheet support).
- Preamble detection and entity auto-detection.
- Wide/tall layout extraction.
- Ledger/tally route activated in ingestion path.
- Unit normalization and canonical period normalization.
- Row-level mixed-unit heuristics strengthened (parenthetical + absolute hints).
- Canonical fields + alias mapping.
- Optional LLM mapping cache flow for unmapped headers.
- Conflict detection/resolution and live promotion.
- Derived KPI generation.
- Validation and onboarding gates in pipeline CLI flow.

### Pending / gaps

- Mixed-unit rows inside single sheets still have edge cases.
- Ledger/tally formats need broader deterministic coverage.
- Region enumeration for mixed-format sources can be expanded.

## Phase 2 - Qualitative Pipeline

Overall: Complete

### Completed

- PDF extraction (PyMuPDF) and DOCX extraction (`python-docx`).
- Chunking and numerical-claim detection.
- Chunk metadata persistence to DuckDB.
- Embedding + storage in local ChromaDB.
- Claim/link metadata fields (`linked_fact_ids`, `linked_periods`, `linked_metrics`).
- Unit tests passing (`test_qualitative.py`: 22 passed).

### Pending

- Broader mixed-content table-vs-narrative bifurcation refinement.

## Phase 3 - Deterministic Tool Surface

Overall: Complete

### Completed

- `fetch_metric`
- `calculate_variance`
- `calculate_ratio`
- `search_context`
- `list_sources`
- `list_available_metrics`

Behavior completed:
- Typed exceptions.
- `Decimal` deterministic arithmetic.
- Citation payloads.

Tests:
- `test_phase3_phase4.py`: passing in current venv.

## Phase 4 - Orchestration

Overall: In progress

### Completed\n\n- Deterministic intent routing for common query classes.\n- LIVE gate enforcement before query.\n- Free-text period and metric heuristic extraction.\n- Tool audit logging (`tool_audit_log.jsonl`).\n- Qualitative numerical-claim caution flag for unlinked claims.\n- Optional cloud planner mode with strict whitelist + argument validation and deterministic execution fallback.\n- Dedicated cloud-plan validation tests added (`test_phase4_cloud_plan_validation.py`).

### Pending\n\n- Production-grade provider adapters and broader cloud response normalization.\n- Expanded deterministic parser coverage for more natural language patterns.\n- Additional tests for malformed/hostile planner output handling.

## Phase 5 - Streamlit UI

Overall: In progress

### Completed

- Structured upload queue.
- Background ingestion worker execution (`ThreadPoolExecutor`).
- Job status panel with manual refresh and error visibility.
- Entity state visibility.
- Guided tool tabs for deterministic operations.
- Free-text deterministic router tab.
- Citation display and show-your-work expanders.

### Pending

- Full onboarding conversation UX in-app.
- Full qualitative ingestion in-app without CLI dependency.

## Verification Run (2026-05-06)

Using `project/venv/Scripts/python.exe`:

- `py_compile project/app.py project/Data_Ingestion/file_reader.py` -> passed
- `pytest project/Data_Ingestion/test_phase3_phase4.py -q` -> 2 passed
- `pytest project/Data_Ingestion/test_qualitative.py -q` -> 22 passed



## Verification Run Addendum (2026-05-06)

- pytest project/Data_Ingestion/test_phase4_cloud_plan_validation.py -q -> 6 passed


## Verification Run Addendum (2026-05-06 - Hardening)

Using project/venv/Scripts/python.exe:
- pytest project/Data_Ingestion/test_phase4_cloud_plan_validation.py -q -> 11 passed
- pytest project/Data_Ingestion/test_file_reader_hardening.py -q -> 4 passed
- pytest project/Data_Ingestion/test_phase3_phase4.py -q -> 2 passed
- pytest project/Data_Ingestion/test_qualitative.py -q -> 22 passed



## Documentation Update (2026-05-06)

- Detailed README run guide added for setup, pipeline execution, Streamlit usage, cloud planner env configuration, testing, and troubleshooting.

