"""
Stage 6: Validation Gate for Phase 1.

Deterministic checks run on staging_facts before promotion to live_facts.
"""

from dataclasses import dataclass, field
from typing import Optional
import duckdb


@dataclass
class ValidationIssue:
    check: str
    severity: str  # 'error' | 'warning'
    entity_id: int
    canonical_field: str
    period: str
    value: float
    message: str
    suggestion: Optional[str] = None


@dataclass
class ValidationReport:
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


INR_MAGNITUDE_RULES = {
    "revenue_gross": (1_000, 1e12),
    "revenue_net": (1_000, 1e12),
    "cogs": (1_000, 1e12),
    "gross_profit": (1_000, 1e12),
    "ebitda": (1_000, 1e12),
    "pat": (1_000, 1e12),
    "accounts_receivable": (1_000, 1e12),
    "accounts_payable": (1_000, 1e12),
    "inventory": (1_000, 1e12),
    "cash_and_equivalents": (1_000, 1e12),
    "total_debt": (1_000, 1e12),
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

POSITIVE_SIGN_FIELDS = {
    "revenue_gross",
    "revenue_net",
    "gmv",
    "arr",
    "subscription_revenue",
    "professional_services_revenue",
    "new_arr",
    "gross_profit",
    "ebitda",
    "pat",
    "accounts_receivable",
    "inventory",
    "cash_and_equivalents",
    "customer_count",
    "order_count",
    "headcount",
}

PERCENTAGE_FIELDS = {
    "gross_margin_pct",
    "ebitda_margin_pct",
    "pat_margin_pct",
    "net_dollar_retention",
    "gross_dollar_retention",
}


def _facts_table(conn, entity_id: int) -> str:
    # Prefer staging_facts for pre-promotion checks; fallback to live_facts.
    staging_count = conn.execute(
        "SELECT COUNT(*) FROM staging_facts WHERE entity_id = ?",
        [entity_id],
    ).fetchone()[0]
    return "staging_facts" if staging_count > 0 else "live_facts"


def _check_sum_relation(conn, entity_id: int) -> list[ValidationIssue]:
    issues = []
    table = _facts_table(conn, entity_id)

    result = conn.execute(
        f"""
        SELECT
            g.period,
            g.value_normalised AS gross_rev,
            r.value_normalised AS returns_val,
            n.value_normalised AS net_rev
        FROM {table} g
        LEFT JOIN {table} r ON r.entity_id = g.entity_id
            AND r.period = g.period AND r.canonical_field = 'returns_refunds'
        JOIN {table} n ON n.entity_id = g.entity_id
            AND n.period = g.period AND n.canonical_field = 'revenue_net'
        WHERE g.entity_id = ?
          AND g.canonical_field = 'revenue_gross'
        """,
        [entity_id],
    ).fetchall()

    for (period, gross, returns_val, net) in result:
        returns_val = returns_val or 0.0
        expected_net = gross - returns_val
        diff_pct = abs(net - expected_net) / abs(expected_net) if expected_net != 0 else 0
        if diff_pct > 0.05:
            issues.append(
                ValidationIssue(
                    check="sum_revenue_gross_returns",
                    severity="warning",
                    entity_id=entity_id,
                    canonical_field="revenue_net",
                    period=period,
                    value=net,
                    message=(
                        f"revenue_gross - returns_refunds ({expected_net:.2f}) differs from "
                        f"revenue_net ({net:.2f}) by {diff_pct*100:.1f}%"
                    ),
                    suggestion="Check if returns/refunds are double-counted or missing.",
                )
            )

    return issues


def _check_margin_consistency(conn, entity_id: int) -> list[ValidationIssue]:
    issues = []
    table = _facts_table(conn, entity_id)

    result = conn.execute(
        f"""
        SELECT m.period, m.value_normalised AS margin_pct, r.value_normalised AS net_rev
        FROM {table} m
        JOIN {table} r ON r.entity_id = m.entity_id AND r.period = m.period
        WHERE m.entity_id = ?
          AND m.canonical_field = 'gross_margin_pct'
          AND r.canonical_field = 'revenue_net'
        """,
        [entity_id],
    ).fetchall()

    for (period, margin_pct, net_rev) in result:
        if net_rev <= 0:
            continue
        derived_check = conn.execute(
            f"""
            SELECT value_normalised
            FROM {table}
            WHERE entity_id = ? AND period = ? AND canonical_field = 'gross_profit'
            """,
            [entity_id, period],
        ).fetchone()

        if derived_check:
            derived_gp = derived_check[0]
            expected_margin = (derived_gp / net_rev) * 100 if net_rev != 0 else 0
            diff = abs(margin_pct - expected_margin)
            if diff > 0.5:
                issues.append(
                    ValidationIssue(
                        check="margin_consistency",
                        severity="warning",
                        entity_id=entity_id,
                        canonical_field="gross_margin_pct",
                        period=period,
                        value=margin_pct,
                        message=(
                            f"gross_margin_pct ({margin_pct:.1f}%) inconsistent with derived "
                            f"value ({expected_margin:.1f}%)"
                        ),
                    )
                )

    return issues


def _check_magnitude(conn, entity_id: int) -> list[ValidationIssue]:
    issues = []
    rows = conn.execute(
        """
        SELECT canonical_field, period, value_normalised, currency, original_unit
        FROM staging_facts
        WHERE entity_id = ?
        """,
        [entity_id],
    ).fetchall()

    for (field, period, value, currency, orig_unit) in rows:
        if field in PERCENTAGE_FIELDS:
            continue

        rules = INR_MAGNITUDE_RULES if currency in ("INR", "INR_absolute") else USD_MAGNITUDE_RULES
        if field in rules:
            min_val, max_val = rules[field]
            if value < min_val or value > max_val:
                severity = "error" if value > max_val else "warning"
                issues.append(
                    ValidationIssue(
                        check="unit_magnitude",
                        severity=severity,
                        entity_id=entity_id,
                        canonical_field=field,
                        period=period,
                        value=value,
                        message=(
                            f"{field} = {value:.2f} ({currency}/{orig_unit}) outside expected "
                            f"range [{min_val:.0f}, {max_val:.0f}]"
                        ),
                        suggestion="Check unit conversion (absolute vs lakh/crore).",
                    )
                )

    return issues


def _check_period_swing(conn, entity_id: int) -> list[ValidationIssue]:
    issues = []
    table = _facts_table(conn, entity_id)
    check_fields = ["revenue_net", "revenue_gross", "ebitda", "pat"]

    for field in check_fields:
        rows = conn.execute(
            f"""
            SELECT period, value_normalised
            FROM {table}
            WHERE entity_id = ? AND canonical_field = ?
            ORDER BY period
            """,
            [entity_id, field],
        ).fetchall()

        if len(rows) < 2:
            continue

        for i in range(1, len(rows)):
            prev_period, prev_val = rows[i - 1]
            curr_period, curr_val = rows[i]
            if prev_val == 0 or curr_val == 0:
                continue
            ratio = abs(curr_val / prev_val)
            if ratio > 10 or ratio < 0.1:
                issues.append(
                    ValidationIssue(
                        check="period_swing",
                        severity="warning",
                        entity_id=entity_id,
                        canonical_field=field,
                        period=curr_period,
                        value=curr_val,
                        message=(
                            f"{field} swung {ratio:.1f}x from {prev_period} ({prev_val:.2f}) "
                            f"to {curr_period} ({curr_val:.2f})"
                        ),
                        suggestion="Verify this is business reality and not a unit/data issue.",
                    )
                )

    return issues


def _check_sign_consistency(conn, entity_id: int) -> list[ValidationIssue]:
    issues = []
    rows = conn.execute(
        """
        SELECT canonical_field, period, value_normalised
        FROM staging_facts
        WHERE entity_id = ?
        """,
        [entity_id],
    ).fetchall()

    for (field, period, value) in rows:
        if field in POSITIVE_SIGN_FIELDS and value < 0 and abs(value) > 1000:
            issues.append(
                ValidationIssue(
                    check="sign_consistency",
                    severity="warning",
                    entity_id=entity_id,
                    canonical_field=field,
                    period=period,
                    value=value,
                    message=f"{field} = {value:.2f} is negative but usually expected positive.",
                    suggestion="Check sign convention in source file.",
                )
            )

    return issues


def validate_staging_facts(
    conn: duckdb.DuckDBPyConnection,
    entity_id: int,
    file_ids: Optional[list[int]] = None,
) -> ValidationReport:
    report = ValidationReport(entity_id=entity_id, file_id=None)

    checks = [
        ("sum_relations", lambda: _check_sum_relation(conn, entity_id)),
        ("margin_consistency", lambda: _check_margin_consistency(conn, entity_id)),
        ("unit_magnitude", lambda: _check_magnitude(conn, entity_id)),
        ("period_swing", lambda: _check_period_swing(conn, entity_id)),
        ("sign_consistency", lambda: _check_sign_consistency(conn, entity_id)),
    ]

    for check_name, check_fn in checks:
        try:
            before = len(report.issues)
            report.issues.extend(check_fn())
            after = len(report.issues)
            if after == before:
                report.passed_checks.append(check_name)
        except Exception as e:
            report.issues.append(
                ValidationIssue(
                    check=check_name,
                    severity="warning",
                    entity_id=entity_id,
                    canonical_field="",
                    period="",
                    value=0.0,
                    message=f"Validation check '{check_name}' failed: {e}",
                )
            )

    return report


def run_validation(conn: duckdb.DuckDBPyConnection, file_id: int) -> list[ValidationIssue]:
    entity_row = conn.execute("SELECT entity_id FROM source_files WHERE file_id = ?", [file_id]).fetchone()
    if not entity_row:
        return []
    entity_id = entity_row[0]
    report = validate_staging_facts(conn, entity_id, file_ids=[file_id])
    return report.issues


def print_validation_report(report: ValidationReport) -> str:
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
                lines.append(f"           -> {issue.suggestion}")

    return "\n".join(lines)
