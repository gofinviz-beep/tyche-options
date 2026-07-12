"""FastAPI dependency injection — provides shared service instances."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import Depends

from tyche.analysis.agent import AnalysisAgent
from tyche.analysis.client import GeminiClient

if TYPE_CHECKING:
    from tyche.analysis.news_classifier import NewsClassifier
    from tyche.market_data.edgar import EdgarClient
    from tyche.market_data.filing_store import Filing8KStore, InsiderTxStore
    from tyche.market_data.finnhub import FinnhubClient
    from tyche.market_data.news_store import NewsArticleStore
from tyche.broker.base import BrokerClient
from tyche.broker.mock import MockBroker
from tyche.config import TycheSettings, get_settings
from tyche.conviction.engine import ConvictionEngine
from tyche.conviction.features import ConvictionFeatureEngine
from tyche.conviction.csp_policy import CSPEligibilityPolicy
from tyche.market_data.data_store import ConvictionSignalStore, OHLCVStore, TickerMetaStore
from tyche.market_data.derived_store import DerivedMetricsStore
from tyche.market_data.earnings import EarningsCalendarClient
from tyche.market_data.economic_calendar import EconomicCalendar
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
_economic_calendar: EconomicCalendar | None = None
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
_derived_store: DerivedMetricsStore | None = None
_portfolio_allocator: PortfolioAllocator | None = None
_csp_predictor: Any | None = None
_breakout_predictor: Any | None = None
_alpha_engine: Any | None = None
_fundamentals_store: Any | None = None
_estimates_store: Any | None = None
_short_interest_store: Any | None = None
_catalyst_store: Any | None = None
_policy_calendar: Any | None = None
_supply_chain_graph: Any | None = None
_deep_dive_store: Any | None = None
_screener_index_store: Any | None = None


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


def get_economic_calendar() -> EconomicCalendar:
    """Provide the economic/macro event calendar (static, no API needed)."""
    global _economic_calendar
    if _economic_calendar is None:
        _economic_calendar = EconomicCalendar()
    return _economic_calendar


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


def get_derived_store(
    settings: TycheSettings = Depends(get_settings),
) -> DerivedMetricsStore:
    """Provide the derived metrics store (IV rank, VRP, etc.)."""
    global _derived_store
    if _derived_store is None:
        _derived_store = DerivedMetricsStore(data_dir=settings.data_dir)
        logger.info(
            "derived_store_initialized",
            path=str(_derived_store.store_dir),
        )
    return _derived_store


def get_fundamentals_store(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the quarterly fundamentals store."""
    global _fundamentals_store
    if _fundamentals_store is None:
        from tyche.market_data.fundamentals_store import FundamentalsStore

        _fundamentals_store = FundamentalsStore(data_dir=settings.data_dir)
        logger.info("fundamentals_store_initialized")
    return _fundamentals_store


def get_estimates_store(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the analyst estimates/revisions/surprises store."""
    global _estimates_store
    if _estimates_store is None:
        from tyche.market_data.estimates_store import EstimatesStore

        _estimates_store = EstimatesStore(data_dir=settings.data_dir)
        logger.info("estimates_store_initialized")
    return _estimates_store


def get_short_interest_store(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the short-interest history store."""
    global _short_interest_store
    if _short_interest_store is None:
        from tyche.market_data.short_interest_store import ShortInterestStore

        _short_interest_store = ShortInterestStore(data_dir=settings.data_dir)
        logger.info("short_interest_store_initialized")
    return _short_interest_store


def get_catalyst_store(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the demand-catalyst / policy signal store (D-CAT / D-POL)."""
    global _catalyst_store
    if _catalyst_store is None:
        from tyche.market_data.catalyst_store import CatalystSignalStore

        _catalyst_store = CatalystSignalStore(data_dir=settings.data_dir)
        logger.info("catalyst_store_initialized")
    return _catalyst_store


def get_policy_calendar(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the structural policy/capex tailwind calendar (D-POL)."""
    global _policy_calendar
    if _policy_calendar is None:
        from tyche.market_data.policy_calendar import PolicyEventCalendar

        _policy_calendar = PolicyEventCalendar()
        logger.info("policy_calendar_initialized")
    return _policy_calendar


def get_supply_chain_graph(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the curated supply-chain demand-propagation graph (D-GRAPH)."""
    global _supply_chain_graph
    if _supply_chain_graph is None:
        from tyche.market_data.supply_chain_graph import SupplyChainGraph

        _supply_chain_graph = SupplyChainGraph()
        logger.info("supply_chain_graph_initialized")
    return _supply_chain_graph


def get_estimates_finnhub(
    settings: TycheSettings = Depends(get_settings),
) -> "FinnhubClient | None":
    """Provide a Finnhub client for estimates (independent of the news flag).

    Returns ``None`` when no API key is configured.
    """
    if not settings.finnhub_api_key:
        return None
    from tyche.market_data.finnhub import FinnhubClient

    return FinnhubClient(api_key=settings.finnhub_api_key)


def get_feature_engine(
    settings: TycheSettings = Depends(get_settings),
) -> ConvictionFeatureEngine:
    """Provide the standalone EMA feature engine (no CSP policy)."""
    global _feature_engine
    if _feature_engine is None:
        signal_store = get_conviction_signal_store(settings)
        derived = get_derived_store(settings)
        _feature_engine = ConvictionFeatureEngine(
            ema_fast=settings.ema_fast_period,
            ema_slow=settings.ema_slow_period,
            pullback_proximity_pct=settings.pullback_proximity_pct,
            signal_store=signal_store,
            derived_store=derived,
            oversold_dip_pct_21ema=settings.oversold_dip_pct_21ema,
            oversold_dip_pct_50ema=settings.oversold_dip_pct_50ema,
            oversold_min_prior_uptrend=settings.oversold_min_prior_uptrend,
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
            max_rsi=settings.csp_max_rsi,
        )
        logger.info("csp_policy_initialized")
    return _csp_policy


def get_csp_safety_predictor(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the XGBoost CSP safety predictor (None if no model artifact)."""
    global _csp_predictor
    if _csp_predictor is None:
        try:
            from tyche.ml.inference import CSPSafetyPredictor
            from tyche.storage.paths import storage_context_from_settings

            ctx = storage_context_from_settings(settings)
            predictor = CSPSafetyPredictor(data_dir=settings.data_dir, ctx=ctx)
            if predictor.is_available:
                _csp_predictor = predictor
                logger.info("csp_safety_predictor_initialized", info=predictor.model_info)
            else:
                logger.info("csp_safety_predictor_unavailable", reason="no_model_artifact")
        except ImportError:
            logger.info("csp_safety_predictor_unavailable", reason="ml_deps_not_installed")
    return _csp_predictor


def get_breakout_predictor(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the XGBoost big-move predictor (None if no model artifacts)."""
    global _breakout_predictor
    if _breakout_predictor is None:
        try:
            from tyche.ml.breakout import BreakoutPredictor
            from tyche.storage.paths import storage_context_from_settings

            ctx = storage_context_from_settings(settings)
            predictor = BreakoutPredictor(data_dir=settings.data_dir, ctx=ctx)
            if predictor.is_available:
                _breakout_predictor = predictor
                logger.info("breakout_predictor_initialized", targets=predictor.targets)
            else:
                logger.info("breakout_predictor_unavailable", reason="no_model_artifact")
        except ImportError:
            logger.info("breakout_predictor_unavailable", reason="ml_deps_not_installed")
    return _breakout_predictor


def get_alpha_engine(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the directional AlphaScoreEngine singleton."""
    global _alpha_engine
    if _alpha_engine is None:
        from tyche.strategy.alpha_engine import build_alpha_score_engine

        _alpha_engine = build_alpha_score_engine(
            discovery_enabled=settings.alpha_discovery_enabled,
            percentile_signals=settings.alpha_percentile_signals_enabled,
            demand_adjusted_extension=settings.alpha_demand_adjusted_extension_enabled,
            demand_mult_ceil_discovery=settings.alpha_demand_mult_ceil_discovery,
        )
        logger.info(
            "alpha_engine_initialized",
            discovery=settings.alpha_discovery_enabled,
        )
    return _alpha_engine


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
        derived = get_derived_store(settings)
        predictor = get_csp_safety_predictor(settings)
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
            derived_store=derived,
            csp_predictor=predictor,
            oversold_dip_pct_21ema=settings.oversold_dip_pct_21ema,
            oversold_dip_pct_50ema=settings.oversold_dip_pct_50ema,
            oversold_min_prior_uptrend=settings.oversold_min_prior_uptrend,
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


_news_article_store: "NewsArticleStore | None" = None
_finnhub_client: "FinnhubClient | None" = None
_news_classifier: "NewsClassifier | None" = None
_edgar_client: "EdgarClient | None" = None
_filing_8k_store: "Filing8KStore | None" = None
_insider_tx_store: "InsiderTxStore | None" = None


def get_news_article_store(
    settings: TycheSettings = Depends(get_settings),
) -> "NewsArticleStore":
    """Provide the news article Parquet store."""
    global _news_article_store
    if _news_article_store is None:
        from tyche.market_data.news_store import NewsArticleStore

        _news_article_store = NewsArticleStore(data_dir=settings.data_dir)
        logger.info("news_article_store_initialized")
    return _news_article_store


def get_finnhub(
    settings: TycheSettings = Depends(get_settings),
) -> "FinnhubClient | None":
    """Provide the Finnhub client (None if no API key or disabled)."""
    global _finnhub_client
    if _finnhub_client is None and settings.finnhub_api_key and settings.news_finnhub_enabled:
        from tyche.market_data.finnhub import FinnhubClient

        _finnhub_client = FinnhubClient(api_key=settings.finnhub_api_key)
        logger.info("finnhub_client_initialized")
    return _finnhub_client


def get_news_classifier(
    gemini: GeminiClient | None = Depends(get_gemini),
    settings: TycheSettings = Depends(get_settings),
) -> "NewsClassifier | None":
    """Provide the news classifier (None if no Gemini configured)."""
    global _news_classifier
    if _news_classifier is None and gemini is not None:
        from tyche.analysis.news_classifier import NewsClassifier

        _news_classifier = NewsClassifier(
            gemini=gemini,
            classify_model=settings.gemini_model_classify,
            workers=settings.news_classify_workers,
            rpm=settings.news_classify_rpm,
        )
        logger.info("news_classifier_initialized")
    return _news_classifier


def get_edgar_client(
    settings: TycheSettings = Depends(get_settings),
) -> "EdgarClient | None":
    """Provide the EDGAR client (None if no user-agent email configured)."""
    global _edgar_client
    if _edgar_client is None and settings.edgar_user_agent_email:
        from tyche.market_data.edgar import EdgarClient

        _edgar_client = EdgarClient(
            user_agent_email=settings.edgar_user_agent_email,
        )
        logger.info("edgar_client_initialized")
    return _edgar_client


def get_filing_8k_store(
    settings: TycheSettings = Depends(get_settings),
) -> "Filing8KStore":
    """Provide the 8-K filing Parquet store."""
    global _filing_8k_store
    if _filing_8k_store is None:
        from tyche.market_data.filing_store import Filing8KStore

        _filing_8k_store = Filing8KStore(data_dir=settings.data_dir)
        logger.info("filing_8k_store_initialized")
    return _filing_8k_store


def get_insider_tx_store(
    settings: TycheSettings = Depends(get_settings),
) -> "InsiderTxStore":
    """Provide the insider transaction Parquet store."""
    global _insider_tx_store
    if _insider_tx_store is None:
        from tyche.market_data.filing_store import InsiderTxStore

        _insider_tx_store = InsiderTxStore(data_dir=settings.data_dir)
        logger.info("insider_tx_store_initialized")
    return _insider_tx_store


def get_deep_dive_store(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the per-ticker precomputed Stock Deep Dive Parquet store."""
    global _deep_dive_store
    if _deep_dive_store is None:
        from tyche.market_data.deep_dive_store import DeepDiveStore

        _deep_dive_store = DeepDiveStore(data_dir=settings.data_dir)
        logger.info("deep_dive_store_initialized")
    return _deep_dive_store


def get_screener_index_store(
    settings: TycheSettings = Depends(get_settings),
) -> Any:
    """Provide the universe-wide Stock Screener index store (v3)."""
    global _screener_index_store
    if _screener_index_store is None:
        from tyche.market_data.screener_index_store import ScreenerIndexStore

        _screener_index_store = ScreenerIndexStore(data_dir=settings.data_dir)
        logger.info("screener_index_store_initialized")
    return _screener_index_store


def reset_all() -> None:
    """Reset all singleton instances and flush stale caches.

    Called on config changes (PATCH /system/config) and in tests.
    Clears in-memory caches AND the on-disk Parquet signal store so the
    next singleton creation doesn't re-warm from stale data.
    """
    global _broker_instance, _gemini_instance, _analysis_agent
    global _risk_engine, _earnings_client, _strategy_engine
    global _universe_builder, _scheduler
    global _polygon_client, _data_store, _conviction_engine
    global _active_monitor, _ticker_meta_store, _portfolio_allocator
    global _conviction_signal_store, _feature_engine, _csp_policy
    global _derived_store, _economic_calendar
    global _news_article_store, _finnhub_client, _news_classifier
    global _edgar_client, _filing_8k_store, _insider_tx_store
    global _csp_predictor, _breakout_predictor, _alpha_engine
    global _catalyst_store, _policy_calendar, _supply_chain_graph
    global _deep_dive_store, _screener_index_store

    from tyche.api.routes.conviction import invalidate_conviction_cache
    invalidate_conviction_cache(clear_engine=True)

    from tyche.api.routes.deep_dive import invalidate_deep_dive_cache
    invalidate_deep_dive_cache()

    _breakout_predictor = None
    _alpha_engine = None

    if _feature_engine is not None and _conviction_engine is None:
        _feature_engine.invalidate_cache()

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
    _derived_store = None
    _portfolio_allocator = None
    _economic_calendar = None
    _news_article_store = None
    _finnhub_client = None
    _news_classifier = None
    _edgar_client = None
    _filing_8k_store = None
    _insider_tx_store = None
    _csp_predictor = None
    _catalyst_store = None
    _policy_calendar = None
    _supply_chain_graph = None
    _deep_dive_store = None
    _screener_index_store = None
