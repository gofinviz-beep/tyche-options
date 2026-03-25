"""Application configuration via environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TycheSettings(BaseSettings):
    """All application settings, loaded from env vars prefixed with TYCHE_."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TYCHE_",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # --- Broker ---
    tradier_api_token: str = ""
    tradier_account_id: str = ""
    tradier_sandbox: bool = True
    tradier_base_url: str = Field(default="")

    # --- LLM ---
    gemini_api_key: str = ""
    gemini_model_fast: str = "gemini-2.5-flash"
    gemini_model_deep: str = "gemini-2.5-pro"

    # --- Market Data ---
    # Alpha Vantage free API key (optional — "demo" key works with rate limits)
    # Get a free key at https://www.alphavantage.co/support/#api-key
    alpha_vantage_key: str = "demo"
    # Manual earnings overrides: {"PL": "2026-06-15", "AAPL": "2026-07-25"}
    earnings_overrides: dict[str, str] = Field(default_factory=dict)
    # Legacy key (kept for backward compat, maps to alpha_vantage_key)
    earnings_api_key: str = ""

    # --- Risk Limits ---
    max_risk_per_trade_pct: float = 5.0
    max_account_exposure_pct: float = 70.0
    max_concentration_per_ticker_pct: float = 25.0
    max_open_positions: int = 8
    max_new_trades_per_day: int = 3
    max_contracts_per_position: int = 40
    min_market_cap_billions: float = 1.0
    preview_only_mode: bool = True

    # --- Wheel-specific ---
    csp_target_dte_min: int = 3
    csp_target_dte_max: int = 14
    cc_target_dte_min: int = 3
    cc_target_dte_max: int = 14
    min_annualized_return_pct: float = 15.0

    # --- Workflow ---
    morning_scan_time: str = "09:35"
    order_monitor_interval_min: int = 15
    midday_review_time: str = "12:30"
    eod_journal_time: str = "15:50"

    # --- Watchlist ---
    watchlist_symbols: list[str] = Field(default_factory=list)

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///tyche.db"

    @property
    def broker_base_url(self) -> str:
        if self.tradier_base_url:
            return self.tradier_base_url
        if self.tradier_sandbox:
            return "https://sandbox.tradier.com/v1"
        return "https://api.tradier.com/v1"


def get_settings() -> TycheSettings:
    """Factory for settings singleton."""
    return TycheSettings()
