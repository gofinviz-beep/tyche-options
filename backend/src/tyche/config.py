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
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from tyche.persistence.config_store import ConfigStore

logger = structlog.get_logger()

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
        "db_dir",
        "polygon_base_url",
        "polygon_rate_limit_rpm",
        "polygon_market_cap_concurrency",
        "log_level",
        "otel_service_name",
        "otel_enabled",
        "notification_smtp_host",
        "notification_smtp_port",
    }
)


class _EnvSettings(BaseSettings):
    """Read-only slice loaded exclusively from env vars / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TYCHE_",
        env_file_encoding="utf-8",
        case_sensitive=False,
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
    db_dir: str = "db"
    polygon_base_url: str = "https://api.polygon.io"
    polygon_rate_limit_rpm: int = 500
    polygon_market_cap_concurrency: int = 20
    log_level: str = "INFO"
    otel_service_name: str = "tyche-options"
    otel_enabled: bool = True
    notification_smtp_host: str = "smtp.gmail.com"
    notification_smtp_port: int = 587


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
    db_dir: str = "db"

    # --- Universe Filtering ---
    min_market_cap_millions: float = 5000.0
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

    # --- Pullback CSP ---
    pullback_csp_enabled: bool = True
    min_prior_streak: int = 5
    pullback_strike_offset_pct: float = 5.0
    pullback_strike_ceiling_pct: float = 1.0

    # --- Expiration Strategy ---
    earliest_expiration_only: bool = True

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
    max_expiration_dates: int = 1
    expiration_mode: str = "friday_target"
    strike_range_pct: float = 15.0
    llm_concurrency: int = 5
    csp_strike_preference: str = "near_21ema"

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
    options_snapshot_min_market_cap: float = 5e9

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

    return TycheSettings(**kwargs)


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
