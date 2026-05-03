"""
Unit normalisation for Phase 1.

Every monetary value entering DuckDB is stored as:
  (value_normalised DOUBLE,   -- always in base unit (INR absolute or USD absolute)
   currency TEXT,             -- 'INR' | 'USD' | etc.
   original_unit TEXT,        -- e.g. 'Rs. Lakhs', 'INR Crore', 'USD thousands'
   conversion_factor DOUBLE,  -- what we multiplied by to get value_normalised
   conversion_applied BOOL)

This eliminates the Lakhs-to-Crores addition bug by design.
"""

from dataclasses import dataclass
from typing import Optional
import re


@dataclass
class UnitSpec:
    currency: str            # 'INR' | 'USD' | 'GBP' etc.
    multiplier: float        # multiply raw value by this to get absolute units
    original_label: str      # human-readable label for storage


# ── Known unit patterns ───────────────────────────────────────────────────────

UNIT_PATTERNS: list[tuple[re.Pattern, UnitSpec]] = [
    # INR variants
    (re.compile(r"rs\.?\s*crore|inr\s*cr|crore|cr\.", re.I),
     UnitSpec(currency="INR", multiplier=10_000_000, original_label="INR Crore")),

    (re.compile(r"rs\.?\s*lakh|inr\s*lakh|lakh|lac\b", re.I),
     UnitSpec(currency="INR", multiplier=100_000, original_label="INR Lakh")),

    (re.compile(r"rs\.?\s*thousand|inr\s*thousand|'000\s*inr", re.I),
     UnitSpec(currency="INR", multiplier=1_000, original_label="INR Thousand")),

    (re.compile(r"rs\.?\s*million|inr\s*million", re.I),
     UnitSpec(currency="INR", multiplier=1_000_000, original_label="INR Million")),

    (re.compile(r"inr\s*absolute|inr$|rs\.?$|rupees", re.I),
     UnitSpec(currency="INR", multiplier=1, original_label="INR")),

    # USD variants
    (re.compile(r"usd\s*thousand|usd\s*'?000|\$\s*thousand", re.I),
     UnitSpec(currency="USD", multiplier=1_000, original_label="USD Thousand")),

    (re.compile(r"usd\s*million|usd\s*mn|\$\s*million|\$mn", re.I),
     UnitSpec(currency="USD", multiplier=1_000_000, original_label="USD Million")),

    (re.compile(r"usd\s*billion|\$\s*billion|\$bn", re.I),
     UnitSpec(currency="USD", multiplier=1_000_000_000, original_label="USD Billion")),

    (re.compile(r"usd$|\$\s*$|usd\s*absolute", re.I),
     UnitSpec(currency="USD", multiplier=1, original_label="USD")),
]

# Fallback when no unit is detected — flag as UNKNOWN so the onboarding
# conversation can ask the user to confirm.
UNKNOWN_UNIT = UnitSpec(currency="UNKNOWN", multiplier=1, original_label="UNKNOWN")


def detect_unit(raw_unit_str: str) -> UnitSpec:
    """
    Parse a raw unit string (from a file header or sheet tab) into a UnitSpec.
    Returns UNKNOWN_UNIT if no pattern matches — caller must surface this
    as a conflict for the user to resolve.
    """
    if not raw_unit_str:
        return UNKNOWN_UNIT

    s = raw_unit_str.strip()
    for pattern, spec in UNIT_PATTERNS:
        if pattern.search(s):
            return spec

    return UNKNOWN_UNIT


def normalise_value(raw_value: float, unit_spec: UnitSpec) -> float:
    """Convert raw_value in unit_spec units to absolute base units."""
    return raw_value * unit_spec.multiplier


def format_for_display(absolute_value: float, currency: str, target_unit: str = "auto") -> str:
    """
    Format an absolute value for display.
    target_unit: 'auto' | 'crore' | 'lakh' | 'million' | 'absolute'
    """
    if currency == "INR":
        if target_unit == "auto":
            if abs(absolute_value) >= 10_000_000:
                return f"₹{absolute_value / 10_000_000:.2f} Cr"
            elif abs(absolute_value) >= 100_000:
                return f"₹{absolute_value / 100_000:.2f} L"
            else:
                return f"₹{absolute_value:,.0f}"
        elif target_unit == "crore":
            return f"₹{absolute_value / 10_000_000:.2f} Cr"
        elif target_unit == "lakh":
            return f"₹{absolute_value / 100_000:.2f} L"
    elif currency == "USD":
        if target_unit == "auto":
            if abs(absolute_value) >= 1_000_000:
                return f"${absolute_value / 1_000_000:.2f}M"
            elif abs(absolute_value) >= 1_000:
                return f"${absolute_value / 1_000:.2f}K"
            else:
                return f"${absolute_value:,.0f}"

    return f"{absolute_value:,.2f}"


@dataclass
class NormalisedValue:
    value_normalised: float
    currency: str
    original_unit: str
    conversion_factor: float
    conversion_applied: bool

    def display(self, target_unit: str = "auto") -> str:
        return format_for_display(self.value_normalised, self.currency, target_unit)


def normalise(raw_value: float, raw_unit_str: str) -> NormalisedValue:
    """Full pipeline: detect unit → normalise → return NormalisedValue."""
    spec = detect_unit(raw_unit_str)
    return NormalisedValue(
        value_normalised=normalise_value(raw_value, spec),
        currency=spec.currency,
        original_unit=spec.original_label,
        conversion_factor=spec.multiplier,
        conversion_applied=(spec.multiplier != 1),
    )
