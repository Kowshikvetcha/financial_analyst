from __future__ import annotations

import sys
from pathlib import Path
import tempfile

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "Data_Ingestion"))

from schema import get_connection, initialise_schema, get_or_create_entity, register_file, update_file_state
from file_reader import ingest_file
from conflict_resolver import promote_to_live, compute_derived_kpis
from phase4_orchestrator import answer_question


st.set_page_config(page_title="Financial AI Co-Pilot", layout="wide")
st.title("Financial AI Co-Pilot")

conn = get_connection(Path(__file__).parent / "Data_Ingestion" / "financial_agent.duckdb")
initialise_schema(conn)

uploaded = st.file_uploader("Upload CSV/XLSX/PDF/DOCX", type=["csv", "xlsx", "xls", "pdf", "docx"])

if uploaded is not None:
    suffix = Path(uploaded.name).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded.getbuffer())
        tmp_path = Path(tmp.name)

    entity_name = st.text_input("Entity name", value=Path(uploaded.name).stem)
    if st.button("Process file"):
        entity_id = get_or_create_entity(conn, entity_name)
        file_id = register_file(conn, entity_id, uploaded.name, str(tmp_path), suffix.lstrip("."), detected_unit="")
        update_file_state(conn, file_id, "SCHEMA_MAPPED")
        if suffix in (".csv", ".xlsx", ".xls"):
            result = ingest_file(conn, tmp_path, file_id, entity_id)
            update_file_state(conn, file_id, "LIVE")
            slug = entity_name.strip().lower().replace(" ", "_")
            promote_to_live(conn, entity_id, slug)
            compute_derived_kpis(conn, entity_id, slug)
            st.success(f"Processed. facts_staged={result['facts_staged']}")
        else:
            st.info("Qualitative files are indexed through the Phase 2 pipeline command flow.")

st.subheader("Ask Questions")
entities = conn.execute("SELECT entity_id, entity_name FROM entities ORDER BY entity_name").fetchall()
if entities:
    id_map = {f"{e[1]} (id={e[0]})": e[0] for e in entities}
    selected = st.selectbox("Entity", list(id_map.keys()))
    q = st.text_input("Question")
    if st.button("Ask") and q.strip():
        out = answer_question(conn, id_map[selected], q)
        st.write(out.answer)
        st.json(out.payload)
else:
    st.caption("No entities yet. Upload and process a file first.")
