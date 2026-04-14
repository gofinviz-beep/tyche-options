"""Static ETF constituent lists for key market/sector ETFs.

Curated from public index factsheets (S&P, Nasdaq, State Street SPDR).
These lists cover the top holdings by weight for each ETF — sufficient for
the relational feature pipeline where membership and weight matter most
for large-cap tickers in the $4B+ analysis universe.

Refresh cadence: quarterly (index reconstitutions).  When the Massive ETF
Global subscription is added, these static lists become the fallback.
"""

from __future__ import annotations

CURATED_ETFS: list[str] = [
    "SPY",   # S&P 500
    "QQQ",   # Nasdaq 100
    "DIA",   # Dow Jones 30
    "XLK",   # Technology Select Sector
    "XLF",   # Financial Select Sector
    "XLE",   # Energy Select Sector
    "XLV",   # Health Care Select Sector
    "SMH",   # VanEck Semiconductor
    "SOXX",  # iShares Semiconductor
    "XLI",   # Industrial Select Sector
]

# Top holdings per ETF — tickers only (weights fetched live via yfinance).
# Ordered roughly by weight.  For broad ETFs (SPY) we include top ~100;
# for sector ETFs we include the full list.

_SPY_TOP: list[str] = [
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "AVGO", "GOOG", "META",
    "TSLA", "BRK.B", "JPM", "LLY", "XOM", "UNH", "V", "PG", "JNJ",
    "WMT", "HD", "CVX", "ABBV", "MA", "NFLX", "CRM", "BAC", "COST",
    "AMD", "MU", "PFE", "KO", "PEP", "DIS", "QCOM", "TMUS", "ACN",
    "TXN", "LIN", "ABT", "PM", "WFC", "DHR", "NEE", "C", "T",
    "SPGI", "COP", "GS", "AMGN", "LOW", "CAT", "MS", "INTC", "IBM",
    "ADP", "ISRG", "RTX", "GE", "NOW", "HON", "BLK", "BKNG", "INTU",
    "AMAT", "LRCX", "PLTR", "MDT", "UBER", "DE", "SYK", "MMC",
    "CB", "VRTX", "PANW", "PGR", "SCHW", "REGN", "CI", "ETN",
    "ADI", "MO", "CME", "SO", "BSX", "FI", "KLAC", "ELV",
    "BMY", "ICE", "MDLZ", "MCK", "APD", "CL", "SHW", "SNPS",
    "USB", "EOG", "CDNS", "TGT", "EMR", "WELL", "CSX",
]

_QQQ: list[str] = [
    "NVDA", "AAPL", "MSFT", "AMZN", "TSLA", "GOOGL", "WMT", "META",
    "GOOG", "AVGO", "COST", "MU", "NFLX", "AMD", "INTC", "LIN",
    "TMUS", "PEP", "ISRG", "BKNG", "PANW", "AMAT", "LRCX", "AMGN",
    "TXN", "QCOM", "INTU", "HON", "CSCO", "PLTR", "KLAC", "REGN",
    "SNPS", "CDNS", "MELI", "CRWD", "ADI", "ADP", "MDLZ", "MRVL",
    "ABNB", "PYPL", "GILD", "FTNT", "MAR", "CTAS", "CEG", "PDD",
    "ORLY", "DASH", "WDAY", "CSX", "ADSK", "NXPI", "MNST", "TTD",
    "ROP", "PCAR", "FANG", "AZN", "FAST", "CHTR", "KDP", "VRSK",
    "AEP", "MCHP", "DDOG", "KHC", "PAYX", "EA", "EXC", "CTSH",
    "LULU", "XEL", "CCEP", "TTWO", "ON", "CDW", "ANSS", "GEHC",
    "CSGP", "BKR", "TEAM", "BIIB", "ILMN", "IDXX", "DXCM", "ZS",
    "MRNA", "WBD", "SMCI", "LCID", "ARM", "CPRT",
]

_DIA: list[str] = [
    "NVDA", "MSFT", "AAPL", "AMZN", "GS", "HD", "CAT", "UNH",
    "V", "CRM", "SHW", "JPM", "AXP", "TRV", "MCD", "AMGN",
    "BA", "HON", "IBM", "JNJ", "MMM", "DIS", "PG", "MRK",
    "NKE", "KO", "CSCO", "WMT", "VZ", "DOW",
]

_XLK: list[str] = [
    "NVDA", "AAPL", "MSFT", "AVGO", "MU", "AMD", "PLTR", "CSCO",
    "CRM", "LRCX", "ORCL", "QCOM", "IBM", "ADBE", "TXN", "NOW",
    "AMAT", "INTU", "ADI", "KLAC", "SNPS", "CDNS", "MRVL", "INTC",
    "NXPI", "FTNT", "ROP", "APH", "MSI", "ANSS", "KEYS", "ON",
    "ZBRA", "MPWR", "TYL", "VRSN", "GEN", "FFIV", "JNPR", "SWKS",
    "TER", "NTAP", "WDC", "HPQ", "HPE", "AKAM", "EPAM",
]

_XLF: list[str] = [
    "JPM", "BRK.B", "V", "MA", "BAC", "WFC", "GS", "SPGI", "MS",
    "C", "AXP", "PGR", "SCHW", "CB", "MMC", "CME", "ICE", "BLK",
    "FI", "AON", "MCO", "PNC", "USB", "AIG", "TFC", "MET",
    "AJG", "TROW", "ALL", "AFL", "TRV", "BK", "PRU", "FITB",
    "STT", "CINF", "HBAN", "RF", "MTB", "CFG", "KEY", "NTRS",
    "RJF", "CBOE", "NDAQ", "FDS", "WRB", "BRO", "EG",
    "L", "DFS", "COF", "SYF",
]

_XLE: list[str] = [
    "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PXD", "PSX",
    "VLO", "WMB", "OKE", "KMI", "FANG", "HAL", "HES", "DVN",
    "BKR", "TRGP", "CTRA", "MRO", "OVV", "APA",
]

_XLV: list[str] = [
    "LLY", "JNJ", "ABBV", "MRK", "UNH", "PFE", "ABT", "TMO",
    "AMGN", "MDT", "ISRG", "DHR", "SYK", "BSX", "VRTX", "REGN",
    "CI", "ELV", "GILD", "BDX", "ZTS", "MCK", "HCA", "DXCM",
    "GEHC", "A", "IQV", "IDXX", "MTD", "EW", "BAX", "RMD",
    "HOLX", "TFX", "ALGN", "BIIB", "PODD", "COO",
]

_SMH: list[str] = [
    "NVDA", "AVGO", "MU", "AMD", "QCOM", "TXN", "INTC", "AMAT",
    "LRCX", "KLAC", "ADI", "MRVL", "NXPI", "ON", "MCHP", "MPWR",
    "SWKS", "TER", "ENTG", "MKSI", "ACLS", "OLED", "RMBS",
    "ARM", "SMCI",
]

_SOXX: list[str] = [
    "NVDA", "AVGO", "MU", "AMD", "QCOM", "TXN", "INTC", "AMAT",
    "LRCX", "KLAC", "ADI", "MRVL", "NXPI", "ON", "MCHP", "MPWR",
    "SWKS", "TER", "ENTG", "MKSI", "ACLS", "OLED", "RMBS",
    "ARM", "SMCI", "GFS", "WOLF", "ALGM", "CRUS",
]

_XLI: list[str] = [
    "GE", "CAT", "RTX", "HON", "DE", "UNP", "ETN", "BA",
    "UPS", "LMT", "ADP", "MMM", "CSX", "EMR", "NOC", "GD",
    "ITW", "WM", "NSC", "PCAR", "CTAS", "TDG", "PH",
    "FDX", "CARR", "OTIS", "AME", "ROK", "IR", "CMI",
    "VRSK", "FAST", "PWR", "DOV", "SWK", "ODFL", "CPRT",
]

_ETF_CONSTITUENTS: dict[str, list[str]] = {
    "SPY": _SPY_TOP,
    "QQQ": _QQQ,
    "DIA": _DIA,
    "XLK": _XLK,
    "XLF": _XLF,
    "XLE": _XLE,
    "XLV": _XLV,
    "SMH": _SMH,
    "SOXX": _SOXX,
    "XLI": _XLI,
}


def get_static_constituents(etf_ticker: str) -> list[str]:
    """Return the curated constituent list for a given ETF."""
    return list(_ETF_CONSTITUENTS.get(etf_ticker.upper(), []))


def get_all_static_constituents() -> dict[str, list[str]]:
    """Return all curated ETF → constituents mappings."""
    return {k: list(v) for k, v in _ETF_CONSTITUENTS.items()}


def get_stock_etf_memberships(ticker: str) -> list[str]:
    """Return the list of curated ETFs that contain the given stock."""
    return [etf for etf, members in _ETF_CONSTITUENTS.items() if ticker in members]
