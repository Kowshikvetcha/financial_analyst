"""
Period normalisation for Phase 1.

All periods are stored in a canonical string format:
  Annual:    "FY24"          (April 2023 – March 2024, Indian FY)
  Monthly:   "FY24-M06"     (September 2023, month 6 of FY24)
  Quarterly: "FY24-Q2"      (July–September 2023, Q2 of FY24)
  Calendar:  "CY2023"       (Jan–Dec 2023, for non-India FY entities)
  CY Month:  "CY2023-M09"   (September 2023 calendar)

The orchestrator always stores the canonical string.  The tool layer
(Phase 3) resolves period aliases at query time.
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class PeriodSpec:
    canonical: str           # the stored string
    period_type: str         # 'annual' | 'monthly' | 'quarterly' | 'calendar_year'
    fiscal_year: int         # e.g. 2024 for FY24
    month: Optional[int]     # 1-12 for monthly, None otherwise
    quarter: Optional[int]   # 1-4 for quarterly, None otherwise


# ── Indian FY month mapping ───────────────────────────────────────────────────
# Month 1 of Indian FY = April (calendar month 4)

MONTH_NAME_TO_FISCAL: dict[str, int] = {
    "april": 1, "apr": 1,
    "may": 2,
    "june": 3, "jun": 3,
    "july": 4, "jul": 4,
    "august": 5, "aug": 5,
    "september": 6, "sep": 6, "sept": 6,
    "october": 7, "oct": 7,
    "november": 8, "nov": 8,
    "december": 9, "dec": 9,
    "january": 10, "jan": 10,
    "february": 11, "feb": 11,
    "march": 12, "mar": 12,
}

FISCAL_MONTH_TO_CALENDAR: dict[int, int] = {
    1: 4, 2: 5, 3: 6, 4: 7, 5: 8, 6: 9,
    7: 10, 8: 11, 9: 12, 10: 1, 11: 2, 12: 3
}


def _fiscal_year_from_short(short: str) -> int:
    """'24' → 2024, '23' → 2023"""
    y = int(short)
    return y + 2000 if y < 100 else y


# ── Pattern matchers ──────────────────────────────────────────────────────────

def parse_period(raw: str) -> Optional[PeriodSpec]:
    """
    Parse a raw period string into a PeriodSpec.
    Returns None if unrecognised (caller should flag as conflict).
    """
    s = raw.strip()

    # FY24, FY2024, FY 24
    m = re.match(r"FY\s*(\d{2,4})$", s, re.I)
    if m:
        fy = _fiscal_year_from_short(m.group(1))
        return PeriodSpec(f"FY{str(fy)[2:]}", "annual", fy, None, None)

    # FY24-M06, FY24-M6
    m = re.match(r"FY\s*(\d{2,4})[_\-]M(\d{1,2})$", s, re.I)
    if m:
        fy = _fiscal_year_from_short(m.group(1))
        mo = int(m.group(2))
        return PeriodSpec(f"FY{str(fy)[2:]}-M{mo:02d}", "monthly", fy, mo, None)

    # FY24-Q2, FY24Q2
    m = re.match(r"FY\s*(\d{2,4})[_\-]?Q(\d)$", s, re.I)
    if m:
        fy = _fiscal_year_from_short(m.group(1))
        q = int(m.group(2))
        return PeriodSpec(f"FY{str(fy)[2:]}-Q{q}", "quarterly", fy, None, q)

    # Q2FY24, Q2 FY24
    m = re.match(r"Q(\d)\s*FY\s*(\d{2,4})$", s, re.I)
    if m:
        q = int(m.group(1))
        fy = _fiscal_year_from_short(m.group(2))
        return PeriodSpec(f"FY{str(fy)[2:]}-Q{q}", "quarterly", fy, None, q)

    # "Apr-24", "Apr 2024", "April 2024"
    m = re.match(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-](\d{2,4})$", s, re.I)
    if m:
        month_name = m.group(1).lower()
        year_short = m.group(2)
        cal_year = _fiscal_year_from_short(year_short)
        # Determine fiscal year: Jan/Feb/Mar belong to the FY ending that year
        cal_month = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }[month_name]
        if cal_month >= 4:  # Apr–Dec: fiscal year ends next calendar year
            fy = cal_year + 1
        else:               # Jan–Mar: fiscal year ends this calendar year
            fy = cal_year
        # Fiscal month number
        fiscal_month = MONTH_NAME_TO_FISCAL[month_name]
        return PeriodSpec(f"FY{str(fy)[2:]}-M{fiscal_month:02d}", "monthly", fy, fiscal_month, None)

    # FY 2023-24, FY2021-22 (two-year notation — take end year)
    m = re.match(r"FY\s*(\d{4})\s*[-–]\s*(\d{2,4})$", s, re.I)
    if m:
        end_str = m.group(2)
        end_year = int(end_str) if len(end_str) == 4 else 2000 + int(end_str)
        return PeriodSpec(f"FY{str(end_year)[2:]}", "annual", end_year, None, None)

    # FY2023A, FY24E, FY2025P (actual/estimate/projected suffix)
    m = re.match(r"FY\s*(\d{2,4})[AEPaep]$", s, re.I)
    if m:
        fy = _fiscal_year_from_short(m.group(1))
        return PeriodSpec(f"FY{str(fy)[2:]}", "annual", fy, None, None)

    # Q1'22, Q3'23 (apostrophe shorthand for year)
    m = re.match(r"Q([1-4])[''](\d{2,4})$", s, re.I)
    if m:
        q = int(m.group(1))
        fy = _fiscal_year_from_short(m.group(2))
        return PeriodSpec(f"FY{str(fy)[2:]}-Q{q}", "quarterly", fy, None, q)

    # "1-Apr-23 to 31-Mar-24" (range — recursively parse end date)
    m = re.match(r".+\s+to\s+(.+)$", s, re.I)
    if m:
        return parse_period(m.group(1).strip())

    # 31-Mar-2022, 31 Mar 2022 (date format — map to fiscal year)
    m = re.match(r"(\d{1,2})[\s\-](jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-](\d{2,4})$", s, re.I)
    if m:
        cal_month_map = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                         "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
        cal_month = cal_month_map[m.group(2).lower()[:3]]
        year = _fiscal_year_from_short(m.group(3))
        fy = year + 1 if cal_month >= 4 else year
        return PeriodSpec(f"FY{str(fy)[2:]}", "annual", fy, None, None)

    # 31.03.24, 31/03/2024, 31-03-24 (DD.MM.YY date format)
    m = re.match(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})$", s)
    if m:
        cal_month = int(m.group(2))
        year = _fiscal_year_from_short(m.group(3))
        fy = year + 1 if cal_month >= 4 else year
        return PeriodSpec(f"FY{str(fy)[2:]}", "annual", fy, None, None)

    # CY2023, CY 2023
    m = re.match(r"CY\s*(\d{4})$", s, re.I)
    if m:
        cy = int(m.group(1))
        return PeriodSpec(f"CY{cy}", "calendar_year", cy, None, None)

    # Plain year "2023", "2024"
    m = re.match(r"^(20\d{2})$", s)
    if m:
        cy = int(m.group(1))
        # Ambiguous — could be FY or CY. Store as CY, flag for confirmation.
        return PeriodSpec(f"CY{cy}", "calendar_year", cy, None, None)

    return None


def period_label(spec: PeriodSpec) -> str:
    """Human readable period label for display."""
    if spec.period_type == "annual":
        fy = spec.fiscal_year
        return f"FY{str(fy)[2:]} (Apr {fy-1}–Mar {fy})"
    if spec.period_type == "monthly" and spec.month:
        cal_month = FISCAL_MONTH_TO_CALENDAR[spec.month]
        from calendar import month_abbr
        fy = spec.fiscal_year
        cal_year = fy if cal_month <= 3 else fy - 1
        return f"{month_abbr[cal_month]} {cal_year}"
    if spec.period_type == "quarterly" and spec.quarter:
        q_months = {1: "Apr–Jun", 2: "Jul–Sep", 3: "Oct–Dec", 4: "Jan–Mar"}
        return f"Q{spec.quarter} FY{str(spec.fiscal_year)[2:]} ({q_months[spec.quarter]})"
    return spec.canonical
