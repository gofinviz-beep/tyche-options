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
    gemini_model_fast: str = "gemini-3-flash-preview"
    gemini_model_deep: str = "gemini-3.1-pro-preview"

    # --- Market Data (Polygon.io / Massive.com) ---
    polygon_api_key: str = ""
    polygon_base_url: str = "https://api.polygon.io"
    polygon_rate_limit_rpm: int = 100  # Paid plans allow much higher RPM

    # Alpha Vantage free API key (optional — "demo" key works with rate limits)
    alpha_vantage_key: str = "demo"
    # Manual earnings overrides: {"PL": "2026-06-15", "AAPL": "2026-07-25"}
    earnings_overrides: dict[str, str] = Field(default_factory=dict)
    # Legacy key (kept for backward compat, maps to alpha_vantage_key)
    earnings_api_key: str = ""

    # --- Data Storage ---
    data_dir: str = "data"
    db_dir: str = "db"

    # --- Universe Filtering ---
    min_market_cap_millions: float = 5000.0
    min_avg_volume: int = 500_000
    min_stock_price: float = 15.0

    # --- Institutional Ownership ---
    min_institutional_pct: float = 0.40  # 40% minimum institutional ownership
    min_institutional_pct_stock_buy: float = 0.50  # 50% for stock buy recommendations

    # --- Conviction Engine ---
    ema_fast_period: int = 8
    ema_slow_period: int = 21
    pullback_proximity_pct: float = 2.0
    max_extension_pct: float = 3.0
    min_days_above_emas: int = 5
    max_days_above_emas: int = 10
    bootstrap_days: int = 120

    # --- Pullback CSP ---
    pullback_csp_enabled: bool = True
    min_prior_streak: int = 5  # min days above both EMAs before the pullback
    pullback_strike_offset_pct: float = 5.0  # strike at X% below support EMA

    # --- Capital ---
    available_capital: float = 100_000.0  # Available cash for CSP collateral (Fidelity)

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

    # --- Options Scan ---
    max_expiration_dates: int = 2
    expiration_mode: str = "friday_target"  # "friday_target" or "max_n"
    strike_range_pct: float = 15.0  # Only consider strikes within X% below the 8-EMA
    llm_concurrency: int = 5  # Max parallel LLM calls
    csp_strike_preference: str = "near_21ema"  # "near_21ema", "otm_target", or "legacy"

    # --- Scan Persistence ---
    scan_retention_count: int = 5

    # --- Conviction Batch ---
    conviction_batch_min_market_cap_millions: float = 500.0
    conviction_batch_min_price: float = 5.0
    conviction_batch_min_avg_volume: int = 500_000
    conviction_snapshot_retention_days: int = 90

    # --- Workflow ---
    morning_scan_time: str = "09:35"
    order_monitor_interval_min: int = 15
    midday_review_time: str = "12:30"
    eod_journal_time: str = "15:50"

    # --- Watchlist ---
    watchlist_symbols: list[str] = Field(default_factory=list)

    # --- Notifications ---
    notification_email_enabled: bool = False
    notification_email_to: str = ""
    notification_smtp_host: str = "smtp.gmail.com"
    notification_smtp_port: int = 587
    notification_smtp_user: str = ""
    notification_smtp_password: str = ""
    notification_pullback_alert_enabled: bool = True
    daily_digest_enabled: bool = False
    daily_digest_time: str = "16:00"

    # --- Observability ---
    log_level: str = "INFO"
    gcp_project_id: str = ""
    otel_service_name: str = "tyche-options"
    otel_enabled: bool = True

    # --- Database ---
    database_url: str = ""

    @property
    def effective_database_url(self) -> str:
        """Resolve default DB URL into db_dir if not explicitly set."""
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.db_dir}/tyche.db"

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
