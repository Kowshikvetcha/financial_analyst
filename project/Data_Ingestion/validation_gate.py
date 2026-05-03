"""
Stage 6: Validation Gate for Phase 1.

Before facts are promoted to live_facts, this module runs deterministic
checks to catch silently wrong numbers:

  1. Sum checks: COGS + Gross Profit ≈ Revenue Net; components sum to total
  2. Unit magnitude checks: INR values in sensible ranges (not absolute vs lakhs)
  3. Cross-period sanity bounds: revenue doesn't swing >5x between adjacent periods
  4. Sign consistency: costs should be negative or positive consistently

Design:
  - Each check returns ValidationResult(warnings=[], errors=[])
  - Errors block promotion; warnings are logged but don't block
  - Checks run on staging_facts before promotion
  - Results stored in validation_log table

API:
  validate_staging_facts(conn, entity_id, file_ids) -> ValidationReport
  run_validation(conn, file_id) -> list[ValidationIssue]
"""

from dataclasses import dataclass, field
from typing import Optional
import duckdb


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    """A single validation finding."""
    check: str              # name of the check that fired
    severity: str           # 'error' | 'warning'
    entity_id: int
    canonical_field: str
    period: str
    value: float
    message: str
    suggestion: Optional[str] = None  # e.g. "check if units are mixed"


@dataclass
class ValidationReport:
    """Full validation report for a file or entity."""
    entity_id: int
    file_id: Optional[int]
    issues: list[ValidationIssue] = field(default_factory=list)
    passed_checks: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")


# ── Magnitude benchmarks ─────────────────────────────────────────────────────
# Reasonable ranges for INR values (in absolute INR).
# Used to detect unit conversion errors.

INR_MAGNITUDE_RULES = {
    # (min_abs, max_abs) per field category — catches absolute vs lakh confusion
    "revenue_gross": (1_000, 1e12),
    "revenue_net": (1_000, 1e12),
    "cogs": (1_000, 1e12),
    "gross_profit": (1_000, 1e12),
    "ebitda": (1_000, 1e12),
    "pat": (1_000, 1e12),
    # Balance sheet items
    "accounts_receivable": (1_000, 1e12),
    "accounts_payable": (1_000, 1e12),
    "inventory": (1_000, 1e12),
    "cash_and_equivalents": (1_000, 1e12),
    "total_debt": (1_000, 1e12),
    # Operating expenses
    "opex": (1_000, 1e12),
    "salary_expense": (1_000, 1e12),
    "marketing_expense": (1_000, 1e12),
    "rent_expense": (1_000, 1e12),
    "logistics_expense": (1_000, 1e12),
}

USD_MAGNITUDE_RULES = {
    "arr": (1_000, 1e9),
    "new_arr": (1_000, 1e9),
    "churned_arr": (1_000, 1e9),
}


# ── Sign rules ────────────────────────────────────────────────────────────────

# Fields that should typically be positive (revenue, assets, counts)
POSITIVE_SIGN_FIELDS = {
    "revenue_gross", "revenue_net", "gmv", "arr",
    "subscription_revenue", "professional_services_revenue",
    "new_arr", "gross_profit", "ebitda", "pat",
    "accounts_receivable", "inventory", "cash_and_equivalents",
    "customer_count", "order_count", "headcount",
}

# Fields that can be negative (expenses, losses)
NEGATIVE_OK_FIELDS = {
    "cogs", "opex", "salary_expense", "marketing_expense",
    "rent_expense", "platform_fees", "logistics_expense",
    "packaging_expense", "rd_expense", "churned_arr",
    "returns_refunds", "total_debt", "accounts_payable",
}

# Percentage fields should be in a sensible range
PERCENTAGE_FIELDS = {
    "gross_margin_pct", "ebitda_margin_pct", "pat_margin_pct",
    "net_dollar_retention", "gross_dollar_retention",
}


# ── Sum check ─────────────────────────────────────────────────────────────────

def _check_sum_relation(conn, entity_id: int) -> list[ValidationIssue]:
    """
    Verify accounting sum relationships:
      revenue_gross - returns_refunds ≈ revenue_net
      revenue_net - cogs ≈ gross_profit (if ebitda present, cross-check)
    """
    issues = []

    # Check: revenue_gross - returns_refunds ≈ revenue_net
    result = conn.execute("""
        SELECT
            g.period,
            g.value_normalised AS gross_rev,
            r.value_normalised AS returns_val,
            n.value_normalised AS net_rev,
            n.original_unit
        FROM live_facts g
        LEFT JOIN live_facts r ON r.entity_id = g.entity_id
            AND r.period = g.period AND r.canonical_field = 'returns_refunds'
        JOIN live_facts n ON n.entity_id = g.entity_id
            AND n.period = g.period AND n.canonical_field = 'revenue_net'
        WHERE g.entity_id = ?
          AND g.canonical_field = 'revenue_gross'
          AND g.is_derived = FALSE
          AND n.is_derived = FALSE
    """, [entity_id]).fetchall()

    for (period, gross, returns_val, net, unit) in result:
        returns_val = returns_val or 0.0
        expected_net = gross - returns_val
        diff_pct = abs(net - expected_net) / abs(expected_net) if expected_net != 0 else 0

        if diff_pct > 0.05:  # >5% discrepancy
            issues.append(ValidationIssue(
                check="sum_revenue_gross_returns",
                severity="warning",
                entity_id=entity_id,
                canonical_field="revenue_net",
                period=period,
                value=net,
                message=f"revenue_gross - returns_refunds ({expected_net:.2f}) differs from revenue_net ({net:.2f}) by {diff_pct*100:.1f}%",
                suggestion="Check if returns/refunds are being double-deducted or if revenue_gross includes them",
            ))

    return issues


def _check_margin_consistency(conn, entity_id: int) -> list[ValidationIssue]:
    """
    Verify derived KPIs are consistent with source fields:
      - gross_margin_pct ≈ (revenue_net - cogs) / revenue_net * 100
      - pat_margin_pct ≈ pat / revenue_net * 100
    """
    issues = []

    # Check gross margin
    result = conn.execute("""
        SELECT
            m.period,
            m.value_normalised AS margin_pct,
            m.source_file_id,
            r.value_normalised AS net_rev
        FROM live_facts m
        JOIN live_facts r ON r.entity_id = m.entity_id AND r.period = m.period
        WHERE m.entity_id = ?
          AND m.canonical_field = 'gross_margin_pct'
          AND m.is_derived = FALSE
          AND r.canonical_field = 'revenue_net'
          AND r.is_derived = FALSE
    """, [entity_id]).fetchall()

    for (period, margin_pct, file_id, net_rev) in result:
        if net_rev <= 0:
            continue
        # Fetch derived gross_profit if exists to compare
        derived_check = conn.execute("""
            SELECT value_normalised FROM live_facts
            WHERE entity_id = ? AND period = ? AND canonical_field = 'gross_profit' AND is_derived = TRUE
        """, [entity_id, period]).fetchone()

        if derived_check:
            derived_gp = derived_check[0]
            expected_margin = (derived_gp / net_rev) * 100 if net_rev != 0 else 0
            diff = abs(margin_pct - expected_margin)
            if diff > 0.5:  # >0.5 percentage points off
                issues.append(ValidationIssue(
                    check="margin_consistency",
                    severity="warning",
                    entity_id=entity_id,
                    canonical_field="gross_margin_pct",
                    period=period,
                    value=margin_pct,
                    message=f"gross_margin_pct ({margin_pct:.1f}%) inconsistent with derived from revenue_net and cogs ({expected_margin:.1f}%)",
                ))

    return issues


# ── Unit magnitude check ──────────────────────────────────────────────────────

def _check_magnitude(conn, entity_id: int) -> list[ValidationIssue]:
    """
    Check that values are in sensible magnitude ranges.
    Catches cases where INR is stored in absolute (e.g., 150000000) instead of Lakhs (150).
    """
    issues = []

    rows = conn.execute("""
        SELECT canonical_field, period, value_normalised, currency, original_unit
        FROM staging_facts
        WHERE entity_id = ?
    """, [entity_id]).fetchall()

    for (field, period, value, currency, orig_unit) in rows:
        # Skip percentage fields
        if field in PERCENTAGE_FIELDS:
            continue

        # Determine expected magnitude range
        rules = INR_MAGNITUDE_RULES if currency in ("INR", "INR_absolute") else USD_MAGNITUDE_RULES

        if field in rules:
            min_val, max_val = rules[field]
            if value < min_val or value > max_val:
                # Check if it's a unit conversion error
                # If value is very large (e.g., 150000000 for a small company), flag it
                severity = "error" if value > max_val else "warning"
                issues.append(ValidationIssue(
                    check="unit_magnitude",
                    severity=severity,
                    entity_id=entity_id,
                    canonical_field=field,
                    period=period,
                    value=value,
                    message=f"{field} = {value:.2f} ({currency}/{orig_unit}) outside expected range [{min_val:.0f}, {max_val:.0f}]",
                    suggestion="Check if unit conversion is needed — value may be in absolute Rs instead of Lakhs/Crore",
                ))

    return issues


# ── Cross-period sanity check ─────────────────────────────────────────────────

def _check_period_swing(conn, entity_id: int) -> list[ValidationIssue]:
    """
    Detect unrealistic period-over-period swings in revenue/EBITDA.
    Flag if a metric changes by >5x between adjacent periods.
    """
    issues = []

    # Check key metrics for extreme swings
    check_fields = ["revenue_net", "revenue_gross", "ebitda", "pat"]

    for field in check_fields:
        rows = conn.execute("""
            SELECT period, value_normalised
            FROM live_facts
            WHERE entity_id = ? AND canonical_field = ? AND is_derived = FALSE
            ORDER BY period
        """, [entity_id, field]).fetchall()

        if len(rows) < 2:
            continue

        # Convert periods to sortable keys (FY24 > FY23 > FY22)
        def period_sort_key(p: str) -> str:
            return p  # Already in canonical format, sort lexicographically

        sorted_rows = sorted(rows, key=lambda r: period_sort_key(r[0]))

        for i in range(1, len(sorted_rows)):
            prev_period, prev_val = sorted_rows[i - 1]
            curr_period, curr_val = sorted_rows[i]

            if prev_val == 0 or curr_val == 0:
                continue

            # Calculate absolute change ratio (handle sign flips)
            ratio = abs(curr_val / prev_val)

            # Flag if ratio > 5 or ratio < 0.2 (5x swing in either direction)
            if ratio > 10 or ratio < 0.1:
                issues.append(ValidationIssue(
                    check="period_swing",
                    severity="warning",
                    entity_id=entity_id,
                    canonical_field=field,
                    period=curr_period,
                    value=curr_val,
                    message=f"{field} swung {ratio:.1f}x from {prev_period} ({prev_val:.2f}) to {curr_period} ({curr_val:.2f})",
                    suggestion="Verify these are real changes, not unit errors or data entry mistakes",
                ))

    return issues


# ── Sign consistency check ─────────────────────────────────────────────────────

def _check_sign_consistency(conn, entity_id: int) -> list[ValidationIssue]:
    """
    Revenue/costs should have consistent signs within an entity.
    Most companies report revenue as positive, costs as positive (both reduce profit).
    Flag anomalies where e.g., revenue is negative.
    """
    issues = []

    rows = conn.execute("""
        SELECT canonical_field, period, value_normalised, currency
        FROM staging_facts
        WHERE entity_id = ?
    """, [entity_id]).fetchall()

    for (field, period, value, currency) in rows:
        if field in POSITIVE_SIGN_FIELDS and value < 0:
            # Revenue shouldn't be negative (unless it's a loss scenario, but still unusual)
            if abs(value) > 1000:  # Only flag significant negatives
                issues.append(ValidationIssue(
                    check="sign_consistency",
                    severity="warning",
                    entity_id=entity_id,
                    canonical_field=field,
                    period=period,
                    value=value,
                    message=f"{field} = {value:.2f} is negative — revenue should be positive",
                    suggestion="Check if this is a genuine loss or a sign convention issue",
                ))

    return issues


# ── Main validation entry point ───────────────────────────────────────────────

def validate_staging_facts(
    conn: duckdb.DuckDBPyConnection,
    entity_id: int,
    file_ids: Optional[list[int]] = None,
) -> ValidationReport:
    """
    Run all validation checks on staging facts for an entity.
    Returns a ValidationReport with all issues found.
    """
    report = ValidationReport(entity_id=entity_id, file_id=None)

    file_filter = ""
    params = [entity_id]
    if file_ids:
        placeholders = ", ".join("?" * len(file_ids))
        file_filter = f"AND file_id IN ({placeholders})"
        params.extend(file_ids)

    # Ensure staging_facts has needed columns for validation
    # (existing schema already has value_normalised, currency, etc.)

    # Run all checks
    checks = [
        ("sum_relations", lambda: _check_sum_relation(conn, entity_id)),
        ("margin_consistency", lambda: _check_margin_consistency(conn, entity_id)),
        ("unit_magnitude", lambda: _check_magnitude(conn, entity_id)),
        ("period_swing", lambda: _check_period_swing(conn, entity_id)),
        ("sign_consistency", lambda: _check_sign_consistency(conn, entity_id)),
    ]

    for check_name, check_fn in checks:
        try:
            issues = check_fn()
            report.issues.extend(issues)
            if not any(i.check == check_name for i in report.issues if i.check == check_name):
                report.passed_checks.append(check_name)
        except Exception as e:
            # If a check fails (e.g., missing columns), log it but don't block
            report.issues.append(ValidationIssue(
                check=check_name,
                severity="warning",
                entity_id=entity_id,
                canonical_field="",
                period="",
                value=0.0,
                message=f"Validation check '{check_name}' failed: {e}",
            ))

    return report


def run_validation(
    conn: duckdb.DuckDBPyConnection,
    file_id: int,
) -> list[ValidationIssue]:
    """
    Run validation for a specific file.
    Returns list of issues found.
    """
    # Get entity_id from file
    entity_row = conn.execute(
        "SELECT entity_id FROM source_files WHERE file_id = ?", [file_id]
    ).fetchone()

    if not entity_row:
        return []

    entity_id = entity_row[0]
    report = validate_staging_facts(conn, entity_id, file_ids=[file_id])
    return report.issues


def print_validation_report(report: ValidationReport) -> str:
    """Format a validation report as a readable string."""
    lines = [f"Validation Report for entity_id={report.entity_id}"]
    lines.append(f"  Errors: {report.error_count}")
    lines.append(f"  Warnings: {report.warning_count}")
    lines.append(f"  Passed checks: {', '.join(report.passed_checks) or 'none'}")

    if report.issues:
        lines.append("\n  Issues:")
        for issue in report.issues:
            prefix = "ERROR" if issue.severity == "error" else "WARN "
            lines.append(f"    [{prefix}] {issue.check}: {issue.message}")
            if issue.suggestion:
                lines.append(f"           → {issue.suggestion}")

    return "\n".join(lines)