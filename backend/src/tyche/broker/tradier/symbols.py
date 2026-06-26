"""OCC option symbol builder and parser.

OCC format: SYMBOL + YYMMDD + C/P + Strike(8 digits, 3 decimal implied)
Example: PL260320P00023000 = PL $23 Put expiring 2026-03-20
"""

from __future__ import annotations

from datetime import date, datetime


def build_occ_symbol(
    underlying: str,
    expiration: date,
    option_type: str,
    strike: float,
) -> str:
    """Build an OCC option symbol.

    Args:
        underlying: Ticker symbol (e.g., "PL")
        expiration: Expiration date
        option_type: "call" or "put"
        strike: Strike price (e.g., 23.0)

    Returns:
        OCC symbol string (e.g., "PL260320P00023000")
    """
    padded_symbol = underlying.upper().ljust(6, " ").rstrip()
    date_str = expiration.strftime("%y%m%d")
    type_char = "C" if option_type.lower() == "call" else "P"
    strike_int = int(strike * 1000)
    strike_str = f"{strike_int:08d}"
    return f"{padded_symbol}{date_str}{type_char}{strike_str}"


def normalize_option_type(value: str | None, *, default: str = "put") -> str:
    """Map OCC/flatfile ``P``/``C`` codes to broker ``put``/``call``."""
    text = str(value or default).lower()
    if text in ("p", "put"):
        return "put"
    if text in ("c", "call"):
        return "call"
    return text


def parse_occ_symbol(occ_symbol: str) -> dict[str, str | float | date]:
    """Parse an OCC option symbol into its components.

    Args:
        occ_symbol: OCC symbol (e.g., "PL260320P00023000")

    Returns:
        Dict with underlying, expiration, option_type, strike.
    """
    symbol = occ_symbol.rstrip()

    strike_str = symbol[-8:]
    type_char = symbol[-9]
    date_str = symbol[-15:-9]
    underlying = symbol[:-15].rstrip()

    strike = int(strike_str) / 1000.0
    option_type = "call" if type_char == "C" else "put"
    expiration = datetime.strptime(date_str, "%y%m%d").date()

    return {
        "underlying": underlying,
        "expiration": expiration,
        "option_type": option_type,
        "strike": strike,
    }
