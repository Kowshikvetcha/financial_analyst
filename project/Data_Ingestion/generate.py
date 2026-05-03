"""
Mock data generator for Phase 1 testing.

Creates two test files mirroring the PRD's test corpus:
  1. glow_naturals_monthly_pnl.csv  — wide format, INR Lakhs, 12 months
  2. krishnan_engineering_annual.csv — wide format, INR Crore, 3 years,
                                       with intentional revenue discrepancy (conflict test)
"""

import csv
from pathlib import Path
import random

OUT_DIR = Path(__file__).parent


def create_glow_naturals(out_path: Path) -> None:
    """
    Glow Naturals D2C beauty brand — monthly P&L, INR Lakhs, FY24.
    Wide format: rows=metrics, cols=months.
    """
    months = [
        "Apr-23", "May-23", "Jun-23", "Jul-23", "Aug-23", "Sep-23",
        "Oct-23", "Nov-23", "Dec-23", "Jan-24", "Feb-24", "Mar-24"
    ]

    # Simulate a growing D2C brand with monsoon dip in Jun-Aug
    random.seed(42)

    def monthly_revenue(base: float) -> list[float]:
        seasonality = [1.0, 1.05, 0.88, 0.82, 0.80, 0.85,
                       1.10, 1.20, 1.25, 1.15, 1.10, 1.30]
        growth = [1 + 0.03 * i for i in range(12)]
        return [round(base * s * g + random.uniform(-1, 1), 2)
                for s, g in zip(seasonality, growth)]

    gmv        = monthly_revenue(32.0)
    rev_net    = [round(g * 0.85, 2) for g in gmv]   # net of returns/discounts
    cogs       = [round(r * 0.42, 2) for r in rev_net]
    salary     = [round(3.2 + (0.3 if i >= 6 else 0), 2) for i in range(12)]  # ops hire in Oct
    marketing  = [round(r * 0.18, 2) for r in rev_net]
    rent       = [1.8] * 12
    opex       = [round(s + m + r + 0.5, 2) for s, m, r in zip(salary, marketing, rent)]
    ebitda     = [round(r - c - o, 2) for r, c, o in zip(rev_net, cogs, opex)]
    pat        = [round(e * 0.72, 2) for e in ebitda]

    rows = [
        ("Metric (Rs. Lakhs)", *months),
        ("GMV", *gmv),
        ("Net Revenue", *rev_net),
        ("Cost of Goods Sold", *cogs),
        ("Salary", *salary),
        ("Marketing", *marketing),
        ("Rent", *rent),
        ("Operating Expenses", *opex),
        ("EBITDA", *ebitda),
        ("Net Profit", *pat),
        ("Headcount", *[12, 12, 12, 13, 13, 13, 15, 15, 15, 15, 15, 16]),
        ("Total Orders", *[round(gmv[i] / 1.4, 0) for i in range(12)]),
    ]

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(f"✓ Created: {out_path}")


def create_krishnan_engineering(out_path_1: Path, out_path_2: Path) -> None:
    """
    Krishnan Engineering Works Pvt Ltd — annual P&L.
    Two files with conflicting turnover figures for FY24 (the PRD's stress test).
    File 1: audited accounts (INR Crore) — authoritative
    File 2: management MIS (INR Crore) — slightly different FY24 revenue
    """
    periods = ["FY22", "FY23", "FY24"]

    # File 1: Audited accounts
    rows_1 = [
        ("Particulars (INR Crore)", "FY22", "FY23", "FY24"),
        ("Turnover (net of GST)", 18.4, 22.7, 27.3),   # authoritative
        ("Cost of Revenue", 11.9, 14.6, 17.5),
        ("Gross Profit", 6.5, 8.1, 9.8),
        ("Employee Cost", 1.8, 2.1, 2.6),
        ("Marketing and Advertising", 0.4, 0.6, 0.8),
        ("Office Rent", 0.3, 0.3, 0.4),
        ("EBITDA", 4.0, 5.1, 6.0),
        ("Profit After Tax", 2.8, 3.5, 4.1),
        ("Accounts Receivable", 3.2, 3.8, 4.5),
        ("Inventory", 2.1, 2.6, 3.0),
        ("Total Borrowings", 5.0, 4.2, 3.5),
    ]

    # File 2: Management MIS — FY24 turnover is different (conflict!)
    rows_2 = [
        ("Revenue Summary (Rs. Crore)", "FY22", "FY23", "FY24"),
        ("Total Revenue from Operations", 18.4, 22.7, 28.1),  # ← CONFLICT: 28.1 vs 27.3
        ("Raw Material Consumed", 11.9, 14.6, 18.0),
        ("Employee Benefits", 1.8, 2.1, 2.6),
        ("EBITDA", 4.0, 5.1, 5.8),                           # ← also slightly different
        ("Net Profit", 2.8, 3.5, 4.0),
        ("Debtors", 3.2, 3.8, 4.7),
        ("Closing Stock", 2.1, 2.6, 3.2),
        ("Borrowings", 5.0, 4.2, 3.5),
    ]

    with open(out_path_1, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows_1)

    with open(out_path_2, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows_2)

    print(f"✓ Created: {out_path_1}")
    print(f"✓ Created: {out_path_2}")


if __name__ == "__main__":
    create_glow_naturals(OUT_DIR / "glow_naturals_monthly_pnl.csv")
    create_krishnan_engineering(
        OUT_DIR / "krishnan_audited_accounts.csv",
        OUT_DIR / "krishnan_management_mis.csv",
    )
    print("\nTest corpus ready in mock_data/")
