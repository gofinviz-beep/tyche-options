"""SIC code to GICS-style sector mapping.

Maps SEC Standard Industrial Classification (SIC) codes to the 11 GICS
sector names used for portfolio analysis and graph construction.

Polygon's `/v3/reference/tickers/{ticker}` returns `sic_code` (e.g. "3571")
and `sic_description` (e.g. "ELECTRONIC COMPUTERS"). This module maps those
codes to human-readable sector names.

SIC is a range-based system — codes within a range share a sector. The mapping
below covers the most common ranges for US-listed equities. Unmapped codes
return None (caller decides how to handle).
"""

from __future__ import annotations

# (start_inclusive, end_inclusive, sector_name)
# Ordered most-specific first so narrower ranges take priority.
_SIC_RANGES: list[tuple[int, int, str]] = [
    # ── Energy ──
    (1311, 1389, "Energy"),
    (2911, 2911, "Energy"),
    (2990, 2999, "Energy"),
    (4922, 4925, "Energy"),
    (5171, 5172, "Energy"),

    # ── Materials ──
    (1000, 1099, "Materials"),
    (1200, 1299, "Materials"),
    (1400, 1499, "Materials"),
    (2400, 2499, "Materials"),
    (2600, 2699, "Materials"),
    (2800, 2829, "Materials"),
    (2860, 2899, "Materials"),
    (2950, 2952, "Materials"),
    (3310, 3399, "Materials"),

    # ── Industrials ──
    (1500, 1799, "Industrials"),
    (3410, 3499, "Industrials"),
    (3500, 3569, "Industrials"),
    (3580, 3599, "Industrials"),
    (3700, 3728, "Industrials"),
    (3730, 3749, "Industrials"),
    (3760, 3769, "Industrials"),
    (3795, 3799, "Industrials"),
    (3810, 3811, "Industrials"),
    (3820, 3824, "Industrials"),
    (3826, 3829, "Industrials"),
    (3840, 3840, "Industrials"),
    (3842, 3842, "Industrials"),
    (4000, 4099, "Industrials"),
    (4200, 4231, "Industrials"),
    (4400, 4499, "Industrials"),
    (4500, 4599, "Industrials"),
    (4600, 4699, "Industrials"),
    (4700, 4789, "Industrials"),
    (7310, 7389, "Industrials"),
    (8710, 8748, "Industrials"),

    # ── Consumer Discretionary ──
    (2200, 2299, "Consumer Discretionary"),
    (2300, 2399, "Consumer Discretionary"),
    (2500, 2599, "Consumer Discretionary"),
    (2700, 2799, "Consumer Discretionary"),
    (3140, 3199, "Consumer Discretionary"),
    (3600, 3669, "Consumer Discretionary"),
    (3711, 3716, "Consumer Discretionary"),
    (3750, 3751, "Consumer Discretionary"),
    (3790, 3792, "Consumer Discretionary"),
    (3900, 3999, "Consumer Discretionary"),
    (5000, 5046, "Consumer Discretionary"),
    (5049, 5099, "Consumer Discretionary"),
    (5130, 5159, "Consumer Discretionary"),
    (5200, 5399, "Consumer Discretionary"),
    (5500, 5599, "Consumer Discretionary"),
    (5600, 5699, "Consumer Discretionary"),
    (5700, 5736, "Consumer Discretionary"),
    (5800, 5899, "Consumer Discretionary"),
    (5900, 5999, "Consumer Discretionary"),
    (7000, 7299, "Consumer Discretionary"),
    (7830, 7833, "Consumer Discretionary"),
    (7900, 7999, "Consumer Discretionary"),

    # ── Consumer Staples ──
    (2000, 2111, "Consumer Staples"),
    (2120, 2199, "Consumer Staples"),
    (2830, 2836, "Consumer Staples"),
    (5100, 5122, "Consumer Staples"),
    (5140, 5149, "Consumer Staples"),
    (5400, 5499, "Consumer Staples"),

    # ── Health Care ──
    (2833, 2836, "Health Care"),
    (2840, 2844, "Health Care"),
    (3841, 3851, "Health Care"),
    (5047, 5048, "Health Care"),
    (5122, 5122, "Health Care"),
    (8000, 8099, "Health Care"),

    # ── Financials ──
    (6000, 6099, "Financials"),
    (6100, 6199, "Financials"),
    (6200, 6299, "Financials"),
    (6300, 6411, "Financials"),
    (6500, 6553, "Financials"),
    (6700, 6726, "Financials"),

    # ── Information Technology ──
    (3570, 3579, "Information Technology"),
    (3670, 3699, "Information Technology"),
    (3812, 3812, "Information Technology"),
    (3825, 3825, "Information Technology"),
    (3669, 3669, "Information Technology"),
    (7370, 7379, "Information Technology"),

    # ── Communication Services ──
    (2710, 2741, "Communication Services"),
    (4810, 4841, "Communication Services"),
    (4880, 4899, "Communication Services"),
    (7810, 7829, "Communication Services"),
    (7840, 7841, "Communication Services"),

    # ── Utilities ──
    (4900, 4991, "Utilities"),
    (4911, 4941, "Utilities"),

    # ── Real Estate ──
    (6510, 6553, "Real Estate"),
    (6798, 6798, "Real Estate"),
    (6726, 6726, "Financials"),
]


def sic_to_sector(sic_code: str | None) -> str | None:
    """Map a SIC code string to a GICS-style sector name.

    Uses the narrowest matching range. When multiple ranges match, the one
    with the smallest span wins (most specific classification).

    Returns None for unknown/unmapped codes.
    """
    if not sic_code:
        return None
    try:
        code = int(sic_code)
    except (ValueError, TypeError):
        return None

    best: str | None = None
    best_span = float("inf")
    for start, end, sector in _SIC_RANGES:
        if start <= code <= end:
            span = end - start
            if span < best_span:
                best = sector
                best_span = span
    return best


SECTOR_NAMES: list[str] = [
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
]
