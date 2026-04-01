"""FastAPI dependency injection — provides shared service instances."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import structlog
from fastapi import Depends

from tyche.analysis.agent import AnalysisAgent
from tyche.analysis.client import GeminiClient
from tyche.broker.base import BrokerClient
from tyche.broker.mock import MockBroker
from tyche.config import TycheSettings, get_settings
from tyche.conviction.engine import ConvictionEngine
from tyche.conviction.features import ConvictionFeatureEngine
from tyche.conviction.csp_policy import CSPEligibilityPolicy
from tyche.market_data.data_store import ConvictionSignalStore, OHLCVStore, TickerMetaStore
from tyche.market_data.earnings import EarningsCalendarClient
from tyche.market_data.polygon import PolygonClient
from tyche.market_data.universe import UniverseBuilder
from tyche.risk.engine import RiskEngine
from tyche.workflow.active_monitor import ActiveMonitor
from tyche.risk.rules import (
    AssignmentExposureRule,
    CashCollateralRule,
    EarningsProximityRule,
    KillSwitchRule,
    MaxConcentrationRule,
    MaxContractsRule,
    MaxDailyTradesRule,
    MaxOpenPositionsRule,
    StrategyWhitelistRule,
)
from tyche.strategy.allocator import PortfolioAllocator
from tyche.strategy.engine import StrategyEngine
from tyche.workflow.scheduler import WorkflowScheduler

logger = structlog.get_logger()

_broker_instance: BrokerClient | None = None
_gemini_instance: GeminiClient | None = None
_analysis_agent: AnalysisAgent | None = None
_risk_engine: RiskEngine | None = None
_earnings_client: EarningsCalendarClient | None = None
_strategy_engine: StrategyEngine | None = None
_universe_builder: UniverseBuilder | None = None
_scheduler: WorkflowScheduler | None = None
_polygon_client: PolygonClient | None = None
_data_store: OHLCVStore | None = None
_conviction_engine: ConvictionEngine | None = None
_feature_engine: ConvictionFeatureEngine | None = None
_csp_policy: CSPEligibilityPolicy | None = None
_active_monitor: ActiveMonitor | None = None
_ticker_meta_store: TickerMetaStore | None = None
_conviction_signal_store: ConvictionSignalStore | None = None
_portfolio_allocator: PortfolioAllocator | None = None


def get_broker(settings: TycheSettings = Depends(get_settings)) -> BrokerClient:
    """Provide the broker client.

    Hybrid architecture:
    - Tradier (production): real-time quotes, option chains, account ops
    - MockBroker: fallback when Tradier not configured (sandbox/dev)
    - Polygon is used separately for conviction/screening (not wired here)
    """
    global _broker_instance
    if _broker_instance is None:
        if not settings.tradier_sandbox and settings.tradier_api_token:
            from tyche.broker.tradier.client import TradierClient

            _broker_instance = TradierClient(
                api_token=settings.tradier_api_token,
                account_id=settings.tradier_account_id,
                base_url=settings.broker_base_url,
                cache_ttl=settings.broker_cache_ttl,
            )
            logger.info("broker_initialized", type="tradier_production")
        else:
            _broker_instance = MockBroker()
            logger.info("broker_initialized", type="mock")
    return _broker_instance


def get_gemini(settings: TycheSettings = Depends(get_settings)) -> GeminiClient | None:
    """Provide the Gemini client (None if no API key configured)."""
    global _gemini_instance
    if _gemini_instance is None and settings.gemini_api_key:
        _gemini_instance = GeminiClient(
            api_key=settings.gemini_api_key,
            model_fast=settings.gemini_model_fast,
            model_deep=settings.gemini_model_deep,
        )
        logger.info("gemini_initialized")
    return _gemini_instance


def get_analysis_agent(
    gemini: GeminiClient | None = Depends(get_gemini),
) -> AnalysisAgent | None:
    """Provide the analysis agent (None if no LLM configured)."""
    global _analysis_agent
    if _analysis_agent is None and gemini is not None:
        _analysis_agent = AnalysisAgent(gemini)
    return _analysis_agent


def get_risk_engine(
    settings: TycheSettings = Depends(get_settings),
) -> RiskEngine:
    """Provide the risk engine with all configured rules."""
    global _risk_engine
    if _risk_engine is None:
        rules = [
            KillSwitchRule(settings=settings),
            CashCollateralRule(),
            MaxContractsRule(max_contracts=settings.max_contracts_per_position),
            MaxConcentrationRule(
                max_pct=settings.max_concentration_per_ticker_pct
            ),
            MaxOpenPositionsRule(max_positions=settings.max_open_positions),
            MaxDailyTradesRule(max_trades=settings.max_new_trades_per_day),
            StrategyWhitelistRule(),
            EarningsProximityRule(),
            AssignmentExposureRule(
                max_pct=settings.max_concentration_per_ticker_pct
            ),
        ]
        _risk_engine = RiskEngine(rules=rules)
        logger.info("risk_engine_initialized", rules=len(rules))
    return _risk_engine


def get_earnings_client(
    settings: TycheSettings = Depends(get_settings),
) -> EarningsCalendarClient | None:
    """Provide the earnings calendar client (free — no paid API needed)."""
    global _earnings_client
    if _earnings_client is None:
        av_key = settings.alpha_vantage_key or settings.earnings_api_key or "demo"
        _earnings_client = EarningsCalendarClient(
            alpha_vantage_key=av_key,
            manual_overrides=settings.earnings_overrides or None,
        )
    return _earnings_client


def get_strategy_engine(
    settings: TycheSettings = Depends(get_settings),
) -> StrategyEngine:
    """Provide the strategy engine."""
    global _strategy_engine
    if _strategy_engine is None:
        from tyche.strategy.strategies.cash_secured_put import CashSecuredPutStrategy
        from tyche.strategy.strategies.covered_call import CoveredCallStrategy

        csp = CashSecuredPutStrategy(
            dte_min=settings.csp_target_dte_min,
            dte_max=settings.csp_target_dte_max,
        )
        cc = CoveredCallStrategy(
            dte_min=settings.cc_target_dte_min,
            dte_max=settings.cc_target_dte_max,
        )
        _strategy_engine = StrategyEngine(csp_strategy=csp, cc_strategy=cc)
    return _strategy_engine


def get_universe_builder(
    settings: TycheSettings = Depends(get_settings),
) -> UniverseBuilder:
    """Provide the universe builder."""
    global _universe_builder
    if _universe_builder is None:
        _universe_builder = UniverseBuilder(
            min_market_cap_millions=settings.min_market_cap_millions,
            min_avg_volume=settings.min_avg_volume,
            min_price=settings.min_stock_price,
        )
    return _universe_builder


def get_scheduler() -> WorkflowScheduler:
    """Provide the workflow scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = WorkflowScheduler()
    return _scheduler


def get_polygon(
    settings: TycheSettings = Depends(get_settings),
) -> PolygonClient | None:
    """Provide the Polygon.io / Massive.com client (None if no key)."""
    global _polygon_client
    if _polygon_client is None and settings.polygon_api_key:
        _polygon_client = PolygonClient(
            api_key=settings.polygon_api_key,
            base_url=settings.polygon_base_url,
            rate_limit_rpm=settings.polygon_rate_limit_rpm,
        )
        logger.info("polygon_client_initialized")
    return _polygon_client


def get_data_store(
    settings: TycheSettings = Depends(get_settings),
) -> OHLCVStore:
    """Provide the local OHLCV data store."""
    global _data_store
    if _data_store is None:
        _data_store = OHLCVStore(data_dir=settings.data_dir)
        logger.info(
            "data_store_initialized",
            path=str(_data_store.store_dir),
            exists=_data_store.exists,
        )
    return _data_store


def get_ticker_meta_store(
    settings: TycheSettings = Depends(get_settings),
) -> TickerMetaStore:
    """Provide the ticker metadata store (market cap, exchange, type)."""
    global _ticker_meta_store
    if _ticker_meta_store is None:
        _ticker_meta_store = TickerMetaStore(data_dir=settings.data_dir)
        logger.info(
            "ticker_meta_store_initialized",
            path=str(_ticker_meta_store.parquet_path),
            exists=_ticker_meta_store.exists,
        )
    return _ticker_meta_store


def get_conviction_signal_store(
    settings: TycheSettings = Depends(get_settings),
) -> ConvictionSignalStore:
    """Provide the Parquet-backed conviction signal disk cache."""
    global _conviction_signal_store
    if _conviction_signal_store is None:
        _conviction_signal_store = ConvictionSignalStore(data_dir=settings.data_dir)
        logger.info(
            "conviction_signal_store_initialized",
            path=str(_conviction_signal_store.parquet_path),
            exists=_conviction_signal_store.exists,
        )
    return _conviction_signal_store


def get_feature_engine(
    settings: TycheSettings = Depends(get_settings),
) -> ConvictionFeatureEngine:
    """Provide the standalone EMA feature engine (no CSP policy)."""
    global _feature_engine
    if _feature_engine is None:
        signal_store = get_conviction_signal_store(settings)
        _feature_engine = ConvictionFeatureEngine(
            ema_fast=settings.ema_fast_period,
            ema_slow=settings.ema_slow_period,
            pullback_proximity_pct=settings.pullback_proximity_pct,
            signal_store=signal_store,
        )
        logger.info(
            "feature_engine_initialized",
            fast=settings.ema_fast_period,
            slow=settings.ema_slow_period,
            disk_cache=signal_store.exists,
        )
    return _feature_engine


def get_csp_policy(
    settings: TycheSettings = Depends(get_settings),
) -> CSPEligibilityPolicy:
    """Provide the stateless CSP eligibility policy."""
    global _csp_policy
    if _csp_policy is None:
        _csp_policy = CSPEligibilityPolicy(
            max_extension_pct=settings.max_extension_pct,
            min_days_above_emas=settings.min_days_above_emas,
            max_days_above_emas=settings.max_days_above_emas,
            pullback_csp_enabled=settings.pullback_csp_enabled,
            min_prior_streak=settings.min_prior_streak,
        )
        logger.info("csp_policy_initialized")
    return _csp_policy


def get_conviction_engine(
    settings: TycheSettings = Depends(get_settings),
) -> ConvictionEngine:
    """Provide the 8/21 EMA conviction engine with disk-backed cache.

    The wrapper composes ``get_feature_engine()`` and ``get_csp_policy()``
    internally, so all three singletons share config and cache.
    """
    global _conviction_engine
    if _conviction_engine is None:
        signal_store = get_conviction_signal_store(settings)
        _conviction_engine = ConvictionEngine(
            ema_fast=settings.ema_fast_period,
            ema_slow=settings.ema_slow_period,
            pullback_proximity_pct=settings.pullback_proximity_pct,
            max_extension_pct=settings.max_extension_pct,
            min_days_above_emas=settings.min_days_above_emas,
            max_days_above_emas=settings.max_days_above_emas,
            pullback_csp_enabled=settings.pullback_csp_enabled,
            min_prior_streak=settings.min_prior_streak,
            signal_store=signal_store,
        )
        logger.info(
            "conviction_engine_initialized",
            fast=settings.ema_fast_period,
            slow=settings.ema_slow_period,
            disk_cache=signal_store.exists,
        )
    return _conviction_engine


def get_active_monitor(
    broker: BrokerClient = Depends(get_broker),
) -> ActiveMonitor:
    """Provide the active position/order monitor."""
    global _active_monitor
    if _active_monitor is None:
        _active_monitor = ActiveMonitor(broker=broker)
        logger.info("active_monitor_initialized")
    return _active_monitor


def get_portfolio_allocator(
    settings: TycheSettings = Depends(get_settings),
) -> PortfolioAllocator:
    """Provide the MILP portfolio allocator."""
    global _portfolio_allocator
    if _portfolio_allocator is None:
        _portfolio_allocator = PortfolioAllocator(
            max_positions=settings.max_open_positions,
            max_contracts_per_position=settings.max_contracts_per_position,
            max_concentration_pct=settings.max_concentration_per_ticker_pct,
            max_extension_pct=settings.max_extension_pct,
        )
        logger.info(
            "portfolio_allocator_initialized",
            max_positions=settings.max_open_positions,
            max_contracts=settings.max_contracts_per_position,
            max_concentration_pct=settings.max_concentration_per_ticker_pct,
        )
    return _portfolio_allocator


def reset_all() -> None:
    """Reset all singleton instances (for testing)."""
    global _broker_instance, _gemini_instance, _analysis_agent
    global _risk_engine, _earnings_client, _strategy_engine
    global _universe_builder, _scheduler
    global _polygon_client, _data_store, _conviction_engine
    global _active_monitor, _ticker_meta_store, _portfolio_allocator
    global _conviction_signal_store, _feature_engine, _csp_policy
    _broker_instance = None
    _gemini_instance = None
    _analysis_agent = None
    _risk_engine = None
    _earnings_client = None
    _strategy_engine = None
    _universe_builder = None
    _scheduler = None
    _polygon_client = None
    _data_store = None
    _conviction_engine = None
    _feature_engine = None
    _csp_policy = None
    _active_monitor = None
    _ticker_meta_store = None
    _conviction_signal_store = None
    _portfolio_allocator = None
