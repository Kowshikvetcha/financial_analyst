# Project Progress — Deterministic Financial AI Co-Pilot

> Last updated: 2026-05-03
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

**Overall status: 🔄 In progress**

### Infrastructure

| Item | Status | Notes |
|------|--------|-------|
| DuckDB schema (7 tables) | ✅ | `schema.py` — entities, source_files, schema_mappings, staging_facts, live_facts, conflicts, ingestion_log |
| `sheet_name` on source_files | ✅ | Added for per-sheet citation lineage |
| Canonical field registry | ✅ | `canonical_fields.py` — 58 fields across 6 categories |
| Alias map | ✅ | ~230 raw header → canonical field mappings; covers Indian P&L, SaaS, manufacturing, D2C |
| Unit normalisation | ✅ | `units.py` — INR (absolute/Lakh/Crore/Million) + USD (absolute/Thousand/Million/Billion) |
| Period normalisation | ✅ | `periods.py` — FY annual/monthly/quarterly, calendar year, date formats, range notation |
| Conflict detection | ✅ | `conflict_resolver.py` — detects (entity, field, period) disagreements across sources |
| Conflict resolution (interactive) | ✅ | Pipeline prompts user to pick authoritative value |
| Staging → live promotion | ✅ | `conflict_resolver.py` — `promote_to_live()` |
| Derived KPI computation | ✅ | gross_profit, gross_margin_pct, ebitda_margin_pct, pat_margin_pct |

### Stage-by-Stage (per PRD §5.1)

| Stage | Name | Status | Notes |
|-------|------|--------|-------|
| 1 | File registration + checksum | ✅ | SHA-256 checksum now computed on registration via `compute_sha256()` |
| 2 | Region enumeration | ❌ | No multi-region detection; no routing of qualitative regions to Phase 2 |
| 3 | Pre-normalisation | 🔄 | Preamble skip ✅, TOTAL row skip ✅, section header skip ✅, ragged lines ✅; paren-negative conversion ✅, mixed-unit row splitting ❌ |
| 4 | Unit & period detection | ✅ | Per-sheet unit detection ✅, all major period formats ✅ |
| 5 | Schema mapping (LLM) | 🔄 | `llm_mapper.py` built — calls Anthropic API for unmapped headers; persistent JSON cache; requires ANTHROPIC_API_KEY env var |
| 6 | Validation gate | 🔄 | `validation_gate.py` built — sum checks, unit magnitude, period swings, sign consistency; runs after staging before promotion |
| 7 | Onboarding conversation (v1.2) | ❌ | No chat gating. Facts promote to live without user acknowledgment. File state machine not enforced. |
| 8 | Promote to live + citation envelopes | 🔄 | Promotion ✅; citation envelopes (cell-level source reference) ❌ not yet populated |

### File format support

| Format | Status | Notes |
|--------|--------|-------|
| CSV | ✅ | Preamble detection, tall/wide layout, unit sniff |
| Excel (.xlsx/.xls) | ✅ | Multi-sheet, per-sheet unit/preamble detection |
| DOCX | ❌ | Phase 2 (qualitative pipeline) |
| PDF | ❌ | Phase 2 (qualitative pipeline) |
| Ledger / vertical-block layout | ❌ | Engineering "3 yr PnL" style — needs LLM schema mapper (Stage 5) |
| Tally double-column layout | 🔄 | Partially works; left-right column pairing not handled |

### Known gaps

- **Stage 5 LLM mapper** is the biggest missing piece — any header not in `ALIAS_MAP` produces 0 facts for that metric. This is a hard ceiling on coverage until Stage 5 is built.
- **Accounting paren negatives** `(124.50)` → `-124.50` not converted.
- **Mixed units within a sheet** (e.g. Sharma Textiles "Other Exp Detail" — most rows in Lakhs, two rows in absolute Rs.) not detected.
- **Cell-level citations** missing — `live_facts` rows don't yet carry sheet + cell reference.
- **File state machine** (`UPLOADED → AWAITING_CONFLICT_RESOLUTION → AWAITING_ACKNOWLEDGMENT → LIVE`) exists in schema but is not enforced — files go straight to LIVE.

---

## Phase 2 — Qualitative Context Pipeline (Vector DB)

**Goal:** Narrative text from PDFs/DOCXs → ChromaDB chunks linked back to DuckDB fact_ids.

**Overall status: ❌ Not started**

| Item | Status | Notes |
|------|--------|-------|
| PyMuPDF PDF parser | ❌ | |
| python-docx DOCX parser | ❌ | |
| Document bifurcation (tables → Phase 1, text → Phase 2) | ❌ | |
| Chunking (150–300 words, no split paragraphs) | ❌ | |
| Annotation linking (chunk → fact_id) | ❌ | |
| ChromaDB embedding + storage | ❌ | |
| `contains_numerical_claim` flag on chunks | ❌ | |
| Notes column capture from tabular files | ❌ | e.g. Glow Naturals "notes" column with "monsoon dip" etc. |

---

## Phase 3 — Deterministic Tool Surface

**Goal:** 6 Python tools are the only authorised path from a question to a fact. No raw SQL from LLM.

**Overall status: ❌ Not started**

| Tool | Status | Notes |
|------|--------|-------|
| `fetch_metric(entity_id, metric, period, unit_out)` | ❌ | |
| `calculate_variance(entity_id, metric, period_1, period_2, unit_out)` | ❌ | |
| `calculate_ratio(entity_id, numerator, denominator, period, unit_out)` | ❌ | |
| `search_context(entity_id, query, period_filter, metric_filter)` | ❌ | Depends on Phase 2 |
| `list_sources(entity_id, metric, period)` | ❌ | |
| `list_available_metrics(entity_id)` | ❌ | |
| Citation envelope on all tool results | ❌ | Defined in PRD §6 |
| Typed exceptions (MetricNotFound, AmbiguousEntity, etc.) | ❌ | |
| Decimal precision (not float) | ❌ | |

---

## Phase 4 — Agentic Orchestration

**Goal:** LLM routes questions to tools and formats answers. Never performs arithmetic or writes SQL.

**Overall status: ❌ Not started**

| Item | Status | Notes |
|------|--------|-------|
| LLM API integration (Claude / GPT-4o) | ❌ | |
| System prompt (router-only persona, no math, no guessing) | ❌ | |
| Entity resolution pre-flight (name → entity_id before any tool call) | ❌ | |
| File state gate (refuse queries on non-LIVE entities) | ❌ | |
| `list_available_metrics` called every turn | ❌ | |
| Conflict-aware response template | ❌ | |
| `contains_numerical_claim` cross-check (DuckDB wins over narrative) | ❌ | Depends on Phase 2 + 3 |
| Tool-call audit log | ❌ | |

---

## Phase 5 — User Interface

**Goal:** Streamlit chat app. File upload triggers Phase 1+2. Onboarding conversation. Show-your-work expanders.

**Overall status: ❌ Not started**

| Item | Status | Notes |
|------|--------|-------|
| Streamlit app scaffold | ❌ | |
| File drag-and-drop upload | ❌ | |
| Background Phase 1+2 processing on upload | ❌ | |
| Onboarding conversation message render | ❌ | Depends on Phase 1 Stage 7 |
| File state indicators (LIVE / AWAITING) | ❌ | |
| Chat input gated by file state | ❌ | |
| Inline citations on every numerical answer | ❌ | Depends on Phase 3 |
| "Show your work" expander per answer | ❌ | |
| Reset source choices command | ❌ | |
| Multi-file session support | ❌ | |

---

## What to build next (recommended order)

1. **Phase 1 Stage 5 — LLM schema mapper**
   The alias dict is the hard ceiling. Any file with novel headers (new industry, new accounting style) extracts 0 facts for those rows. Building the LLM mapper with deterministic validation will make ingestion truly general-purpose.

2. **Phase 1 Stage 6 — Validation gate**
   Sum checks, unit magnitude checks, cross-period sanity checks. Prevents silently wrong numbers.

3. **Phase 3 — Tool surface**
   Can be built and tested against the current live_facts without Phase 2 or 5. These tools are what the LLM orchestrator calls — building them first means Phases 4 and 5 have something real to plug into.

4. **Phase 1 Stage 7 — Onboarding conversation**
   Generates the bot's first chat message. Needed before Phase 5 can be tested end-to-end.

5. **Phase 4 — LLM orchestration**
   Wire the LLM to Phase 3 tools with the system prompt constraints.

6. **Phase 5 — Streamlit UI**
   Plug orchestrator into a chat surface.

7. **Phase 2 — Qualitative pipeline**
   Can run in parallel with Phase 3/4/5 since it's independent.
