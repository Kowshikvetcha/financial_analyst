"""
Canonical field registry for Phase 1.

Every metric the system understands lives here. Raw column names from uploaded
files are mapped TO these canonical fields by the schema mapper. DuckDB always
stores canonical field names — never raw headers.

Mapping strategy (tried in order):
  1. Exact match on lowercased stripped key
  2. Parenthetical stripped match: "ARR ($000s)" → "arr"
  3. Substring/partial match on canonical field name
  4. Keyword extraction match (words without common stopwords)
  5. Ledger format detection (vertical-block P&L style)

Adding a new metric = add a CanonicalField entry + extend ALIAS_MAP.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import re


class MetricCategory(str, Enum):
    REVENUE = "revenue"
    COST = "cost"
    PROFITABILITY = "profitability"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"
    OPERATIONAL = "operational"


class UnitType(str, Enum):
    CURRENCY = "currency"          # monetary values (INR, USD, etc.)
    PERCENTAGE = "percentage"      # ratios expressed as %
    COUNT = "count"                # units, headcount, orders
    RATIO = "ratio"                # pure ratios (not %)


@dataclass
class CanonicalField:
    name: str                          # snake_case canonical name
    display_name: str                  # human-readable label
    category: MetricCategory
    unit_type: UnitType
    description: str
    is_derived: bool = False           # True = calculated from other fields, not raw
    derived_from: list[str] = field(default_factory=list)  # source field names
    # Keywords for fuzzy matching - words that strongly indicate this field
    keywords: list[str] = field(default_factory=list)


# ── Master registry ──────────────────────────────────────────────────────────

CANONICAL_FIELDS: dict[str, CanonicalField] = {

    # Revenue
    "revenue_gross": CanonicalField(
        name="revenue_gross",
        display_name="Gross Revenue",
        category=MetricCategory.REVENUE,
        unit_type=UnitType.CURRENCY,
        description="Total revenue before any deductions (returns, discounts, GST)",
        keywords=["gross", "total revenue", "sale of products", "sales"],
    ),
    "revenue_net": CanonicalField(
        name="revenue_net",
        display_name="Net Revenue",
        category=MetricCategory.REVENUE,
        unit_type=UnitType.CURRENCY,
        description="Revenue after deductions. Synonyms: Turnover (net of GST), Net Sales",
        keywords=["net", "revenue", "turnover", "sales", "operations"],
    ),
    "gmv": CanonicalField(
        name="gmv",
        display_name="Gross Merchandise Value",
        category=MetricCategory.REVENUE,
        unit_type=UnitType.CURRENCY,
        description="Total value of goods sold through platform before returns/discounts",
        keywords=["gmv", "merchandise", "gross merchandise"],
    ),
    "arr": CanonicalField(
        name="arr",
        display_name="Annual Recurring Revenue",
        category=MetricCategory.REVENUE,
        unit_type=UnitType.CURRENCY,
        description="Annualised value of recurring subscription contracts",
        keywords=["arr", "recurring", "annual", "subscription"],
    ),

    # Costs
    "cogs": CanonicalField(
        name="cogs",
        display_name="Cost of Goods Sold",
        category=MetricCategory.COST,
        unit_type=UnitType.CURRENCY,
        description="Direct cost of producing goods sold. Synonyms: Cost of Revenue, Material Cost",
        keywords=["cogs", "cost of goods", "cost of revenue", "cost of sales", "material", "raw"],
    ),
    "opex": CanonicalField(
        name="opex",
        display_name="Operating Expenses",
        category=MetricCategory.COST,
        unit_type=UnitType.CURRENCY,
        description="All operating costs excluding COGS (salaries, rent, marketing, etc.)",
        keywords=["opex", "operating", "expenses", "overhead", "indirect"],
    ),
    "salary_expense": CanonicalField(
        name="salary_expense",
        display_name="Salary & Employee Benefits",
        category=MetricCategory.COST,
        unit_type=UnitType.CURRENCY,
        description="Total personnel costs including salaries, bonuses, PF, ESIC",
        keywords=["salary", "wages", "employee", "personnel", "labour", "labor", "staff"],
    ),
    "marketing_expense": CanonicalField(
        name="marketing_expense",
        display_name="Marketing & Advertising",
        category=MetricCategory.COST,
        unit_type=UnitType.CURRENCY,
        description="Spend on ads, promotions, influencer, performance marketing",
        keywords=["marketing", "advertising", "ads", "promotion", "sales and marketing"],
    ),
    "rent_expense": CanonicalField(
        name="rent_expense",
        display_name="Rent & Facilities",
        category=MetricCategory.COST,
        unit_type=UnitType.CURRENCY,
        description="Office/warehouse rent, utilities, maintenance",
        keywords=["rent", "facilities", "utilities", "office"],
    ),

    # Profitability
    "gross_profit": CanonicalField(
        name="gross_profit",
        display_name="Gross Profit",
        category=MetricCategory.PROFITABILITY,
        unit_type=UnitType.CURRENCY,
        description="Revenue Net minus COGS",
        is_derived=True,
        derived_from=["revenue_net", "cogs"],
        keywords=["gross", "profit", "gp"],
    ),
    "gross_margin_pct": CanonicalField(
        name="gross_margin_pct",
        display_name="Gross Margin %",
        category=MetricCategory.PROFITABILITY,
        unit_type=UnitType.PERCENTAGE,
        description="Gross Profit / Revenue Net × 100",
        is_derived=True,
        derived_from=["gross_profit", "revenue_net"],
        keywords=["gross margin", "margin"],
    ),
    "ebitda": CanonicalField(
        name="ebitda",
        display_name="EBITDA",
        category=MetricCategory.PROFITABILITY,
        unit_type=UnitType.CURRENCY,
        description="Earnings before Interest, Tax, Depreciation, Amortisation",
        keywords=["ebitda", "operating profit", "operating income"],
    ),
    "ebitda_margin_pct": CanonicalField(
        name="ebitda_margin_pct",
        display_name="EBITDA Margin %",
        category=MetricCategory.PROFITABILITY,
        unit_type=UnitType.PERCENTAGE,
        description="EBITDA / Revenue Net × 100",
        is_derived=True,
        derived_from=["ebitda", "revenue_net"],
        keywords=["ebitda margin", "margin"],
    ),
    "pat": CanonicalField(
        name="pat",
        display_name="Profit After Tax",
        category=MetricCategory.PROFITABILITY,
        unit_type=UnitType.CURRENCY,
        description="Net profit after all deductions including tax. Synonyms: Net Profit, Net Income",
        keywords=["pat", "net profit", "profit after tax", "net income"],
    ),
    "pat_margin_pct": CanonicalField(
        name="pat_margin_pct",
        display_name="PAT Margin %",
        category=MetricCategory.PROFITABILITY,
        unit_type=UnitType.PERCENTAGE,
        description="PAT / Revenue Net × 100",
        is_derived=True,
        derived_from=["pat", "revenue_net"],
        keywords=["pat margin", "net margin", "margin"],
    ),

    # Balance Sheet
    "accounts_receivable": CanonicalField(
        name="accounts_receivable",
        display_name="Accounts Receivable",
        category=MetricCategory.BALANCE_SHEET,
        unit_type=UnitType.CURRENCY,
        description="Amounts owed by customers. Synonyms: Trade Receivables, Debtors",
        keywords=["receivable", "debtor", "trade receivable", "sundry debtor"],
    ),
    "accounts_payable": CanonicalField(
        name="accounts_payable",
        display_name="Accounts Payable",
        category=MetricCategory.BALANCE_SHEET,
        unit_type=UnitType.CURRENCY,
        description="Amounts owed to suppliers. Synonyms: Trade Payables, Creditors",
        keywords=["payable", "creditor", "trade payable", "sundry creditor"],
    ),
    "inventory": CanonicalField(
        name="inventory",
        display_name="Inventory",
        category=MetricCategory.BALANCE_SHEET,
        unit_type=UnitType.CURRENCY,
        description="Stock of goods held for sale or production",
        keywords=["inventory", "stock", "closing stock"],
    ),
    "cash_and_equivalents": CanonicalField(
        name="cash_and_equivalents",
        display_name="Cash & Equivalents",
        category=MetricCategory.BALANCE_SHEET,
        unit_type=UnitType.CURRENCY,
        description="Cash on hand plus short-term liquid investments",
        keywords=["cash", "bank", "cash equivalent"],
    ),
    "total_debt": CanonicalField(
        name="total_debt",
        display_name="Total Debt",
        category=MetricCategory.BALANCE_SHEET,
        unit_type=UnitType.CURRENCY,
        description="All interest-bearing borrowings (short + long term)",
        keywords=["debt", "borrowing", "loan", "borrowings"],
    ),

    # Operational
    "headcount": CanonicalField(
        name="headcount",
        display_name="Headcount",
        category=MetricCategory.OPERATIONAL,
        unit_type=UnitType.COUNT,
        description="Total number of employees (FTE)",
        keywords=["headcount", "employee", "staff", "fte", "people"],
    ),
    "customer_count": CanonicalField(
        name="customer_count",
        display_name="Customer Count",
        category=MetricCategory.OPERATIONAL,
        unit_type=UnitType.COUNT,
        description="Number of active customers",
        keywords=["customer", "logo", "active"],
    ),
    "order_count": CanonicalField(
        name="order_count",
        display_name="Order Count",
        category=MetricCategory.OPERATIONAL,
        unit_type=UnitType.COUNT,
        description="Number of orders processed",
        keywords=["order", "orders"],
    ),

    # Revenue (new)
    "returns_refunds": CanonicalField(
        name="returns_refunds", display_name="Returns & Refunds",
        category=MetricCategory.REVENUE, unit_type=UnitType.CURRENCY,
        description="Returns, refunds, and cancellations deducted from gross revenue",
        keywords=["return", "refund"],
    ),
    "subscription_revenue": CanonicalField(
        name="subscription_revenue", display_name="Subscription Revenue",
        category=MetricCategory.REVENUE, unit_type=UnitType.CURRENCY,
        description="Recurring subscription / SaaS revenue",
        keywords=["subscription", "recurring", "saas"],
    ),
    "professional_services_revenue": CanonicalField(
        name="professional_services_revenue", display_name="Professional Services Revenue",
        category=MetricCategory.REVENUE, unit_type=UnitType.CURRENCY,
        description="Implementation, consulting, and services revenue",
        keywords=["professional", "services", "consulting"],
    ),
    "new_arr": CanonicalField(
        name="new_arr", display_name="New ARR",
        category=MetricCategory.REVENUE, unit_type=UnitType.CURRENCY,
        description="New annual recurring revenue added in the period",
        keywords=["new arr", "new booking"],
    ),
    "churned_arr": CanonicalField(
        name="churned_arr", display_name="Churned ARR",
        category=MetricCategory.REVENUE, unit_type=UnitType.CURRENCY,
        description="ARR lost due to cancellations and downgrades",
        keywords=["churn", "churned arr"],
    ),

    # Cost (new)
    "platform_fees": CanonicalField(
        name="platform_fees", display_name="Platform / Marketplace Fees",
        category=MetricCategory.COST, unit_type=UnitType.CURRENCY,
        description="Fees paid to marketplaces (Amazon, Flipkart, etc.)",
        keywords=["platform", "marketplace", "commission", "fees"],
    ),
    "logistics_expense": CanonicalField(
        name="logistics_expense", display_name="Logistics & Shipping",
        category=MetricCategory.COST, unit_type=UnitType.CURRENCY,
        description="Freight, shipping, and delivery costs",
        keywords=["logistics", "freight", "shipping", "delivery"],
    ),
    "packaging_expense": CanonicalField(
        name="packaging_expense", display_name="Packaging",
        category=MetricCategory.COST, unit_type=UnitType.CURRENCY,
        description="Packaging materials cost",
        keywords=["packaging", "pack"],
    ),
    "rd_expense": CanonicalField(
        name="rd_expense", display_name="R&D Expense",
        category=MetricCategory.COST, unit_type=UnitType.CURRENCY,
        description="Research and development spend",
        keywords=["r&d", "research", "development", "product development"],
    ),

    # Operational (new)
    "net_dollar_retention": CanonicalField(
        name="net_dollar_retention", display_name="Net Dollar Retention %",
        category=MetricCategory.OPERATIONAL, unit_type=UnitType.PERCENTAGE,
        description="Revenue retained plus expansion from prior-period cohort",
        keywords=["net dollar retention", "ndr", "net retention"],
    ),
    "gross_dollar_retention": CanonicalField(
        name="gross_dollar_retention", display_name="Gross Dollar Retention %",
        category=MetricCategory.OPERATIONAL, unit_type=UnitType.PERCENTAGE,
        description="Revenue retained (excluding expansion) from prior-period cohort",
        keywords=["gross dollar retention", "gross retention"],
    ),
    "logo_churn_count": CanonicalField(
        name="logo_churn_count", display_name="Logo Churn Count",
        category=MetricCategory.OPERATIONAL, unit_type=UnitType.COUNT,
        description="Number of customers lost in the period",
        keywords=["logo churn", "customer churn"],
    ),
}


# ── Alias map: raw header variants → canonical field name ────────────────────
# Keys are lowercased + stripped. Add new variants as you discover them.

ALIAS_MAP: dict[str, str] = {
    # revenue_gross
    "gross revenue": "revenue_gross",
    "total revenue": "revenue_gross",
    "sale of products": "revenue_gross",
    "sales": "revenue_gross",
    "turnover (gross)": "revenue_gross",
    "sale of products (domestic)": "revenue_gross",
    "sale of products (export)": "revenue_gross",

    # revenue_net
    "net revenue": "revenue_net",
    "net sales": "revenue_net",
    "turnover": "revenue_net",
    "turnover (net of gst)": "revenue_net",
    "revenue (net)": "revenue_net",
    "revenue net of gst": "revenue_net",
    "total revenue from operations": "revenue_net",
    "revenue from operations": "revenue_net",
    "net turnover": "revenue_net",
    "job work charges recd": "revenue_net",
    "total sales": "revenue_net",

    # gmv
    "gmv": "gmv",
    "gross merchandise value": "gmv",
    "platform gmv": "gmv",

    # arr
    "arr": "arr",
    "annual recurring revenue": "arr",
    "annualised recurring revenue": "arr",
    "arr ($000s)": "arr",
    "arr (end of period)": "arr",
    "arr eop": "arr",
    "end of period arr": "arr",

    # returns_refunds
    "returns/refunds": "returns_refunds",
    "returns & refunds": "returns_refunds",
    "returns and refunds": "returns_refunds",
    "refunds": "returns_refunds",

    # subscription_revenue
    "subscription revenue": "subscription_revenue",
    "recurring revenue": "subscription_revenue",
    "saas revenue": "subscription_revenue",

    # professional_services_revenue
    "professional services": "professional_services_revenue",
    "professional services revenue": "professional_services_revenue",
    "services revenue": "professional_services_revenue",

    # new_arr
    "new arr": "new_arr",
    "new arr ($000s)": "new_arr",
    "new bookings": "new_arr",

    # churned_arr
    "churned arr": "churned_arr",
    "churned arr ($000s)": "churned_arr",
    "churn arr": "churned_arr",

    # cogs
    "cogs": "cogs",
    "cost of goods sold": "cogs",
    "cost of revenue": "cogs",
    "cost of sales": "cogs",
    "material cost": "cogs",
    "raw material consumed": "cogs",
    "direct costs": "cogs",
    "cost of materials & sub-contract": "cogs",
    "cost of materials and sub-contract": "cogs",
    "cost of materials": "cogs",
    "direct material cost": "cogs",
    "purchases": "cogs",
    "hosting & infrastructure": "cogs",
    "hosting and infrastructure": "cogs",

    # opex
    "opex": "opex",
    "operating expenses": "opex",
    "total operating expenses": "opex",
    "operating costs": "opex",
    "indirect expenses": "opex",
    "selling & admin overheads": "opex",
    "selling and admin overheads": "opex",
    "selling general and administrative": "opex",
    "sg&a": "opex",
    "general & admin expenses": "opex",
    "manufacturing overheads": "opex",
    "other expenses": "opex",
    "other operating expenses": "opex",
    "administrative expenses": "opex",

    # salary_expense
    "salary": "salary_expense",
    "salaries": "salary_expense",
    "employee benefits": "salary_expense",
    "staff costs": "salary_expense",
    "personnel costs": "salary_expense",
    "salaries and wages": "salary_expense",
    "employee cost": "salary_expense",
    "direct labour": "salary_expense",
    "direct labor": "salary_expense",
    "labour charges": "salary_expense",
    "wages & labour": "salary_expense",
    "wages and labour": "salary_expense",
    "employee benefits expense": "salary_expense",
    "manpower cost": "salary_expense",
    "wages": "salary_expense",

    # marketing_expense
    "marketing": "marketing_expense",
    "advertising": "marketing_expense",
    "marketing and advertising": "marketing_expense",
    "ad spend": "marketing_expense",
    "performance marketing": "marketing_expense",
    "marketing spend": "marketing_expense",
    "marketing & advertising": "marketing_expense",
    "sales & marketing": "marketing_expense",
    "sales and marketing": "marketing_expense",
    "sales promotion & marketing": "marketing_expense",

    # platform_fees
    "amazon/flipkart fees": "platform_fees",
    "marketplace fees": "platform_fees",
    "platform commission": "platform_fees",

    # logistics_expense
    "logistics": "logistics_expense",
    "freight outward": "logistics_expense",
    "freight": "logistics_expense",
    "shipping costs": "logistics_expense",

    # packaging_expense
    "packaging": "packaging_expense",
    "packaging material": "packaging_expense",

    # rent_expense
    "rent": "rent_expense",
    "rent and utilities": "rent_expense",
    "office rent": "rent_expense",
    "facilities": "rent_expense",
    "rent + utilities": "rent_expense",
    "rent (factory + office)": "rent_expense",
    "factory rent": "rent_expense",

    # rd_expense
    "r&d": "rd_expense",
    "research and development": "rd_expense",
    "r&d expense": "rd_expense",
    "product development": "rd_expense",

    # gross_profit
    "gross profit": "gross_profit",
    "gp": "gross_profit",
    "gross margin": "gross_profit",
    "gross profit / (loss)": "gross_profit",

    # ebitda
    "ebitda": "ebitda",
    "ebitda (after non-recurring)": "ebitda",
    "operating profit": "ebitda",
    "operating income": "ebitda",

    # pat
    "pat": "pat",
    "net profit": "pat",
    "profit after tax": "pat",
    "net income": "pat",
    "profit/(loss) after tax": "pat",
    "net profit / (loss)": "pat",
    "profit / (loss) after tax": "pat",
    "profit after tax (pat)": "pat",

    # balance sheet
    "accounts receivable": "accounts_receivable",
    "trade receivables": "accounts_receivable",
    "debtors": "accounts_receivable",
    "sundry debtors": "accounts_receivable",
    "trade receivables (net)": "accounts_receivable",
    "receivables": "accounts_receivable",
    "book debts": "accounts_receivable",
    "trade debtors": "accounts_receivable",
    "accounts payable": "accounts_payable",
    "trade payables": "accounts_payable",
    "creditors": "accounts_payable",
    "sundry creditors": "accounts_payable",
    "inventory": "inventory",
    "stock": "inventory",
    "closing stock": "inventory",
    "cash": "cash_and_equivalents",
    "cash and cash equivalents": "cash_and_equivalents",
    "cash and bank": "cash_and_equivalents",
    "total debt": "total_debt",
    "borrowings": "total_debt",
    "total borrowings": "total_debt",
    "loans": "total_debt",
    "long term borrowings": "total_debt",
    "short term borrowings": "total_debt",
    "bank borrowings": "total_debt",
    "term loans": "total_debt",

    # net_dollar_retention
    "net dollar retention %": "net_dollar_retention",
    "net dollar retention": "net_dollar_retention",
    "ndr": "net_dollar_retention",
    "net revenue retention": "net_dollar_retention",
    "net revenue retention %": "net_dollar_retention",

    # gross_dollar_retention
    "gross $ retention %": "gross_dollar_retention",
    "gross dollar retention %": "gross_dollar_retention",
    "gross retention": "gross_dollar_retention",

    # operational
    "headcount": "headcount",
    "employees": "headcount",
    "total employees": "headcount",
    "fte": "headcount",
    "total headcount": "headcount",
    "number of employees": "headcount",
    "employee count": "headcount",
    "total staff": "headcount",
    "customers": "customer_count",
    "active customers": "customer_count",
    "logo count (eop)": "customer_count",
    "logo count": "customer_count",
    "number of customers": "customer_count",
    "active customer count": "customer_count",
    "logos": "customer_count",
    "customer count": "customer_count",
    "orders": "order_count",
    "total orders": "order_count",
    "number of orders": "order_count",

    # logo_churn_count
    "logo churn (count)": "logo_churn_count",
    "logo churn": "logo_churn_count",
    "customer churn count": "logo_churn_count",
}


def resolve_alias(raw_header: str) -> Optional[str]:
    """Return canonical field name for a raw header, or None if unknown."""
    import re as _re
    key = raw_header.strip().lower()
    if key in ALIAS_MAP:
        return ALIAS_MAP[key]
    # Try stripping parenthetical qualifiers: "ARR ($000s)" → "arr"
    key_no_paren = _re.sub(r'\s*\(.*?\)\s*', ' ', key).strip()
    if key_no_paren and key_no_paren in ALIAS_MAP:
        return ALIAS_MAP[key_no_paren]
    # Try fuzzy matching via keywords
    fuzzy_match = _fuzzy_match(raw_header)
    if fuzzy_match:
        return fuzzy_match
    return None


# ── Fuzzy matching via keyword extraction ───────────────────────────────────

# Stopwords to strip from headers before keyword matching
_STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
    'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
    'it', 'its', 'this', 'that', 'these', 'those', 'amount', 'value',
    'total', 'items', 'rs', 'inr', 'usd', 'currency', 'figures',
}

# Common accounting modifiers that don't change meaning
_MODIFIERS = {
    'current', 'previous', 'prior', 'last', 'first', 'opening', 'closing',
    'net', 'gross', 'total', 'sub', 'annual', 'quarterly', 'monthly',
    'actual', 'estimated', 'budget', 'forecast', 'projected', 'audited',
}


def _tokenize(header: str) -> set[str]:
    """Extract meaningful keywords from a header, stripping stopwords and modifiers."""
    import re as _re
    # Strip parenthetical content
    text = _re.sub(r'\(.*?\)', ' ', header.lower())
    # Extract alphanumeric tokens
    tokens = set(_re.findall(r'\b[a-z]+\b', text))
    # Remove stopwords and modifiers
    return tokens - _STOPWORDS - _MODIFIERS


def _fuzzy_match(raw_header: str) -> Optional[str]:
    """
    Match a raw header to a canonical field using keyword extraction.
    Returns canonical field name or None if no confident match found.

    Scoring:
      - 3+ keyword matches with canonical field keywords → high confidence
      - 2 keyword matches → medium confidence
      - 1 keyword match → low confidence (only used if header is short)
    """
    header_tokens = _tokenize(raw_header)
    if not header_tokens:
        return None

    best_match: Optional[str] = None
    best_score = 0

    for field_name, field_def in CANONICAL_FIELDS.items():
        field_tokens = set(k.lower() for k in field_def.keywords)
        if not field_tokens:
            # Fall back to matching against field name and display name
            field_tokens = {field_name.replace('_', ' '), field_def.display_name.lower()}

        # Calculate overlap
        overlap = header_tokens & field_tokens
        score = len(overlap)

        # Boost score if all header tokens match
        if header_tokens.issubset(field_tokens):
            score += 1

        # Prefer matches where keywords are complete words (not substrings)
        for tok in overlap:
            if tok in field_def.display_name.lower() or tok in field_name:
                score += 0.5

        if score > best_score:
            # Only accept low-confidence match if header is short (< 5 words)
            if score >= 2 or (score >= 1 and len(header_tokens) <= 4):
                best_score = score
                best_match = field_name

    return best_match


# ── Ledger format detection ─────────────────────────────────────────────────

LEDGER_INDICATORS = [
    'particulars', 'description', 'narration', 'account',
    'debit', 'credit', 'dr', 'cr',
    'voucher', 'ledger', 'posting', 'balance',
]

LEDGER_METRIC_PATTERNS = {
    'revenue_net': ['sales', 'revenue', 'income', 'receipt', 'subscription'],
    'cogs': ['purchase', 'cost', 'material', 'goods'],
    'salary_expense': ['salary', 'wage', 'employee', 'personnel'],
    'marketing_expense': ['marketing', 'advertisement', 'promotion'],
    'rent_expense': ['rent', 'lease', 'hiring'],
    'opex': ['expense', 'overhead', 'sundry'],
}


def detect_ledger_format(df, headers: list[str]) -> bool:
    """
    Detect if a dataframe represents a ledger-style P&L (vertical-block format).
    Common in Tally exports and accounting software.

    Returns True if this looks like a ledger format that needs special handling.
    """
    # Check first column for ledger indicators
    if not headers:
        return False

    first_col_lower = headers[0].lower()

    # Check for common ledger column names
    for indicator in LEDGER_INDICATORS:
        if indicator in first_col_lower:
            return True

    # Check if it looks like a chart of accounts listing
    # Ledger format often has narrow first column (descriptions) and multiple
    # value columns without clear period headers
    if len(headers) <= 3:
        return False  # Too few columns for a period-based layout

    # If first column has lots of text rows and numeric columns are small,
    # it's likely ledger format
    return False  # Default to not ledger


def extract_ledger_facts(
    df,
    file_id: int,
    entity_id: int,
    unit_spec_str: str,
) -> list[dict]:
    """
    Extract facts from a ledger-format dataframe (vertical-block P&L).
    This handles Tally double-column layout and chart-of-accounts exports.

    Returns list of fact dicts for staging.
    """
    from units import detect_unit, normalise
    from periods import parse_period

    facts = []
    metric_col = df.columns[0]
    unit_spec = detect_unit(unit_spec_str)

    for row in df.iter_rows(named=True):
        raw_label = str(row.get(metric_col, "") or "").strip()
        if not raw_label:
            continue

        # Skip section headers and totals
        if raw_label.startswith('---') or raw_label.startswith('==='):
            continue
        if re.match(r'^\s*(total|grand total|subtotal|balance)\s*$', raw_label, re.I):
            continue

        # Try to match label to canonical field
        canonical = resolve_alias(raw_label)
        if not canonical:
            continue

        # For each remaining column, try to extract value
        for col in df.columns[1:]:
            raw_val = row.get(col)
            if raw_val is None or str(raw_val).strip() in ("", "-", "—", "N/A", "na"):
                continue

            # Try to parse as numeric
            try:
                # Handle accounting negatives (124.50) → -124.50
                val_str = str(raw_val).replace(",", "").strip()
                if val_str.startswith('(') and val_str.endswith(')'):
                    val_str = '-' + val_str[1:-1]
                numeric_val = float(val_str)
            except ValueError:
                # Try to extract period from column header if it's text
                period_spec = parse_period(str(col))
                if not period_spec:
                    continue
                # Column header is a period, row is a metric
                try:
                    val_str = str(raw_val).replace(",", "").strip()
                    if val_str.startswith('(') and val_str.endswith(')'):
                        val_str = '-' + val_str[1:-1]
                    numeric_val = float(val_str)
                except ValueError:
                    continue

            nv = normalise(numeric_val, unit_spec_str)
            facts.append({
                "file_id": file_id,
                "entity_id": entity_id,
                "canonical_field": canonical,
                "period": "FY24",  # Default to FY24, should be enhanced with actual period detection
                "value_normalised": nv.value_normalised,
                "currency": nv.currency,
                "original_unit": nv.original_unit,
                "conversion_factor": nv.conversion_factor,
                "conversion_applied": nv.conversion_applied,
                "raw_value": numeric_val,
                "raw_header": raw_label,
                "row_context": raw_label,
                "cell_reference": None,  # Will be filled by caller
                "source_sheet": None,
            })

    return facts
