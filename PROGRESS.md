# Project Progress — Deterministic Financial AI Co-Pilot

> Last updated: 2026-05-04
> Reference: `docs/Financial_AI_Agent_PRD_v1.2.docx`, `docs/Phase_Reference_Report.docx`

---

## Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Complete |
| 🔄 | In progress / partial |
| ❌ | Not started |

---

## Phase 1 — Quantitative Pipeline (Structured DB)

**Goal:** Raw uploaded file → clean, unit-explicit, queryable facts in DuckDB.

**Overall status: ✅ Phase 1 complete**

### Infrastructure

| Item | Status | Notes |
|------|--------|-------|
| DuckDB schema (7 tables) | ✅ | `schema.py` — entities, source_files, schema_mappings, staging_facts, live_facts, conflicts, ingestion_log |
| `sheet_name` on source_files | ✅ | Added for per-sheet citation lineage |
| Canonical field registry | ✅ | `canonical_fields.py` — 58 fields across 6 categories |
| Alias map | ✅ | ~330 raw header → canonical field mappings; covers Indian P&L, SaaS, manufacturing, D2C |
| Unit normalisation | ✅ | `units.py` — INR (absolute/Lakh/Crore/Million) + USD (absolute/Thousand/Million/Billion) |
| Period normalisation | ✅ | `periods.py` — FY annual/monthly/quarterly, calendar year, date formats, range notation |
| Conflict detection | ✅ | `conflict_resolver.py` — detects (entity, field, period) disagreements across sources |
| Conflict resolution (interactive) | ✅ | Pipeline prompts user to pick authoritative value |
| Staging → live promotion | ✅ | `conflict_resolver.py` — `promote_to_live()` |
| Derived KPI computation | ✅ | gross_profit, gross_margin_pct, ebitda_margin_pct, pat_margin_pct |
| Cell-level citations | ✅ | `live_facts` rows carry `cell_reference` (e.g., "B5") and `source_sheet` |
| SHA-256 file checksum | ✅ | Computed on registration via `compute_sha256()` |
| LLM schema mapper | ✅ | `llm_mapper.py` — Anthropic API calls for unmapped headers, persistent JSON cache, re-ingest with LLM-mapped headers |
| Validation gate | ✅ | `validation_gate.py` — sum checks, unit magnitude, period swings, sign consistency; soft block (prompts user) |
| Onboarding conversation gate | ✅ | `onboarding_gate.py` — Stage 7; prints summary, awaits user acknowledgment before LIVE promotion |
| State machine enforcement | ✅ | `UPLOADED → SCHEMA_MAPPED → AWAITING_CONFLICT_RESOLUTION → AWAITING_ACKNOWLEDGMENT → LIVE` |
| Session-level LLM alias cache | ✅ | `canonical_fields.py` — `register_llm_mapping()` + `_llm_cache` on `resolve_alias()` |

### Stage-by-Stage (per PRD §5.1)

| Stage | Name | Status | Notes |
|-------|------|--------|-------|
| 1 | File registration + checksum | ✅ | SHA-256 checksum computed on registration |
| 2 | Region enumeration | ❌ | No multi-region detection; no routing of qualitative regions to Phase 2 |
| 3 | Pre-normalisation | ✅ | Preamble skip, TOTAL row skip, section header skip, ragged lines, paren-negative conversion |
| 4 | Unit & period detection | ✅ | Per-sheet unit detection, all major period formats |
| 5 | Schema mapping (LLM) | ✅ | `llm_mapper.py` — calls Anthropic API for unmapped headers; persistent cache; re-ingests after mapping |
| 6 | Validation gate | ✅ | `validation_gate.py` — soft block with user prompt; non-interactive defaults to proceed |
| 7 | Onboarding conversation (v1.2) | ✅ | `onboarding_gate.py` — summary per entity, user acknowledgment, AWAITING_ACKNOWLEDGMENT state |
| 8 | Promote to live + citation envelopes | ✅ | Promotion with cell_reference and source_sheet populated |

### File format support

| Format | Status | Notes |
|--------|--------|-------|
| CSV | ✅ | Preamble detection, tall/wide layout, unit sniff |
| Excel (.xlsx/.xls) | ✅ | Multi-sheet, per-sheet unit/preamble detection |
| DOCX | ❌ | Phase 2 (qualitative pipeline) |
| PDF | ❌ | Phase 2 (qualitative pipeline) |
| Ledger / vertical-block layout | 🔄 | Engineering "3 yr PnL" style — needs LLM schema mapper |
| Tally double-column layout | 🔄 | Partially works; left-right column pairing not handled |

### Known gaps

- **Mixed units within a sheet** (e.g. Sharma Textiles "Other Exp Detail" — most rows in Lakhs, two rows in absolute Rs.) not detected.

---

## Phase 2 — Qualitative Context Pipeline (Vector DB)

**Goal:** Narrative text from PDFs/DOCXs → ChromaDB chunks linked back to DuckDB fact_ids.

**Overall status: ✅ Complete**

Implemented in this update:
- `schema.py`: `qualitative_chunks` table + `register_qualitative_file()` + `get_qualitative_chunks()`
- `embeddings.py`: lazy-loaded singleton `all-MiniLM-L6-v2` sentence-transformer wrapper
- `qualitative.py`: extraction/chunking/claim detection/linking/ChromaDB persistence pipeline
- `pipeline.py`: Phase 2 invocation + `--skip-qualitative` flag
- `file_reader.py`: inline annotation extraction from notes/comment columns
- `test_qualitative.py`: comprehensive unit test suite (22 tests)
- ChromaDB storage path: `project/Data_Ingestion/chromadb_data/`
- Verified end-to-end run on `ZenithOps_CIM_Project_Atlas.docx` (132 chunks stored) after Phase 1

Verification status:
- `pytest Data_Ingestion/test_qualitative.py -v`: **22 passed**
- `python Data_Ingestion/pipeline.py --mock`: Phase 1 + Phase 2 completed successfully
- ChromaDB duplicate-ID warning fixed by switching `collection.add(...)` to `collection.upsert(...)` in `store_in_chromadb()`.

| Item | Status | Notes |
|------|--------|-------|
| PyMuPDF PDF parser | ✅ | Implemented in `qualitative.py` |
| python-docx DOCX parser | ✅ | Implemented in `qualitative.py` |
| Document bifurcation (tables → Phase 1, text → Phase 2) | 🔄 | Partial: qualitative files are routed to Phase 2; unified mixed-content bifurcation logic can be expanded |
| Chunking (150–300 words, no split paragraphs) | ✅ | Implemented chunking pipeline with paragraph-aware processing |
| Annotation linking (chunk → fact_id) | ✅ | Claim/linking pipeline implemented in `qualitative.py` |
| ChromaDB embedding + storage | ✅ | Implemented with local ChromaDB persistence + upsert dedupe |
| `contains_numerical_claim` flag on chunks | ✅ | Implemented and persisted in chunk metadata |
| Notes column capture from tabular files | ✅ | `extract_inline_annotations()` in `file_reader.py` |

---

## Phase 3 — Deterministic Tool Surface

**Goal:** 6 Python tools are the only authorised path from a question to a fact. No raw SQL from LLM.

**Overall status: 🔄 In progress**

| Tool | Status | Notes |
|------|--------|-------|
| `fetch_metric(entity_id, metric, period, unit_out)` | ✅ | Implemented in `phase3_tools.py` |
| `calculate_variance(entity_id, metric, period_1, period_2, unit_out)` | ✅ | Implemented in `phase3_tools.py` |
| `calculate_ratio(entity_id, numerator, denominator, period, unit_out)` | ✅ | Implemented in `phase3_tools.py` |
| `search_context(entity_id, query, period_filter, metric_filter)` | ✅ | Implemented against `qualitative_chunks` metadata |
| `list_sources(entity_id, metric, period)` | ✅ | Implemented in `phase3_tools.py` |
| `list_available_metrics(entity_id)` | ✅ | Implemented in `phase3_tools.py` |
| Citation envelope on all tool results | ✅ | Included for metric/variance/ratio responses |
| Typed exceptions (MetricNotFound, AmbiguousEntity, etc.) | ✅ | Added in `phase3_tools.py` |
| Decimal precision (not float) | ✅ | `Decimal` used for deterministic calculations |
| Phase 3/4 test coverage | ✅ | `test_phase3_phase4.py` added; total suite now 24 passing tests |

---

## Phase 4 — Agentic Orchestration

**Goal:** LLM routes questions to tools and formats answers. Never performs arithmetic or writes SQL.

**Overall status: 🔄 In progress**

| Item | Status | Notes |
|------|--------|-------|
| LLM API integration (Claude / GPT-4o) | ❌ | Not wired yet |
| System prompt (router-only persona, no math, no guessing) | ❌ | Not wired yet |
| Entity resolution pre-flight (name → entity_id before any tool call) | ✅ | Implemented in orchestrator/tool layer |
| File state gate (refuse queries on non-LIVE entities) | ✅ | Implemented in `phase4_orchestrator.py` |
| `list_available_metrics` called every turn | ✅ | Implemented in `answer_question()` |
| Conflict-aware response template | 🔄 | Basic deterministic templates present |
| `contains_numerical_claim` cross-check (DuckDB wins over narrative) | ❌ | Next enhancement |
| Tool-call audit log | ✅ | JSONL audit log implemented |

---

## Phase 5 — User Interface

**Goal:** Streamlit chat app. File upload triggers Phase 1+2. Onboarding conversation. Show-your-work expanders.

**Overall status: 🔄 In progress**

| Item | Status | Notes |
|------|--------|-------|
| Streamlit app scaffold | ✅ | Added `project/app.py` |
| File drag-and-drop upload | ✅ | Implemented basic uploader in `project/app.py` |
| Background Phase 1+2 processing on upload | ❌ | Not yet async/backgrounded |
| Onboarding conversation message render | ❌ | Pending UX work |
| File state indicators (LIVE / AWAITING) | ❌ | Pending UI enhancement |
| Chat input gated by file state | ✅ | Enforced via orchestrator LIVE gate |
| Inline citations on every numerical answer | 🔄 | Tool payload includes citations; UI rendering pending |
| "Show your work" expander per answer | ❌ | Pending UI enhancement |
| Reset source choices command | ❌ | Pending |
| Multi-file session support | 🔄 | Basic entity selection implemented |

---

## What to build next (recommended order)

1. **Phase 3 — Tool surface**
   Can be built and tested against the current live_facts without Phase 2 or 5. These tools are what the LLM orchestrator calls — building them first means Phases 4 and 5 have something real to plug into.

2. **Phase 4 — LLM orchestration**
   Wire the LLM to Phase 3 tools with the system prompt constraints.

3. **Phase 5 — Streamlit UI**
   Plug orchestrator into a chat surface.

4. **Phase 4 completion — LLM routing layer**
   Wire real LLM prompt + strict tool schema and claim cross-check policy.

5. **Phase 5 completion — Streamlit UX**
   Add onboarding cards, state badges, citation expanders, and background processing.
