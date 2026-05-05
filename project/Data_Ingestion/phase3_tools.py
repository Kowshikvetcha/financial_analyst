from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, getcontext
from typing import Optional
import json

import duckdb

from qualitative import _get_chroma_client
from units import format_for_display
from schema import get_qualitative_chunks

getcontext().prec = 28


class ToolError(Exception):
    pass


class MetricNotFound(ToolError):
    pass


class EntityNotFound(ToolError):
    pass


class PeriodNotFound(ToolError):
    pass


class AmbiguousEntity(ToolError):
    pass


class DivisionByZero(ToolError):
    pass


@dataclass
class CitationEnvelope:
    source_file_id: int
    source_sheet: Optional[str]
    cell_reference: Optional[str]
    fact_id: str


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ToolError(f"invalid numeric value: {value}")


def _resolve_entity_id(conn: duckdb.DuckDBPyConnection, entity: str | int) -> int:
    if isinstance(entity, int):
        row = conn.execute("SELECT entity_id FROM entities WHERE entity_id = ?", [entity]).fetchone()
        if not row:
            raise EntityNotFound(f"entity_id={entity} not found")
        return entity

    q = entity.strip().lower()
    rows = conn.execute(
        "SELECT entity_id FROM entities WHERE lower(entity_name) LIKE ? OR entity_slug LIKE ?",
        [f"%{q}%", f"%{q.replace(' ', '_')}%"],
    ).fetchall()
    if not rows:
        raise EntityNotFound(f"entity='{entity}' not found")
    ids = sorted({r[0] for r in rows})
    if len(ids) > 1:
        raise AmbiguousEntity(f"entity='{entity}' resolved to {ids}")
    return ids[0]


def _citation_from_row(row) -> CitationEnvelope:
    return CitationEnvelope(source_file_id=row[6], source_sheet=row[7], cell_reference=row[8], fact_id=row[0])


def _list_valid_periods(conn: duckdb.DuckDBPyConnection, entity_id: int, metric: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT period
        FROM live_facts
        WHERE entity_id = ? AND canonical_field = ?
        ORDER BY period
        """,
        [entity_id, metric],
    ).fetchall()
    return [r[0] for r in rows]


def fetch_metric(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str | int,
    metric: str,
    period: str,
    unit_out: Optional[str] = None,
) -> dict:
    eid = _resolve_entity_id(conn, entity_id)
    row = conn.execute(
        """
        SELECT fact_id, canonical_field, period, value_normalised, currency,
               original_unit, source_file_id, source_sheet, cell_reference
        FROM live_facts
        WHERE entity_id = ? AND canonical_field = ? AND period = ?
        """,
        [eid, metric, period],
    ).fetchone()
    if not row:
        valid_periods = _list_valid_periods(conn, eid, metric)
        if valid_periods:
            raise PeriodNotFound(
                f"period='{period}' not found for metric='{metric}'. available={valid_periods}"
            )
        raise MetricNotFound(f"metric='{metric}' not found for entity_id={eid}")

    value_decimal = _to_decimal(row[3])
    out = {
        "entity_id": eid,
        "metric": row[1],
        "period": row[2],
        "value": str(value_decimal),
        "currency": row[4],
        "display": format_for_display(float(value_decimal), row[4], unit_out) if unit_out else None,
        "citation": _citation_from_row(row).__dict__,
    }
    return out


def calculate_variance(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str | int,
    metric: str,
    period_1: str,
    period_2: str,
    unit_out: Optional[str] = None,
) -> dict:
    a = fetch_metric(conn, entity_id, metric, period_1, unit_out=None)
    b = fetch_metric(conn, entity_id, metric, period_2, unit_out=None)
    va = _to_decimal(a["value"])
    vb = _to_decimal(b["value"])
    delta = vb - va
    pct = None if va == 0 else (delta / va) * Decimal("100")
    return {
        "entity_id": a["entity_id"],
        "metric": metric,
        "period_1": period_1,
        "period_2": period_2,
        "value_1": str(va),
        "value_2": str(vb),
        "delta": str(delta),
        "delta_pct": str(pct) if pct is not None else None,
        "display_delta": format_for_display(float(delta), a["currency"], unit_out) if unit_out else None,
        "citations": [a["citation"], b["citation"]],
    }


def calculate_ratio(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str | int,
    numerator: str,
    denominator: str,
    period: str,
    unit_out: Optional[str] = None,
) -> dict:
    n = fetch_metric(conn, entity_id, numerator, period)
    d = fetch_metric(conn, entity_id, denominator, period)
    nv = _to_decimal(n["value"])
    dv = _to_decimal(d["value"])
    if dv == 0:
        raise DivisionByZero(f"denominator='{denominator}' is zero for period={period}")
    r = nv / dv
    return {
        "entity_id": n["entity_id"],
        "numerator": numerator,
        "denominator": denominator,
        "period": period,
        "ratio": str(r),
        "ratio_pct": str(r * Decimal("100")),
        "citations": [n["citation"], d["citation"]],
        "display_hint": unit_out,
    }


def list_sources(conn: duckdb.DuckDBPyConnection, entity_id: str | int, metric: str, period: str) -> dict:
    eid = _resolve_entity_id(conn, entity_id)
    rows = conn.execute(
        """
        SELECT lf.fact_id, sf.filename, sf.file_path, lf.source_file_id, lf.source_sheet, lf.cell_reference
        FROM live_facts lf JOIN source_files sf ON lf.source_file_id = sf.file_id
        WHERE lf.entity_id = ? AND lf.canonical_field = ? AND lf.period = ?
        """,
        [eid, metric, period],
    ).fetchall()
    if not rows:
        raise MetricNotFound(f"no sources for metric='{metric}' period='{period}'")
    return {
        "entity_id": eid,
        "metric": metric,
        "period": period,
        "sources": [
            {
                "fact_id": r[0],
                "filename": r[1],
                "file_path": r[2],
                "source_file_id": r[3],
                "source_sheet": r[4],
                "cell_reference": r[5],
            }
            for r in rows
        ],
    }


def list_available_metrics(conn: duckdb.DuckDBPyConnection, entity_id: str | int) -> dict:
    eid = _resolve_entity_id(conn, entity_id)
    rows = conn.execute(
        """
        SELECT canonical_field, COUNT(*) AS n
        FROM live_facts
        WHERE entity_id = ?
        GROUP BY canonical_field
        ORDER BY canonical_field
        """,
        [eid],
    ).fetchall()
    return {"entity_id": eid, "metrics": [{"metric": r[0], "observations": r[1]} for r in rows]}


def search_context(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str | int,
    query: str,
    period_filter: Optional[str] = None,
    metric_filter: Optional[str] = None,
) -> dict:
    eid = _resolve_entity_id(conn, entity_id)
    chunks = get_qualitative_chunks(conn, eid)
    by_doc_id = {c.get("chroma_document_id"): c for c in chunks if c.get("chroma_document_id")}

    if period_filter:
        chunks = [c for c in chunks if period_filter.lower() in (c.get("linked_periods") or "").lower()]
    if metric_filter:
        chunks = [c for c in chunks if metric_filter.lower() in (c.get("linked_metrics") or "").lower()]

    top = []
    used_fallback = False
    try:
        client = _get_chroma_client()
        col_rows = conn.execute(
            "SELECT DISTINCT file_id FROM qualitative_chunks WHERE entity_id = ? AND chroma_document_id IS NOT NULL",
            [eid],
        ).fetchall()
        for (fid,) in col_rows:
            cname = f"chunks_e{eid}_f{fid}"
            coll = client.get_collection(name=cname)
            res = coll.query(query_texts=[query], n_results=8)
            for doc_id in (res.get("ids") or [[]])[0]:
                if doc_id in by_doc_id:
                    top.append(by_doc_id[doc_id])
    except Exception:
        used_fallback = True
        query_terms = [t for t in query.lower().split() if len(t) > 2]
        scored = []
        for c in chunks:
            text = (c.get("raw_text") or "").lower()
            score = sum(1 for t in query_terms if t in text)
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [c for _, c in scored[:8]]

    results = []
    for c in top:
        linked = c.get("linked_fact_ids")
        if isinstance(linked, str):
            try:
                linked = json.loads(linked or "[]")
            except json.JSONDecodeError:
                linked = []

        results.append(
            {
                "chunk_id": c["chunk_id"],
                "section_path": c.get("section_path"),
                "contains_numerical_claim": c.get("contains_numerical_claim", False),
                "raw_text": (c.get("raw_text") or "")[:600],
                "linked_fact_ids": linked,
                "citation": {
                    "source_file_id": c.get("file_id"),
                    "chunk_id": c.get("chunk_id"),
                },
            }
        )

    return {
        "entity_id": eid,
        "query": query,
        "period_filter": period_filter,
        "metric_filter": metric_filter,
        "search_mode": "keyword_fallback" if used_fallback else "chroma_similarity",
        "results": results,
    }
