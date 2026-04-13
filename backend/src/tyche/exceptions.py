"""Custom exception hierarchy for Tyche Options."""

from __future__ import annotations


class TycheError(Exception):
    """Base exception for all Tyche errors."""


# --- Broker Errors ---


class BrokerError(TycheError):
    """Base for all broker-related errors."""


class BrokerAuthError(BrokerError):
    """Authentication failed with the broker API."""


class BrokerConnectionError(BrokerError):
    """Could not connect to the broker API."""


class BrokerRateLimitError(BrokerError):
    """Broker API rate limit exceeded."""


class BrokerOrderError(BrokerError):
    """Order placement, cancellation, or replacement failed."""


class BrokerDataError(BrokerError):
    """Invalid or missing data returned by the broker."""


# --- Risk Errors ---


class RiskError(TycheError):
    """Base for risk-related errors."""


class RiskRuleViolation(RiskError):
    """A deterministic risk rule was violated."""

    def __init__(self, rule_name: str, reason: str) -> None:
        self.rule_name = rule_name
        self.reason = reason
        super().__init__(f"Risk rule '{rule_name}' violated: {reason}")


class KillSwitchActive(RiskError):
    """The kill switch is active — no live orders allowed."""


class InsufficientCollateral(RiskError):
    """Not enough cash to secure the position."""


# --- Strategy Errors ---


class StrategyError(TycheError):
    """Base for strategy engine errors."""


class NoViableCandidates(StrategyError):
    """No candidates passed the filter pipeline."""


# --- Analysis Errors ---


class AnalysisError(TycheError):
    """Base for LLM analysis errors."""


class LLMResponseError(AnalysisError):
    """LLM returned an unparseable or invalid response."""


class GroundingViolation(AnalysisError):
    """LLM output references data not present in inputs."""


# --- Market Data Errors ---


class MarketDataError(TycheError):
    """Base for external market data errors."""


class EarningsDataUnavailable(MarketDataError):
    """Could not retrieve earnings calendar data."""


class PolygonAPIError(MarketDataError):
    """Error communicating with Polygon.io / Massive.com API."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Polygon API {status_code}: {message}")


class PolygonRateLimitError(PolygonAPIError):
    """Polygon API rate limit exceeded."""

    def __init__(self) -> None:
        super().__init__(429, "Rate limit exceeded")


class InsufficientDataError(MarketDataError):
    """Not enough historical data to compute indicators."""


class FinnhubAPIError(MarketDataError):
    """Error communicating with the Finnhub API."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Finnhub API {status_code}: {message}")


class EdgarAPIError(MarketDataError):
    """Error communicating with the SEC EDGAR API."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"EDGAR API {status_code}: {message}")


class DataStoreError(TycheError):
    """Error reading/writing local data cache."""


# --- News Errors ---


class NewsIngestionError(TycheError):
    """Error during news article ingestion."""


class NewsClassificationError(TycheError):
    """Error during news article classification."""


# --- EDGAR Errors ---


class EdgarIngestionError(TycheError):
    """Error during EDGAR filing ingestion."""


# --- Persistence Errors ---


class PersistenceError(TycheError):
    """Base for database errors."""
