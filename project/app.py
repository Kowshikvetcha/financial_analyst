from __future__ import annotations

import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "Data_Ingestion"))

from schema import (
    get_connection,
    initialise_schema,
    get_or_create_entity,
    register_file,
    update_file_state,
)
from file_reader import ingest_file
from conflict_resolver import promote_to_live, compute_derived_kpis
from phase3_tools import (
    fetch_metric,
    calculate_variance,
    calculate_ratio,
    list_sources,
    list_available_metrics,
    search_context,
    ToolError,
)
from phase4_orchestrator import answer_question

DB_PATH = Path(__file__).parent / "Data_Ingestion" / "financial_agent.duckdb"

# Background worker pool (module-level so it survives reruns)
_EXECUTOR = ThreadPoolExecutor(max_workers=2)
_JOBS: dict[str, dict] = {}

st.set_page_config(page_title="Financial AI Co-Pilot", layout="wide")
st.title("Financial AI Co-Pilot")


# UI connection (main thread)
conn = get_connection(DB_PATH)
initialise_schema(conn)


def _entity_rows():
    return conn.execute(
        """
        SELECT e.entity_id, e.entity_name,
               SUM(CASE WHEN sf.state='LIVE' THEN 1 ELSE 0 END) AS live_files,
               COUNT(sf.file_id) AS total_files
        FROM entities e
        LEFT JOIN source_files sf ON sf.entity_id = e.entity_id
        GROUP BY e.entity_id, e.entity_name
        ORDER BY e.entity_name
        """
    ).fetchall()


def _ingest_structured_file_job(path: str, uploaded_name: str, entity_name: str) -> dict:
    # Dedicated connection inside worker thread.
    job_conn = get_connection(DB_PATH)
    initialise_schema(job_conn)
    try:
        entity_id = get_or_create_entity(job_conn, entity_name)
        file_id = register_file(
            job_conn,
            entity_id,
            uploaded_name,
            path,
            Path(path).suffix.lstrip("."),
            detected_unit="",
        )
        update_file_state(job_conn, file_id, "SCHEMA_MAPPED")

        result = ingest_file(job_conn, Path(path), file_id, entity_id)

        # UI path keeps auto-promotion, CLI pipeline retains full gates.
        update_file_state(job_conn, file_id, "LIVE")
        slug = entity_name.strip().lower().replace(" ", "_")
        promote_to_live(job_conn, entity_id, slug)
        compute_derived_kpis(job_conn, entity_id, slug)

        return {"entity_id": entity_id, "file_id": file_id, **result}
    finally:
        job_conn.close()


def _render_citations(payload: dict) -> None:
    if "citation" in payload:
        c = payload["citation"]
        st.caption(
            f"Citation: file_id={c.get('source_file_id')}, sheet={c.get('source_sheet')}, cell={c.get('cell_reference')}"
        )

    if "citations" in payload and isinstance(payload["citations"], list):
        for i, c in enumerate(payload["citations"], start=1):
            st.caption(
                f"Citation {i}: file_id={c.get('source_file_id')}, sheet={c.get('source_sheet')}, cell={c.get('cell_reference')}"
            )


def _refresh_jobs() -> None:
    for job_id, meta in list(_JOBS.items()):
        fut = meta.get("future")
        if not fut:
            continue
        if fut.done() and meta.get("status") == "queued":
            try:
                result = fut.result()
                meta["status"] = "completed"
                meta["result"] = result
            except Exception as e:
                meta["status"] = "failed"
                meta["error"] = str(e)


st.subheader("1) Upload and Process")
uploaded = st.file_uploader(
    "Upload CSV/XLSX (structured) or PDF/DOCX (qualitative)",
    type=["csv", "xlsx", "xls", "pdf", "docx"],
)

col_u1, col_u2 = st.columns([2, 1])
with col_u1:
    entity_default = Path(uploaded.name).stem if uploaded else ""
    entity_name = st.text_input("Entity name", value=entity_default)
with col_u2:
    process_btn = st.button("Queue File Processing", use_container_width=True)

if uploaded is not None and process_btn:
    suffix = Path(uploaded.name).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded.getbuffer())
        tmp_path = Path(tmp.name)

    if not entity_name.strip():
        st.error("Entity name is required.")
    else:
        if suffix in (".csv", ".xlsx", ".xls"):
            job_id = str(uuid.uuid4())[:8]
            fut = _EXECUTOR.submit(_ingest_structured_file_job, str(tmp_path), uploaded.name, entity_name.strip())
            _JOBS[job_id] = {
                "status": "queued",
                "filename": uploaded.name,
                "entity_name": entity_name.strip(),
                "future": fut,
                "result": None,
                "error": None,
            }
            st.success(f"Queued job {job_id} for {uploaded.name}")
        else:
            st.info(
                "Qualitative files are processed through the pipeline command flow. "
                "Run: python Data_Ingestion/pipeline.py"
            )

_refresh_jobs()

st.subheader("2) Background Jobs")
if st.button("Refresh Job Status"):
    _refresh_jobs()

if _JOBS:
    for job_id, meta in sorted(_JOBS.items()):
        st.write(f"- Job {job_id} | {meta['filename']} | entity={meta['entity_name']} | status={meta['status']}")
        if meta["status"] == "completed" and meta.get("result"):
            r = meta["result"]
            st.caption(
                f"facts_staged={r.get('facts_staged')} layout={r.get('layout')} unit={r.get('unit_used') or 'absolute'}"
            )
        elif meta["status"] == "failed":
            st.error(f"Job {job_id} failed: {meta.get('error')}")
else:
    st.caption("No background jobs yet.")

st.subheader("3) Entity State")
rows = _entity_rows()
if rows:
    for eid, ename, live_files, total_files in rows:
        status = "LIVE" if live_files and live_files > 0 else "NOT LIVE"
        st.write(
            f"- {ename} (id={eid}) | files: {live_files or 0}/{total_files or 0} LIVE | status: {status}"
        )
else:
    st.caption("No entities yet.")

st.subheader("4) Deterministic Tool Actions")
entities = conn.execute("SELECT entity_id, entity_name FROM entities ORDER BY entity_name").fetchall()
if entities:
    labels = {f"{name} (id={eid})": eid for eid, name in entities}
    selected = st.selectbox("Entity", list(labels.keys()))
    selected_id = labels[selected]

    tabs = st.tabs(
        [
            "Fetch Metric",
            "Variance",
            "Ratio",
            "Sources",
            "Context",
            "Router (Free Text)",
        ]
    )

    try:
        m = list_available_metrics(conn, selected_id)
        metric_options = [x["metric"] for x in m.get("metrics", [])]
    except Exception:
        metric_options = []

    with tabs[0]:
        metric = (
            st.selectbox("Metric", metric_options, key="fetch_metric")
            if metric_options
            else st.text_input("Metric", key="fetch_metric_text")
        )
        period = st.text_input("Period", value="FY24", key="fetch_period")
        if st.button("Run fetch_metric", key="btn_fetch"):
            try:
                payload = fetch_metric(conn, selected_id, metric, period)
                st.json(payload)
                _render_citations(payload)
                with st.expander("Show your work"):
                    st.code(
                        f"fetch_metric(entity_id={selected_id}, metric='{metric}', period='{period}')"
                    )
            except ToolError as e:
                st.error(str(e))

    with tabs[1]:
        metric = (
            st.selectbox("Metric", metric_options, key="var_metric")
            if metric_options
            else st.text_input("Metric", key="var_metric_text")
        )
        p1 = st.text_input("Period 1", value="FY23", key="var_p1")
        p2 = st.text_input("Period 2", value="FY24", key="var_p2")
        if st.button("Run calculate_variance", key="btn_var"):
            try:
                payload = calculate_variance(conn, selected_id, metric, p1, p2)
                st.json(payload)
                _render_citations(payload)
                with st.expander("Show your work"):
                    st.code(
                        f"calculate_variance(entity_id={selected_id}, metric='{metric}', period_1='{p1}', period_2='{p2}')"
                    )
            except ToolError as e:
                st.error(str(e))

    with tabs[2]:
        num = (
            st.selectbox("Numerator", metric_options, key="ratio_num")
            if metric_options
            else st.text_input("Numerator", key="ratio_num_text")
        )
        den = (
            st.selectbox("Denominator", metric_options, key="ratio_den")
            if metric_options
            else st.text_input("Denominator", key="ratio_den_text")
        )
        p = st.text_input("Period", value="FY24", key="ratio_period")
        if st.button("Run calculate_ratio", key="btn_ratio"):
            try:
                payload = calculate_ratio(conn, selected_id, num, den, p)
                st.json(payload)
                _render_citations(payload)
                with st.expander("Show your work"):
                    st.code(
                        f"calculate_ratio(entity_id={selected_id}, numerator='{num}', denominator='{den}', period='{p}')"
                    )
            except ToolError as e:
                st.error(str(e))

    with tabs[3]:
        metric = (
            st.selectbox("Metric", metric_options, key="src_metric")
            if metric_options
            else st.text_input("Metric", key="src_metric_text")
        )
        period = st.text_input("Period", value="FY24", key="src_period")
        if st.button("Run list_sources", key="btn_sources"):
            try:
                payload = list_sources(conn, selected_id, metric, period)
                st.json(payload)
                with st.expander("Show your work"):
                    st.code(
                        f"list_sources(entity_id={selected_id}, metric='{metric}', period='{period}')"
                    )
            except ToolError as e:
                st.error(str(e))

    with tabs[4]:
        query = st.text_input("Context query", value="Explain revenue growth drivers", key="ctx_q")
        period_filter = st.text_input("Period filter (optional)", value="", key="ctx_period")
        metric_filter = st.text_input("Metric filter (optional)", value="", key="ctx_metric")
        if st.button("Run search_context", key="btn_context"):
            try:
                payload = search_context(
                    conn,
                    selected_id,
                    query,
                    period_filter=period_filter or None,
                    metric_filter=metric_filter or None,
                )
                st.json(payload)
                with st.expander("Show your work"):
                    st.code(
                        "search_context(entity_id={}, query='{}', period_filter={}, metric_filter={})".format(
                            selected_id,
                            query,
                            repr(period_filter or None),
                            repr(metric_filter or None),
                        )
                    )
            except ToolError as e:
                st.error(str(e))

    with tabs[5]:
        q = st.text_input("Ask a free-text question", key="router_q")
        if st.button("Run deterministic router", key="btn_router") and q.strip():
            out = answer_question(conn, selected_id, q)
            st.write(out.answer)
            st.json({"tool": out.tool_name, "payload": out.payload})
            with st.expander("Show your work"):
                st.code(f"answer_question(entity_id={selected_id}, question={q!r})")
else:
    st.caption("Upload and process at least one file first.")
