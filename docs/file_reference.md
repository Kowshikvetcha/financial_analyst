# File Reference - Deterministic Financial AI Co-Pilot

Keep this document updated after code changes.

## Top Level

- `AGENTS.md` - operating constraints and high-level phase status.
- `README.md` - quick start, architecture summary, and known gaps.
- `PROGRESS.md` - detailed completion and pending work list.
- `docs/file_reference.md` - this file.

## `project/`

- `requirements.txt` - runtime dependencies.
- `app.py` - Streamlit UI surface for deterministic workflows.
- `input_files/` - user upload folder for ingestion pipeline.
- `example_input_files/` - seeded test/reference input files.
- `Data_Ingestion/` - quantitative + qualitative + tool/orchestrator modules.

## `project/Data_Ingestion/`

### Core pipeline modules

- `pipeline.py`
  - Quantitative pipeline orchestrator.
  - Groups files by detected entity.
  - Handles sheet-wise ingestion.
  - Runs conflict resolution, validation gate, onboarding gate, and live promotion.

- `schema.py`
  - DuckDB schema initialization.
  - Entity and source file registration.
  - File state updates.
  - Qualitative chunk retrieval helpers.

- `file_reader.py`
  - Reads CSV/XLSX files with preamble detection.
  - Detects wide/tall layout plus ledger/tally style routing.
  - Extracts facts to staging with unit and period normalization.
  - Applies row-level mixed-unit override heuristics.
  - Captures cell/sheet lineage.
  - Bridges inline annotations to qualitative chunks.

- `canonical_fields.py`
  - Canonical metric registry and alias mapping.
  - Session-level LLM mapping registration support.
  - Ledger detection and extraction helpers used by ingestion.

- `units.py`
  - Unit detection and normalization to base values.

- `periods.py`
  - Canonical period parsing and labeling.

- `conflict_resolver.py`
  - Detects cross-source conflicts.
  - Stores conflict records.
  - Resolves conflicts and promotes staged facts to live.
  - Computes derived KPI fields.

- `validation_gate.py`
  - Deterministic pre-promotion checks.
  - Emits warnings/errors and report summaries.

- `onboarding_gate.py`
  - Builds human review summary before promotion.
  - Interactive acknowledgement gate for CLI flow.

- `llm_mapper.py`
  - Optional Anthropic-backed schema mapping for unmapped headers.
  - Persistent JSON cache for previously mapped headers.

### Qualitative modules (Phase 2)

- `embeddings.py`
  - Local embedding model wrapper (sentence-transformers).

- `qualitative.py`
  - PDF/DOCX extraction and chunking.
  - Numerical claim detection.
  - Chunk-to-fact linking metadata.
  - ChromaDB storage and qualitative pipeline execution.

### Tool/orchestration modules

- `phase3_tools.py`
  - Deterministic query/computation tool surface.
  - Typed exceptions.
  - Citation payloads.

- `phase4_orchestrator.py`\n  - Deterministic free-text routing to Phase 3 tools.\n  - Optional cloud planner mode (env-gated) with strict JSON plan contract.\n  - Tool whitelist + required argument validation before deterministic execution.\n  - LIVE gate and tool-call audit log.\n  - Cross-check policy flagging for unlinked narrative numeric claims.

### Data and tests

- `financial_agent.duckdb` - active local DuckDB database.
- `chromadb_data/` - local ChromaDB persistence files.
- `mock_data/` - generated mock ingestion CSVs.
- `test_phase3_phase4.py` - tests for deterministic tools/orchestrator gate.\n- `test_phase4_cloud_plan_validation.py` - tests cloud-plan whitelist/args validation, type/size checks, provider response extraction, and fallback behavior.\n- `test_file_reader_hardening.py` - tests mixed-unit override rules, annotation numerical-claim flags, and ledger-route activation.\n- `test_qualitative.py` - qualitative pipeline tests.

## `project/app.py` UI behavior

The app currently provides:

1. Upload queue for structured files.
2. Background ingestion workers (thread pool).
3. Job status panel (queued/completed/failed) with manual refresh.
4. Entity state summary (LIVE vs not LIVE).
5. Guided deterministic tool tabs:
   - `fetch_metric`
   - `calculate_variance`
   - `calculate_ratio`
   - `list_sources`
   - `search_context`
6. Free-text deterministic router tab (`answer_question`).
7. Citation display and show-your-work expanders.

## Known limitations (code-level)

- Mixed-unit detection is improved but still heuristic.
- Ledger/tally extraction coverage is improved but partial.
- Full cloud LLM orchestration contract for Phase 4 is pending.
- UI onboarding conversation workflow is still basic.





## Runbook Location

- README.md includes a full runbook: environment setup, ingestion CLI flow, Streamlit usage, optional cloud planner env config, test commands, and troubleshooting.

