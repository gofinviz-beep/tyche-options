"""Application configuration with split persistence.

Secrets and infrastructure settings come from environment variables / ``.env``.
User-editable operational config (risk limits, watchlist, scan params, etc.)
is persisted to ``config.db`` so changes made via the Settings UI take effect
immediately without a restart.

Loading priority (highest wins):
  1. Environment / ``.env``  → secrets + infrastructure
  2. ``config.db`` (SQLite)  → user-editable operational config
  3. Defaults defined here   → fallback values
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tyche.persistence.config_store import ConfigStore

logger = structlog.get_logger()


def offset_schedule_time(time_hm: str, offset_minutes: int) -> tuple[int, int]:
    """Return ``(hour, minute)`` in ET after shifting ``HH:MM`` by *offset_minutes*."""
    parts = (time_hm or "00:00").strip().split(":")
    hour = int(parts[0]) if parts and parts[0] else 0
    minute = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    total = (hour * 60 + minute + offset_minutes) % (24 * 60)
    return total // 60, total % 60


def ohlcv_refresh_time_before_flatfile(
    flatfile_time: str, offset_minutes: int = 30,
) -> tuple[int, int]:
    """OHLCV refresh slot: *offset_minutes* before the flatfile ingest time."""
    return offset_schedule_time(flatfile_time, -offset_minutes)


# ---------------------------------------------------------------------------
# 1.  Environment-only settings (secrets + deployment infrastructure)
# ---------------------------------------------------------------------------

_ENV_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        # Secrets
        "tradier_api_token",
        "tradier_account_id",
        "gemini_api_key",
        "polygon_api_key",
        "alpha_vantage_key",
        "earnings_api_key",
        "notification_smtp_user",
        "notification_smtp_password",
        "database_url",
        "gcp_project_id",
        # Infrastructure (deployment-specific, not user-editable)
        "tradier_sandbox",
        "tradier_base_url",
        "data_dir",
        "data_backend",
        "gcs_bucket",
        "gcs_prefix",
        "run_env",
        "load_gcp_secrets",
        "db_dir",
        "polygon_base_url",
        "polygon_rate_limit_rpm",
        "polygon_market_cap_concurrency",
        "log_level",
        "otel_service_name",
        "otel_enabled",
        "notification_smtp_host",
        "notification_smtp_port",
        "massive_s3_access_key",
        "massive_s3_secret_key",
        "massive_s3_url",
        "massive_s3_bucket",
        "finnhub_api_key",
        "edgar_user_agent_email",
        "ingest_window",
    }
)


def _strip_inline_env_comment(value: str) -> str:
    """Drop trailing ``# comment`` from a dotenv value (pydantic does not)."""
    cleaned = value.strip()
    if " #" in cleaned:
        cleaned = cleaned.split(" #", 1)[0].strip()
    if cleaned.startswith("#"):
        return ""
    return cleaned


class _EnvSettings(BaseSettings):
    """Read-only slice loaded exclusively from env vars / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TYCHE_",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Secrets ---
    tradier_api_token: str = ""
    tradier_account_id: str = ""
    gemini_api_key: str = ""
    polygon_api_key: str = ""
    alpha_vantage_key: str = "demo"
    earnings_api_key: str = ""
    notification_smtp_user: str = ""
    notification_smtp_password: str = ""
    database_url: str = ""
    gcp_project_id: str = ""

    # --- Infrastructure ---
    tradier_sandbox: bool = True
    tradier_base_url: str = Field(default="")
    data_dir: str = "data"
    data_backend: str = "local"
    gcs_bucket: str = ""
    gcs_prefix: str = ""
    run_env: str = "dev"
    load_gcp_secrets: bool = False
    # evening | morning — Cloud Run per-job env (Pacific session end date)
    ingest_window: str = ""

    @field_validator("gcs_bucket", mode="before")
    @classmethod
    def _normalize_gcs_bucket(cls, value: Any) -> str:
        if value is None:
            return ""
        return _strip_inline_env_comment(str(value))

    @field_validator("gcs_prefix", mode="before")
    @classmethod
    def _normalize_gcs_prefix(cls, value: Any) -> str:
        if value is None:
            return ""
        return _strip_inline_env_comment(str(value)).strip("/")
    db_dir: str = "db"
    polygon_base_url: str = "https://api.polygon.io"
    polygon_rate_limit_rpm: int = 500
    polygon_market_cap_concurrency: int = 20
    log_level: str = "INFO"
    otel_service_name: str = "tyche-options"
    otel_enabled: bool = True
    notification_smtp_host: str = "smtp.gmail.com"
    notification_smtp_port: int = 587

    # --- Massive S3 flat files ---
    massive_s3_access_key: str = ""
    massive_s3_secret_key: str = ""
    massive_s3_url: str = "https://files.massive.com"
    massive_s3_bucket: str = "flatfiles"

    # --- Finnhub ---
    finnhub_api_key: str = ""

    # --- EDGAR ---
    edgar_user_agent_email: str = ""


# ---------------------------------------------------------------------------
# 2.  Complete settings model (BaseModel — NOT BaseSettings)
# ---------------------------------------------------------------------------


class TycheSettings(BaseModel):
    """All application settings.

    Built by :func:`get_settings` which merges env + config.db + defaults.
    Can also be instantiated directly in tests with explicit kwargs.
    """

    # --- Broker (env-only) ---
    tradier_api_token: str = ""
    tradier_account_id: str = ""
    tradier_sandbox: bool = True
    tradier_base_url: str = Field(default="")
    broker_cache_ttl: float = 300.0

    # --- LLM (key is env-only, model names are config) ---
    gemini_api_key: str = ""
    gemini_model_fast: str = "gemini-3-flash-preview"
    gemini_model_deep: str = "gemini-3.1-pro-preview"
    gemini_model_classify: str = "gemini-2.5-flash-lite"

    # --- Market Data (keys/infra are env-only) ---
    polygon_api_key: str = ""
    polygon_base_url: str = "https://api.polygon.io"
    polygon_rate_limit_rpm: int = 500
    polygon_market_cap_concurrency: int = 20

    alpha_vantage_key: str = "demo"
    earnings_overrides: dict[str, str] = Field(default_factory=dict)
    earnings_api_key: str = ""

    # --- Data Storage (env-only) ---
    data_dir: str = "data"
    data_backend: str = "local"  # local | gcs
    gcs_bucket: str | None = None
    gcs_prefix: str = ""
    run_env: str = "dev"
    load_gcp_secrets: bool = False
    ingest_window: str = ""
    db_dir: str = "db"

    # --- Published signals API (GCS mode) ---
    api_prefer_published_signals: bool = True
    api_allow_curated_fallback: bool = False
    api_allow_local_db_fallback: bool = False
    allow_inline_scan: bool = False
    published_max_age_minutes: int = 180

    # --- Local APScheduler (disable when Cloud Run owns batch compute) ---
    scheduler_enabled: bool = True

    # --- Universe Filtering ---
    min_market_cap_millions: float = 4000.0
    min_avg_volume: int = 500_000
    min_stock_price: float = 15.0

    # --- Market Cap Policy ---
    allow_missing_market_cap: bool = True

    # --- Institutional Ownership ---
    min_institutional_pct: float = 0.40
    min_institutional_pct_stock_buy: float = 0.50
    institutional_batch_size: int = 20
    institutional_max_retries: int = 2

    # --- Conviction Engine ---
    ema_fast_period: int = 8
    ema_slow_period: int = 21
    pullback_proximity_pct: float = 2.0
    max_extension_pct: float = 3.0
    min_days_above_emas: int = 5
    max_days_above_emas: int = 10
    bootstrap_days: int = 120

    # --- Deep Dip / Oversold Detection ---
    oversold_dip_pct_21ema: float = 5.0
    oversold_dip_pct_50ema: float = 5.0
    oversold_min_prior_uptrend: int = 10

    # --- CSP RSI Gate ---
    csp_max_rsi: float = 0.0  # 0 = disabled; e.g. 70 blocks overbought tickers

    # --- Pullback CSP ---
    pullback_csp_enabled: bool = True
    min_prior_streak: int = 5
    pullback_strike_offset_pct: float = 5.0
    pullback_strike_ceiling_pct: float = 1.0

    # --- Expiration Strategy ---
    earliest_expiration_only: bool = False

    # --- Capital ---
    available_capital: float = 100_000.0

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
    csp_target_dte_min: int = 1
    csp_target_dte_max: int = 45
    cc_target_dte_min: int = 3
    cc_target_dte_max: int = 14
    min_annualized_return_pct: float = 15.0

    # --- Options Scan ---
    max_expiration_dates: int = 2
    expiration_mode: str = "friday_target"
    strike_range_pct: float = 15.0
    llm_concurrency: int = 5
    csp_strike_preference: str = "near_21ema"
    scanner_llm_enabled: bool = False
    min_scan_dte: int = 5
    target_dte_sweet_spot: int = 14

    # --- CSP Quality Filters ---
    csp_min_bid: float = 0.50
    csp_min_premium_pct: float = 0.5
    csp_min_volume: int = 10
    csp_min_oi: int = 50

    # --- Candidate Ranking ---
    ranking_mode: str = "legacy"
    ranking_weight_conviction: float = 1.0
    ranking_weight_ema_proximity: float = 1.0
    ranking_weight_trend_persistence: float = 0.8
    ranking_weight_liquidity: float = 0.6

    # --- Regime Scaling ---
    regime_scaling_enabled: bool = False
    regime_vol_normal_threshold: float = 0.20
    regime_vol_high_threshold: float = 0.30

    # --- Overlap Policy ---
    overlap_policy_enabled: bool = False
    overlap_net_exposure_cap_pct: float = 25.0
    overlap_small_add_max_pct: float = 15.0

    # --- Pre-Allocator Pool ---
    pre_allocator_pool_size: int = 0

    # --- Scan Persistence ---
    scan_retention_count: int = 5

    # --- Conviction Batch ---
    conviction_batch_min_market_cap_millions: float = 500.0
    conviction_batch_min_price: float = 5.0
    conviction_batch_min_avg_volume: int = 500_000
    conviction_snapshot_retention_days: int = 90

    # --- Stock Deep Dive Batch (v2 precompute) ---
    deep_dive_batch_enabled: bool = True
    deep_dive_batch_min_market_cap_millions: float = 1000.0
    # Serve a precomputed payload as "fresh" when its as_of_date is within N
    # trading sessions of the latest OHLCV session; otherwise recompute
    # inline (or serve stale in cloud mode when inline compute is blocked).
    deep_dive_max_staleness_sessions: int = 2

    # v3 Stock Screener ("Diamond Finder") — universe-wide compact index batch
    screener_index_batch_enabled: bool = True
    screener_index_min_market_cap_millions: float = 1000.0

    # --- Workflow ---
    morning_scan_time: str = "09:35"
    order_monitor_interval_min: int = 15
    midday_review_time: str = "12:30"
    eod_journal_time: str = "15:50"

    # --- Options Chain Snapshots ---
    options_snapshot_enabled: bool = True
    options_snapshot_time: str = "16:10"
    options_snapshot_max_expirations: int = 2
    options_snapshot_min_dte: int = 1
    options_snapshot_max_dte: int = 45
    options_snapshot_concurrency: int = 10
    options_snapshot_rpm: int = 120
    options_snapshot_min_market_cap: float = 4e9
    options_snapshot_max_tickers: int = 500

    # --- Candidate universe (cloud metadata-first filtering) ---
    options_candidate_max_tickers: int = 500
    stocks_derived_max_tickers: int = 3000
    require_optionable: bool = True

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

    # --- Observability (env-only) ---
    log_level: str = "INFO"
    gcp_project_id: str = ""
    otel_service_name: str = "tyche-options"
    otel_enabled: bool = True

    # --- Massive S3 flat files (env-only) ---
    massive_s3_access_key: str = ""
    massive_s3_secret_key: str = ""
    massive_s3_url: str = "https://files.massive.com"
    massive_s3_bucket: str = "flatfiles"

    # --- Finnhub (env-only) ---
    finnhub_api_key: str = ""

    # --- News Pipeline ---
    news_ingestion_enabled: bool = False
    news_finnhub_enabled: bool = True
    news_ingest_interval_minutes: int = 240
    news_classify_workers: int = 2
    news_classify_rpm: int = 25
    news_lookback_hours: int = 48
    news_risk_threshold: float = -0.3

    # --- ML Retrain ---
    ml_retrain_enabled: bool = True
    ml_retrain_day_of_month: int = 1
    ml_retrain_time: str = "02:00"

    # --- Automated Data Pipelines ---
    flatfile_ingest_enabled: bool = True
    flatfile_ingest_time: str = "07:00"
    # Daily OHLCV refresh runs this many minutes before ``flatfile_ingest_time``
    # so grouped-daily bars land before the S3 options flatfile pipeline.
    ohlcv_refresh_offset_minutes: int = 30
    flatfile_ingest_min_market_cap: float = 1_000_000_000
    conviction_batch_after_ohlcv: bool = True
    alpha_batch_enabled: bool = True
    # When true, run the directional alpha batch immediately after the nightly
    # flatfile ingest completes (OHLCV refresh runs 30 min earlier). When false,
    # falls back to the standalone 4:20 PM ET weekday cron.
    alpha_batch_after_flatfile: bool = True
    # Directional Alpha BUILD net: the widest market-cap floor the nightly batch
    # computes, so the page control can explore down to here. Common-stock only
    # (no warrants/units/ADRs). The page defaults to a $1B view and can filter
    # upward; set this lower only to widen what the page can reveal.
    alpha_min_market_cap_millions: float = 250.0
    # When true, the alpha batch ALSO scores the "sustained" big-move models
    # (``big_move_sustained_*`` — require the move to still hold at the end of
    # the horizon, not just an intra-window peak) and writes a second snapshot.
    # The Directional Alpha page exposes a Peak/Sustained toggle to compare them
    # live. When false, only the legacy "peak" snapshot is produced.
    alpha_sustained_enabled: bool = True
    # --- Directional Alpha discovery mode (gated; conservative path when off) ---
    alpha_discovery_enabled: bool = False
    alpha_percentile_signals_enabled: bool = False
    alpha_demand_adjusted_extension_enabled: bool = False
    alpha_peer_tier_normalization_enabled: bool = False
    alpha_class_weighting_enabled: bool = True
    alpha_purged_walk_forward_enabled: bool = True
    alpha_discovery_train_min_market_cap_millions: float = 250.0
    alpha_demand_mult_ceil_discovery: float = 1.45
    alpha_discovery_snapshot_enabled: bool = False
    bridge_tradier_iv_enabled: bool = True
    correlation_refresh_enabled: bool = True
    etf_refresh_enabled: bool = True
    quarterly_meta_refresh_enabled: bool = True
    weekly_meta_refresh_enabled: bool = True

    # --- Demand data (fundamentals, estimates, short interest) ---
    # Powers the Demand Conviction (Directional Alpha v2) engine. Each store
    # degrades gracefully when its source/credentials are unavailable.
    demand_data_enabled: bool = True
    fundamentals_refresh_enabled: bool = True
    estimates_refresh_enabled: bool = True
    short_interest_refresh_enabled: bool = True
    # Demand catalysts from Benzinga Corporate Guidance (via Massive/Polygon key).
    guidance_refresh_enabled: bool = True
    # Fundamentals source: "finnhub" (Fundamental-1 statements) or "polygon"
    # (Massive Financials & Ratios). Finnhub is preferred — its standardized
    # statements carry the quarterly revenue series D-FUND needs.
    fundamentals_source: str = "finnhub"
    # Daily window (ET) after OHLCV refresh; fundamentals/SI change slowly,
    # estimates are snapshotted daily to build a revision time series.
    demand_data_refresh_time: str = "03:00"
    demand_data_min_market_cap_millions: float = 250.0
    demand_data_concurrency: int = 8
    # Finnhub request rate (calls/min) for demand-data ingestion. Free tier is
    # 60; paid Fundamental-1/Estimates-1 plans allow 300. Default 250 keeps a
    # safety margin under the 300 SLA. The client backs off on any 429, so
    # over-setting self-heals (just slower). Override per-run via --finnhub-rpm.
    finnhub_rate_limit_rpm: int = 250

    # --- EDGAR Pipeline ---
    edgar_ingestion_enabled: bool = False
    edgar_user_agent_email: str = ""
    edgar_ingest_interval_minutes: int = 1440
    edgar_lookback_days: int = 30

    # --- Database (env-only) ---
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


# ---------------------------------------------------------------------------
# 3.  Factory, cache, and migration
# ---------------------------------------------------------------------------

_settings_cache: TycheSettings | None = None
_config_store: ConfigStore | None = None


def get_config_store() -> ConfigStore:
    """Lazily create / return the singleton ConfigStore.

    Uses ``db_dir`` from ``_EnvSettings`` to locate ``config.db``.
    """
    global _config_store
    if _config_store is None:
        env = _EnvSettings()
        db_path = Path(env.db_dir) / "config.db"
        _config_store = ConfigStore(db_path)
    return _config_store


def _try_json_parse(raw: str) -> Any:
    """Best-effort JSON parse for .env migration values."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _migrate_env_to_config_db(store: ConfigStore) -> None:
    """One-time migration: seed config.db from .env values.

    Only runs when config.db is empty (first startup with new code).
    Reads ``.env`` as text, extracts ``TYCHE_*`` keys for config fields,
    and writes them to the store.
    """
    if not store.is_empty:
        return

    env_path = Path(".env")
    if not env_path.exists():
        return

    config_field_names = set(TycheSettings.model_fields.keys()) - _ENV_ONLY_FIELDS
    migrated: dict[str, Any] = {}

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw_value = stripped.partition("=")
        key = key.strip()

        if not key.startswith("TYCHE_"):
            continue

        field_name = key.removeprefix("TYCHE_").lower()
        if field_name not in config_field_names:
            continue

        parsed = _try_json_parse(raw_value)
        migrated[field_name] = parsed

    if migrated:
        store.set_many(migrated)
        logger.info(
            "config_migrated_from_env",
            fields=list(migrated.keys()),
            count=len(migrated),
        )


def _build_settings(env: _EnvSettings, db_values: dict[str, Any]) -> TycheSettings:
    """Merge env + config.db + defaults into a TycheSettings instance."""
    kwargs: dict[str, Any] = {}

    env_dict = env.model_dump()
    for field_name in _ENV_ONLY_FIELDS:
        if field_name in env_dict:
            kwargs[field_name] = env_dict[field_name]

    for field_name in TycheSettings.model_fields:
        if field_name in _ENV_ONLY_FIELDS:
            continue
        if field_name in db_values:
            kwargs[field_name] = db_values[field_name]

    settings = TycheSettings(**kwargs)
    # GCS view-only laptops should not duplicate Cloud Run nightly jobs.
    if settings.data_backend == "gcs" and "scheduler_enabled" not in db_values:
        settings = settings.model_copy(update={"scheduler_enabled": False})
    return settings


def get_settings() -> TycheSettings:
    """Factory for the application-wide settings singleton.

    First call triggers migration from ``.env`` if ``config.db`` is empty.
    Subsequent calls return the cached instance until
    :func:`invalidate_settings` is called.
    """
    global _settings_cache
    if _settings_cache is None:
        store = get_config_store()
        if store.is_empty:
            _migrate_env_to_config_db(store)
        env = _EnvSettings()
        db_values = store.get_all()
        _settings_cache = _build_settings(env, db_values)
    return _settings_cache


def invalidate_settings() -> None:
    """Clear the cached settings so the next ``get_settings()`` re-reads
    from env + config.db.  Call after writing to config.db.
    """
    global _settings_cache
    _settings_cache = None
