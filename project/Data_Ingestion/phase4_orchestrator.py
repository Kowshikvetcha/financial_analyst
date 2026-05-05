from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import json
import os
import re
import time
import urllib.error
import urllib.request

import duckdb

from periods import parse_period
from phase3_tools import (
    list_available_metrics,
    fetch_metric,
    calculate_variance,
    calculate_ratio,
    list_sources,
    search_context,
    ToolError,
    MetricNotFound,
    PeriodNotFound,
)

AUDIT_PATH = Path(__file__).parent / "tool_audit_log.jsonl"

ALLOWED_TOOLS = {
    "list_available_metrics",
    "fetch_metric",
    "calculate_variance",
    "calculate_ratio",
    "list_sources",
    "search_context",
}

TOOL_REQUIRED_ARGS = {
    "list_available_metrics": set(),
    "fetch_metric": {"metric", "period"},
    "calculate_variance": {"metric", "period_1", "period_2"},
    "calculate_ratio": {"numerator", "denominator", "period"},
    "list_sources": {"metric", "period"},
    "search_context": {"query"},
}

TOOL_OPTIONAL_ARGS = {
    "search_context": {"period_filter", "metric_filter"},
    "fetch_metric": {"unit_out"},
    "calculate_variance": {"unit_out"},
    "calculate_ratio": {"unit_out"},
}


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
    row = conn.execute(
        "SELECT COUNT(*) FROM source_files WHERE entity_id = ? AND state = 'LIVE'",
        [entity_id],
    ).fetchone()
    return bool(row and row[0] > 0)


def _canonicalise_period_tokens(question: str) -> list[str]:
    candidates = set()
    patterns = [
        r"FY\s*\d{2,4}(?:[-_]?Q[1-4]|[-_]?M\d{1,2})?",
        r"Q[1-4]\s*FY\s*\d{2,4}",
        r"Q[1-4]['’]?\d{2}",
        r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-\s]\d{2,4}",
        r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}",
        r"20\d{2}",
    ]
    for p in patterns:
        for m in re.finditer(p, question, re.I):
            raw = m.group(0)
            spec = parse_period(raw)
            if spec:
                candidates.add(spec.canonical)
    return sorted(candidates)


def _metric_alias_map(metrics: list[dict]) -> dict[str, str]:
    alias = {}
    for m in metrics:
        metric = m["metric"]
        alias[metric.lower()] = metric
        alias[metric.replace("_", " ").lower()] = metric
    lightweight = {
        "revenue": "revenue_net",
        "net revenue": "revenue_net",
        "gross revenue": "revenue_gross",
        "profit": "pat",
        "net profit": "pat",
        "ebitda": "ebitda",
        "gross margin": "gross_margin_pct",
        "ebitda margin": "ebitda_margin_pct",
        "pat margin": "pat_margin_pct",
        "cogs": "cogs",
        "opex": "opex",
    }
    for key, val in lightweight.items():
        alias.setdefault(key, val)
    return alias


def _extract_metric(question: str, available_metrics: list[dict]) -> Optional[str]:
    q = question.lower()
    alias = _metric_alias_map(available_metrics)
    for token in sorted(alias.keys(), key=len, reverse=True):
        if token in q:
            return alias[token]
    return None


def _extract_ratio_parts(question: str, available_metrics: list[dict]) -> tuple[Optional[str], Optional[str]]:
    q = question.lower()
    alias = _metric_alias_map(available_metrics)
    for sep in ["/", " over ", " divided by "]:
        if sep in q:
            lhs, rhs = q.split(sep, 1)
            n = _extract_metric(lhs, available_metrics)
            d = _extract_metric(rhs, available_metrics)
            return n, d

    if "margin" in q:
        if "ebitda" in q:
            return "ebitda", "revenue_net"
        if "gross" in q:
            return "gross_profit", "revenue_net"
        if "pat" in q or "net profit" in q:
            return "pat", "revenue_net"

    found = []
    for token in sorted(alias.keys(), key=len, reverse=True):
        if token in q:
            m = alias[token]
            if m not in found:
                found.append(m)
            if len(found) == 2:
                break
    if len(found) == 2:
        return found[0], found[1]
    return None, None


def _cross_check_numerical_claims(payload: dict) -> dict:
    flagged = 0
    for row in payload.get("results", []):
        if row.get("contains_numerical_claim") and not row.get("linked_fact_ids"):
            row["needs_verification"] = True
            flagged += 1
    payload["duckdb_cross_check"] = {
        "status": "requires_review" if flagged else "ok",
        "flagged_chunks": flagged,
        "policy": "Narrative numerical claims without fact links must be treated as unverified.",
    }
    return payload


def _response_for_error(e: Exception) -> OrchestratorResult:
    if isinstance(e, PeriodNotFound):
        return OrchestratorResult(str(e), "error", {"error_type": "PeriodNotFound"})
    if isinstance(e, MetricNotFound):
        return OrchestratorResult(str(e), "error", {"error_type": "MetricNotFound"})
    if isinstance(e, ToolError):
        return OrchestratorResult(str(e), "error", {"error_type": "ToolError"})
    return OrchestratorResult(f"Unexpected error: {e}", "error", {"error_type": "UnexpectedError"})


def _cloud_router_enabled() -> bool:
    return os.environ.get("ORCH_CLOUD_ROUTER_ENABLED", "0").strip() in {"1", "true", "True"}


def _json_from_maybe_fenced(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            cleaned = "\n".join(lines[1:-1]).strip()
    obj = json.loads(cleaned)
    if not isinstance(obj, dict):
        raise ValueError("planner output must be a JSON object")
    return obj


def _extract_content_from_provider_response(out: dict) -> str:
    if not isinstance(out, dict):
        return ""

    # OpenAI-like
    choices = out.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        t = item.get("text")
                        if isinstance(t, str):
                            parts.append(t)
                return "\n".join(parts)

    # Anthropic-like minimal fallback
    content = out.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)

    return ""


def _call_cloud_planner(question: str, available_metrics: list[str]) -> dict:
    api_url = os.environ.get("ORCH_LLM_API_URL", "").strip()
    api_key = os.environ.get("ORCH_LLM_API_KEY", "").strip()
    model = os.environ.get("ORCH_LLM_MODEL", "gpt-4o-mini").strip()

    if not api_url or not api_key:
        raise RuntimeError("cloud router enabled but ORCH_LLM_API_URL/ORCH_LLM_API_KEY not set")

    system = (
        "You are a routing planner. Output one JSON object only. "
        "Never do arithmetic. Never output SQL. Choose exactly one deterministic tool."
    )

    tool_schema = {
        "allowed_tools": sorted(ALLOWED_TOOLS),
        "required_args": {k: sorted(v) for k, v in TOOL_REQUIRED_ARGS.items()},
        "optional_args": {
            "search_context": ["period_filter", "metric_filter"],
            "fetch_metric": ["unit_out"],
            "calculate_variance": ["unit_out"],
            "calculate_ratio": ["unit_out"],
        },
    }

    user_payload = {
        "question": question,
        "available_metrics": available_metrics,
        "output_contract": {"tool": "string", "args": "object"},
        "tool_schema": tool_schema,
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload)},
        ],
        "temperature": 0,
    }

    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"cloud planner HTTP {e.code}: {body[:300]}")

    content = _extract_content_from_provider_response(out)
    if not content:
        raise RuntimeError("cloud planner returned no content")

    return _json_from_maybe_fenced(content)


def _validate_plan(plan: dict) -> tuple[str, dict]:
    tool = plan.get("tool")
    args = plan.get("args", {})

    if not isinstance(tool, str):
        raise ValueError("plan.tool must be string")
    if tool not in ALLOWED_TOOLS:
        raise ValueError(f"tool '{tool}' not allowed")
    if not isinstance(args, dict):
        raise ValueError("plan.args must be object")
    if len(args) > 12:
        raise ValueError("plan.args too large")

    required = TOOL_REQUIRED_ARGS[tool]
    missing = [k for k in required if k not in args]
    if missing:
        raise ValueError(f"missing args for {tool}: {missing}")

    allowed_keys = set(required) | TOOL_OPTIONAL_ARGS.get(tool, set())
    unknown = [k for k in args.keys() if k not in allowed_keys]
    if unknown:
        raise ValueError(f"unknown args for {tool}: {unknown}")

    # Type + basic sanity checks on all present args.
    for k, v in args.items():
        if v is None:
            continue
        if not isinstance(v, str):
            raise ValueError(f"arg '{k}' must be string")
        if len(v.strip()) == 0:
            raise ValueError(f"arg '{k}' cannot be empty")
        if len(v) > 200:
            raise ValueError(f"arg '{k}' too long")

    return tool, args


def _execute_plan(conn: duckdb.DuckDBPyConnection, entity_id: int, tool: str, args: dict) -> OrchestratorResult:
    if tool == "list_available_metrics":
        payload = list_available_metrics(conn, entity_id)
        return OrchestratorResult("Here are the currently queryable metrics for this entity.", tool, payload)

    if tool == "fetch_metric":
        payload = fetch_metric(conn, entity_id, args["metric"], args["period"], args.get("unit_out"))
        return OrchestratorResult("Fetched metric deterministically.", tool, payload)

    if tool == "calculate_variance":
        payload = calculate_variance(
            conn,
            entity_id,
            args["metric"],
            args["period_1"],
            args["period_2"],
            args.get("unit_out"),
        )
        return OrchestratorResult("Computed deterministic variance.", tool, payload)

    if tool == "calculate_ratio":
        payload = calculate_ratio(
            conn,
            entity_id,
            args["numerator"],
            args["denominator"],
            args["period"],
            args.get("unit_out"),
        )
        return OrchestratorResult("Computed deterministic ratio.", tool, payload)

    if tool == "list_sources":
        payload = list_sources(conn, entity_id, args["metric"], args["period"])
        return OrchestratorResult("Found deterministic source lineage.", tool, payload)

    if tool == "search_context":
        payload = search_context(
            conn,
            entity_id,
            args["query"],
            period_filter=args.get("period_filter"),
            metric_filter=args.get("metric_filter"),
        )
        payload = _cross_check_numerical_claims(payload)
        return OrchestratorResult("Found qualitative context matches.", tool, payload)

    raise ValueError(f"unsupported tool: {tool}")


def _answer_question_local(conn: duckdb.DuckDBPyConnection, entity_id: int, question: str) -> OrchestratorResult:
    q = question.strip()
    ql = q.lower()

    metrics_info = list_available_metrics(conn, entity_id)
    available_metrics = metrics_info.get("metrics", [])

    try:
        if any(token in ql for token in ["available metrics", "what metrics", "list metrics"]):
            _audit("list_available_metrics", {"entity_id": entity_id}, "ok")
            return OrchestratorResult(
                answer="Here are the currently queryable metrics for this entity.",
                tool_name="list_available_metrics",
                payload=metrics_info,
            )

        if any(token in ql for token in ["source", "citation", "where did", "which file"]):
            metric = _extract_metric(q, available_metrics)
            periods = _canonicalise_period_tokens(q)
            if not metric or not periods:
                return OrchestratorResult(
                    answer="To list sources, provide both metric and period.",
                    tool_name="list_sources",
                    payload={"available_metrics": available_metrics[:20]},
                )
            payload = list_sources(conn, entity_id, metric, periods[0])
            _audit("list_sources", {"entity_id": entity_id, "metric": metric, "period": periods[0]}, "ok")
            return OrchestratorResult("Found deterministic source lineage.", "list_sources", payload)

        if any(token in ql for token in ["context", "why", "explain", "narrative", "qualitative"]):
            metric = _extract_metric(q, available_metrics)
            periods = _canonicalise_period_tokens(q)
            payload = search_context(
                conn,
                entity_id,
                q,
                period_filter=periods[0] if periods else None,
                metric_filter=metric,
            )
            payload = _cross_check_numerical_claims(payload)
            _audit("search_context", {"entity_id": entity_id, "query": q}, "ok")
            return OrchestratorResult("Found qualitative context matches.", "search_context", payload)

        if any(token in ql for token in ["variance", "change", "growth", "delta"]):
            metric = _extract_metric(q, available_metrics)
            periods = _canonicalise_period_tokens(q)
            if not metric or len(periods) < 2:
                return OrchestratorResult(
                    answer="For variance, provide metric and two periods.",
                    tool_name="calculate_variance",
                    payload={"available_metrics": available_metrics[:20]},
                )
            payload = calculate_variance(conn, entity_id, metric, periods[0], periods[1])
            _audit(
                "calculate_variance",
                {"entity_id": entity_id, "metric": metric, "period_1": periods[0], "period_2": periods[1]},
                "ok",
            )
            return OrchestratorResult("Computed deterministic variance.", "calculate_variance", payload)

        if any(token in ql for token in ["ratio", "margin", "divided by", "/"]):
            n, d = _extract_ratio_parts(q, available_metrics)
            periods = _canonicalise_period_tokens(q)
            if not n or not d or not periods:
                return OrchestratorResult(
                    answer="For ratio, provide numerator, denominator, and one period.",
                    tool_name="calculate_ratio",
                    payload={"available_metrics": available_metrics[:20]},
                )
            payload = calculate_ratio(conn, entity_id, n, d, periods[0])
            _audit(
                "calculate_ratio",
                {"entity_id": entity_id, "numerator": n, "denominator": d, "period": periods[0]},
                "ok",
            )
            return OrchestratorResult("Computed deterministic ratio.", "calculate_ratio", payload)

        metric = _extract_metric(q, available_metrics)
        periods = _canonicalise_period_tokens(q)
        if metric and periods:
            payload = fetch_metric(conn, entity_id, metric, periods[0])
            _audit("fetch_metric", {"entity_id": entity_id, "metric": metric, "period": periods[0]}, "ok")
            return OrchestratorResult("Fetched metric deterministically.", "fetch_metric", payload)

        _audit("list_available_metrics", {"entity_id": entity_id, "question": q}, "needs_parameters")
        return OrchestratorResult(
            answer="Tell me a metric and period, or ask for available metrics.",
            tool_name="list_available_metrics",
            payload={"available_metrics": available_metrics[:25]},
        )

    except Exception as e:
        _audit("tool_error", {"entity_id": entity_id, "question": q}, str(e))
        return _response_for_error(e)


def answer_question(conn: duckdb.DuckDBPyConnection, entity_id: int, question: str) -> OrchestratorResult:
    if not _is_entity_live(conn, entity_id):
        return OrchestratorResult(
            answer="This entity is not LIVE yet. Complete onboarding/conflict resolution first.",
            tool_name="state_gate",
            payload={},
        )

    if _cloud_router_enabled():
        try:
            metrics = list_available_metrics(conn, entity_id)
            metric_names = [m["metric"] for m in metrics.get("metrics", [])]
            plan = _call_cloud_planner(question, metric_names)
            tool, args = _validate_plan(plan)
            _audit("cloud_plan", {"entity_id": entity_id, "plan": plan}, "ok")
            result = _execute_plan(conn, entity_id, tool, args)
            _audit(tool, {"entity_id": entity_id, "args": args, "mode": "cloud_plan"}, "ok")
            return result
        except Exception as e:
            _audit("cloud_plan", {"entity_id": entity_id, "question": question}, f"fallback_local:{e}")

    return _answer_question_local(conn, entity_id, question)
