"""Route artifact registry shared by publisher (GCP-C) and API reader (GCP-D)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tyche.storage.paths import StorageContext

ROUTE_FILES: dict[str, str] = {
    "options": "options.json",
    "options_scanner": "options_scanner.json",
    "options_conviction": "options_conviction.json",
    "options_explore": "options_explore.json",
    "options_monitor": "options_monitor.json",
    "options_covered_calls": "options_covered_calls.json",
    "stocks": "stocks.json",
    "stocks_alpha": "stocks_alpha.json",
    "stocks_conviction": "stocks_conviction.json",
    "stocks_deep_dips": "stocks_deep_dips.json",
    "stocks_history": "stocks_history.json",
    "intelligence": "intelligence.json",
    "intelligence_news": "intelligence_news.json",
    "intelligence_filings": "intelligence_filings.json",
    "intelligence_insider": "intelligence_insider.json",
}

ROUTE_PATHS: dict[str, str] = {
    "options": "/options",
    "options_scanner": "/options/scanner",
    "options_conviction": "/options/conviction",
    "options_explore": "/options/explore",
    "options_monitor": "/options/monitor",
    "options_covered_calls": "/options/covered-calls",
    "stocks": "/stocks/",
    "stocks_alpha": "/stocks/alpha/",
    "stocks_conviction": "/stocks/conviction",
    "stocks_deep_dips": "/stocks/deep-dips",
    "stocks_history": "/stocks/history",
    "intelligence": "/intelligence",
    "intelligence_news": "/intelligence/news",
    "intelligence_filings": "/intelligence/filings",
    "intelligence_insider": "/intelligence/insider",
}

ALPHA_SUSTAINED_SOURCE_CANDIDATES = (
    "signals/alpha/alpha_signals_sustained.parquet",
    "alpha_signals_sustained.parquet",
)
ALPHA_PEAK_SOURCE_CANDIDATES = (
    "signals/alpha/alpha_signals.parquet",
    "alpha_signals.parquet",
)


def first_existing_path(
    candidates: tuple[str, ...] | list[str],
    *,
    ctx: "StorageContext",
) -> str | None:
    """Return the first relative path that exists in storage."""
    from tyche.storage import exists as storage_exists

    for rel in candidates:
        if storage_exists(rel, ctx=ctx):
            return rel
    return None
