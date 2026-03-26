"""Tests for configuration system."""

from __future__ import annotations

from tyche.config import TycheSettings


def test_default_settings_are_safe() -> None:
    """Verify default settings start in preview-only sandbox mode."""
    settings = TycheSettings(
        tradier_api_token="test",
        tradier_account_id="test",
        gemini_api_key="test",
        tradier_sandbox=True,
    )
    assert settings.tradier_sandbox is True
    assert settings.preview_only_mode is True
    assert settings.max_contracts_per_position == 40
    assert settings.min_market_cap_billions == 1.0


def test_sandbox_base_url() -> None:
    settings = TycheSettings(
        tradier_api_token="test",
        tradier_account_id="test",
        gemini_api_key="test",
        tradier_sandbox=True,
    )
    assert settings.broker_base_url == "https://sandbox.tradier.com/v1"


def test_production_base_url() -> None:
    settings = TycheSettings(
        tradier_api_token="test",
        tradier_account_id="test",
        gemini_api_key="test",
        tradier_sandbox=False,
    )
    assert settings.broker_base_url == "https://api.tradier.com/v1"


def test_custom_base_url_overrides() -> None:
    settings = TycheSettings(
        tradier_api_token="test",
        tradier_account_id="test",
        gemini_api_key="test",
        tradier_base_url="http://localhost:8080/v1",
    )
    assert settings.broker_base_url == "http://localhost:8080/v1"
