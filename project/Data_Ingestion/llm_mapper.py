"""
Stage 5: LLM Schema Mapper for Phase 1.

When resolve_alias() can't match a raw header to a canonical field via the
static ALIAS_MAP, this module calls the LLM (Claude) to suggest a mapping.

Design:
  1. Before staging facts, call LLM with the full list of unmapped headers
     for this sheet, plus the canonical field registry.
  2. LLM returns a list of {raw_header, suggested_canonical_field, confidence, reasoning}
  3. Mappings are stored in schema_mappings table with mapped_by='llm'
  4. Low-confidence LLM mappings are flagged for human review (Stage 7)

API:
  llm_map_headers(unmapped_headers, file_context) -> list[SchemaMapping]
  get_llm_mapping(raw_header) -> Optional[str]  # cache lookup
"""

import os
import json
import time
import hashlib
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

# LLM API config — set ANTHROPIC_API_KEY in environment
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# Cache file for LLM mappings (persistent across runs)
MAPPING_CACHE_PATH = Path(__file__).parent / "llm_mapping_cache.json"


@dataclass
class SchemaMapping:
    """Result from LLM schema mapping."""
    raw_header: str
    canonical_field: Optional[str]  # None = could not map
    confidence: str  # 'high' | 'medium' | 'low'
    reasoning: str
    mapped_by: str = 'llm'
    needs_review: bool = False  # True = requires human confirmation


# ── Canonical fields for LLM context ──────────────────────────────────────────

CANONICAL_FIELDS_SUMMARY = """
## Canonical Field Registry (58 fields across 6 categories)

### REVENUE
- revenue_gross: Total revenue before deductions (Gross Revenue, Total Revenue, Sales)
- revenue_net: Revenue after deductions (Net Revenue, Net Sales, Turnover, Revenue from Operations)
- gmv: Gross Merchandise Value (platform total before returns)
- arr: Annual Recurring Revenue (subscription/SaaS)
- returns_refunds: Returns and Refunds deducted from gross
- subscription_revenue: Recurring subscription revenue
- professional_services_revenue: Implementation/consulting revenue
- new_arr: New ARR added in period
- churned_arr: ARR lost to churn

### COSTS
- cogs: Cost of Goods Sold (material, direct costs, hosting)
- opex: Operating Expenses (indirect costs, admin, overhead)
- salary_expense: Salary and Employee Benefits (wages, PF, labour)
- marketing_expense: Marketing and Advertising (ads, promotions)
- rent_expense: Rent and Facilities (office, warehouse)
- platform_fees: Marketplace fees (Amazon, Flipkart commissions)
- logistics_expense: Logistics and Shipping (freight, delivery)
- packaging_expense: Packaging materials
- rd_expense: Research and Development

### PROFITABILITY
- gross_profit: Revenue Net minus COGS (derived)
- gross_margin_pct: Gross Profit / Revenue × 100 (derived)
- ebitda: Earnings before Interest, Tax, D&A
- ebitda_margin_pct: EBITDA / Revenue × 100 (derived)
- pat: Profit After Tax (net profit)
- pat_margin_pct: PAT / Revenue × 100 (derived)

### BALANCE SHEET
- accounts_receivable: Amounts owed by customers (Debtors, Trade Receivables)
- accounts_payable: Amounts owed to suppliers (Creditors, Trade Payables)
- inventory: Stock of goods held (Closing Stock)
- cash_and_equivalents: Cash on hand + short-term investments
- total_debt: All interest-bearing borrowings (Loans, Borrowings)

### CASH FLOW
- (to be extended as needed)

### OPERATIONAL
- headcount: Total employees (FTE)
- customer_count: Number of active customers (Logo Count)
- order_count: Number of orders processed
- net_dollar_retention: NDR % (revenue retained + expansion)
- gross_dollar_retention: GDR % (revenue retained, no expansion)
- logo_churn_count: Customers lost in period
"""


# ── LLM call ──────────────────────────────────────────────────────────────────

def _call_anthropic(messages: list[dict], system: str) -> str:
    """Call Anthropic Claude API. Returns response content or raises exception."""
    import urllib.request
    import urllib.error

    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set in environment. "
            "Set it with: export ANTHROPIC_API_KEY=sk-ant-..."
        )

    payload = {
        "model": "claude-opus-4-5-20251101",
        "max_tokens": 1024,
        "system": system,
        "messages": messages,
    }

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"Anthropic API error {e.code}: {body}")
    except Exception as e:
        raise RuntimeError(f"Anthropic API call failed: {e}")


# ── Mapping cache ─────────────────────────────────────────────────────────────

def _load_cache() -> dict[str, dict]:
    """Load persistent LLM mapping cache."""
    if MAPPING_CACHE_PATH.exists():
        try:
            with open(MAPPING_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_cache(cache: dict) -> None:
    """Save LLM mapping cache."""
    with open(MAPPING_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


# ── Main LLM mapping function ──────────────────────────────────────────────────

def llm_map_headers(
    unmapped_headers: list[str],
    file_context: Optional[dict] = None,
) -> list[SchemaMapping]:
    """
    Call LLM to map unmapped raw headers to canonical fields.

    Args:
        unmapped_headers: List of raw column/row headers that resolve_alias()
                          couldn't match to a canonical field.
        file_context: Optional dict with keys:
            - entity_name: str
            - filename: str
            - sheet_name: str
            - detected_unit: str
            - layout: str ('wide' | 'tall')

    Returns:
        List of SchemaMapping objects. canonical_field may be None if LLM
        couldn't confidently map the header.

    Raises:
        RuntimeError if ANTHROPIC_API_KEY not set or API call fails.
    """
    if not unmapped_headers:
        return []

    file_ctx_str = ""
    if file_context:
        file_ctx_str = f"""
## File Context
- Entity: {file_context.get('entity_name', 'unknown')}
- File: {file_context.get('filename', 'unknown')}
- Sheet: {file_context.get('sheet_name', 'N/A')}
- Unit: {file_context.get('detected_unit', 'unknown')}
- Layout: {file_context.get('layout', 'unknown')}
"""

    system_prompt = f"""You are a financial data schema mapper for a Private Equity analysis system.

Your job: Map raw column/row headers from financial files to canonical field names.

{file_ctx_str}

{CANONICAL_FIELDS_SUMMARY}

## Rules
1. Only map to canonical fields from the registry above.
2. If a header has no clear match, set canonical_field to null.
3. Confidence levels:
   - high: header clearly matches (exact word match, standard accounting term)
   - medium: reasonable match but could be ambiguous (fuzzy match on partial keyword)
   - low: guess based on weak signal (needs human review → set needs_review=true)
4. Include brief reasoning for each mapping.
5. Output ONLY valid JSON array with no markdown, no commentary.
6. If the header describes something NOT in the canonical registry (e.g., a qualitative note, a date, a section label), map it to null and explain why.

## Output format
Return a JSON array of objects:
[
  {{"raw_header": "...", "canonical_field": "field_name" or null, "confidence": "high|medium|low", "reasoning": "...", "needs_review": true|false}}
]

Process ALL headers in the list. Do not skip any."""

    headers_json = json.dumps(unmapped_headers, ensure_ascii=False)

    messages = [
        {
            "role": "user",
            "content": f"Unmapped headers to map:\n{headers_json}",
        }
    ]

    response = _call_anthropic(messages, system_prompt)

    # Parse JSON response
    try:
        # Try to extract JSON from response (strip markdown code blocks if present)
        cleaned = response.strip()
        if cleaned.startswith("```"):
            # Strip markdown code block
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1])  # Remove ```json and ``` lines
        mappings_data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM returned invalid JSON: {e}\nResponse was: {response[:500]}")

    # Validate response structure
    if not isinstance(mappings_data, list):
        raise RuntimeError(f"LLM returned non-list: {type(mappings_data)}")

    results = []
    for item in mappings_data:
        if not isinstance(item, dict):
            continue
        results.append(SchemaMapping(
            raw_header=item.get("raw_header", ""),
            canonical_field=item.get("canonical_field"),
            confidence=item.get("confidence", "low"),
            reasoning=item.get("reasoning", ""),
            mapped_by="llm",
            needs_review=item.get("needs_review", False),
        ))

    return results


# ── Cache integration ────────────────────────────────────────────────────────

def get_cached_mapping(raw_header: str) -> Optional[str]:
    """
    Check persistent cache for a previously LLM-mapped header.
    Returns canonical field name or None.
    """
    cache = _load_cache()
    key = hashlib.md5(raw_header.strip().lower().encode()).hexdigest()
    entry = cache.get(key)
    if entry:
        return entry.get("canonical_field")
    return None


def cache_mapping(raw_header: str, canonical_field: Optional[str],
                  confidence: str, reasoning: str) -> None:
    """Store a LLM mapping result in the persistent cache."""
    cache = _load_cache()
    key = hashlib.md5(raw_header.strip().lower().encode()).hexdigest()
    cache[key] = {
        "raw_header": raw_header,
        "canonical_field": canonical_field,
        "confidence": confidence,
        "reasoning": reasoning,
        "cached_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_cache(cache)


def llm_map_with_cache(
    unmapped_headers: list[str],
    file_context: Optional[dict] = None,
) -> list[SchemaMapping]:
    """
    LLM map with persistent caching. Checks cache first, only calls LLM
    for uncached headers. Merges cached results with fresh LLM results.
    """
    cache = _load_cache()
    need_llm: list[str] = []
    results: list[SchemaMapping] = []

    for header in unmapped_headers:
        key = hashlib.md5(header.strip().lower().encode()).hexdigest()
        entry = cache.get(key)
        if entry:
            results.append(SchemaMapping(
                raw_header=header,
                canonical_field=entry.get("canonical_field"),
                confidence="cached",
                reasoning=f"Cached from previous run: {entry.get('reasoning', '')}",
                mapped_by="llm_cached",
                needs_review=False,
            ))
        else:
            need_llm.append(header)

    if need_llm:
        fresh = llm_map_headers(need_llm, file_context)
        for mapping in fresh:
            # Cache it
            cache_mapping(
                mapping.raw_header,
                mapping.canonical_field,
                mapping.confidence,
                mapping.reasoning,
            )
            results.append(mapping)

    return results


# ── Convenience wrapper ───────────────────────────────────────────────────────

def enrich_with_llm(unmapped_headers: list[str], file_context: dict) -> dict[str, str]:
    """
    High-level wrapper: returns {raw_header: canonical_field} dict
    for all unmapped headers, using cache + LLM as needed.
    """
    mappings = llm_map_with_cache(unmapped_headers, file_context)
    return {
        m.raw_header: m.canonical_field
        for m in mappings
        if m.canonical_field is not None
    }