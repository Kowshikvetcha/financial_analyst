from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import time

import duckdb

from phase3_tools import (
    list_available_metrics,
    fetch_metric,
    calculate_variance,
    calculate_ratio,
    search_context,
    ToolError,
)


AUDIT_PATH = Path(__file__).parent / "tool_audit_log.jsonl"


@dataclass
class OrchestratorResult:
    answer: str
    tool_name: str
    payload: dict


def _audit(tool_name: str, args: dict, outcome: str) -> None:
    entry = {"ts": int(time.time()), "tool": tool_name, "args": args, "outcome": outcome}
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _is_entity_live(conn: duckdb.DuckDBPyConnection, entity_id: int) -> bool:
    row = conn.execute("SELECT COUNT(*) FROM source_files WHERE entity_id = ? AND state = 'LIVE'", [entity_id]).fetchone()
    return bool(row and row[0] > 0)


def answer_question(conn: duckdb.DuckDBPyConnection, entity_id: int, question: str) -> OrchestratorResult:
    if not _is_entity_live(conn, entity_id):
        return OrchestratorResult(
            answer="This entity is not LIVE yet. Complete onboarding/conflict resolution first.",
            tool_name="state_gate",
            payload={},
        )

    metrics = list_available_metrics(conn, entity_id)
    q = question.lower().strip()

    try:
        if "variance" in q or "change" in q:
            _audit("calculate_variance", {"entity_id": entity_id, "question": question}, "started")
            payload = {"hint": "Provide explicit metric and periods in UI for deterministic routing.", "available_metrics": metrics["metrics"][:15]}
            _audit("calculate_variance", {"entity_id": entity_id}, "needs_parameters")
            return OrchestratorResult("I can compute variance once you provide metric and two periods.", "calculate_variance", payload)
        if "ratio" in q or "margin" in q:
            _audit("calculate_ratio", {"entity_id": entity_id, "question": question}, "started")
            payload = {"hint": "Provide numerator, denominator and period.", "available_metrics": metrics["metrics"][:15]}
            _audit("calculate_ratio", {"entity_id": entity_id}, "needs_parameters")
            return OrchestratorResult("I can compute ratio once you provide numerator, denominator and period.", "calculate_ratio", payload)
        if "context" in q or "why" in q or "explain" in q:
            _audit("search_context", {"entity_id": entity_id, "query": question}, "started")
            payload = search_context(conn, entity_id, question)
            _audit("search_context", {"entity_id": entity_id}, "ok")
            return OrchestratorResult("Found qualitative context matches.", "search_context", payload)

        _audit("fetch_metric", {"entity_id": entity_id, "question": question}, "fallback")
        payload = {"available_metrics": metrics["metrics"][:20]}
        return OrchestratorResult("Tell me metric + period for a deterministic fetch.", "list_available_metrics", payload)
    except ToolError as e:
        _audit("tool_error", {"entity_id": entity_id, "question": question}, str(e))
        return OrchestratorResult(f"Tool error: {e}", "error", {})
