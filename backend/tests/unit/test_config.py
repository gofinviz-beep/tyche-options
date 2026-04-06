"""Tests for configuration system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tyche.config import (
    TycheSettings,
    _EnvSettings,
    _build_settings,
    _migrate_env_to_config_db,
    get_config_store,
    get_settings,
    invalidate_settings,
)
from tyche.persistence.config_store import ConfigStore


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


class TestConfigStore:
    """ConfigStore CRUD operations."""

    def test_empty_store(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path / "config.db")
        assert store.is_empty
        assert store.get_all() == {}
        assert store.get("nonexistent") is None

    def test_set_and_get(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path / "config.db")
        store.set("available_capital", 150_000.0)
        assert store.get("available_capital") == 150_000.0
        assert not store.is_empty

    def test_set_many(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path / "config.db")
        store.set_many({
            "available_capital": 200_000.0,
            "max_open_positions": 10,
            "watchlist_symbols": ["AAPL", "MSFT"],
        })
        all_vals = store.get_all()
        assert all_vals["available_capital"] == 200_000.0
        assert all_vals["max_open_positions"] == 10
        assert all_vals["watchlist_symbols"] == ["AAPL", "MSFT"]

    def test_overwrite(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path / "config.db")
        store.set("available_capital", 100_000.0)
        store.set("available_capital", 200_000.0)
        assert store.get("available_capital") == 200_000.0

    def test_delete(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path / "config.db")
        store.set("available_capital", 100_000.0)
        store.delete("available_capital")
        assert store.get("available_capital") is None
        assert store.is_empty

    def test_json_round_trip_types(self, tmp_path: Path) -> None:
        store = ConfigStore(tmp_path / "config.db")
        store.set_many({
            "bool_val": True,
            "int_val": 42,
            "float_val": 3.14,
            "str_val": "hello",
            "list_val": [1, 2, 3],
            "dict_val": {"a": 1},
        })
        vals = store.get_all()
        assert vals["bool_val"] is True
        assert vals["int_val"] == 42
        assert vals["float_val"] == 3.14
        assert vals["str_val"] == "hello"
        assert vals["list_val"] == [1, 2, 3]
        assert vals["dict_val"] == {"a": 1}


class TestBuildSettings:
    """Settings factory merges env + db + defaults correctly."""

    def test_defaults_used_when_db_empty(self) -> None:
        env = _EnvSettings(
            tradier_api_token="tok",
            tradier_account_id="acc",
        )
        settings = _build_settings(env, db_values={})
        assert settings.tradier_api_token == "tok"
        assert settings.available_capital == 100_000.0  # default
        assert settings.max_open_positions == 8  # default

    def test_db_values_override_defaults(self) -> None:
        env = _EnvSettings(tradier_api_token="tok")
        db = {"available_capital": 250_000.0, "max_open_positions": 12}
        settings = _build_settings(env, db)
        assert settings.available_capital == 250_000.0
        assert settings.max_open_positions == 12

    def test_env_fields_not_overridden_by_db(self) -> None:
        env = _EnvSettings(tradier_api_token="real_token")
        db = {"tradier_api_token": "fake_token"}
        settings = _build_settings(env, db)
        assert settings.tradier_api_token == "real_token"

    def test_watchlist_from_db(self) -> None:
        env = _EnvSettings()
        db = {"watchlist_symbols": ["AAPL", "MSFT"]}
        settings = _build_settings(env, db)
        assert settings.watchlist_symbols == ["AAPL", "MSFT"]


class TestMigration:
    """One-time .env → config.db migration."""

    def test_migrates_config_fields(self, tmp_path: Path, monkeypatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "TYCHE_TRADIER_API_TOKEN=secret\n"
            "TYCHE_AVAILABLE_CAPITAL=150000\n"
            "TYCHE_MAX_OPEN_POSITIONS=10\n"
            "TYCHE_WATCHLIST_SYMBOLS=[\"AAPL\",\"MSFT\"]\n"
        )
        monkeypatch.chdir(tmp_path)
        store = ConfigStore(tmp_path / "config.db")
        _migrate_env_to_config_db(store)

        vals = store.get_all()
        assert vals["available_capital"] == 150_000
        assert vals["max_open_positions"] == 10
        assert vals["watchlist_symbols"] == ["AAPL", "MSFT"]
        assert "tradier_api_token" not in vals  # secret stays in env

    def test_skips_when_no_env_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        store = ConfigStore(tmp_path / "config.db")
        _migrate_env_to_config_db(store)
        assert store.is_empty

    def test_only_runs_when_empty(self, tmp_path: Path, monkeypatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("TYCHE_AVAILABLE_CAPITAL=150000\n")
        monkeypatch.chdir(tmp_path)

        store = ConfigStore(tmp_path / "config.db")
        store.set("available_capital", 999_999.0)

        _migrate_env_to_config_db(store)
        assert store.get("available_capital") == 999_999.0


class TestInvalidation:
    """Settings cache invalidation."""

    def test_invalidate_clears_cache(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("tyche.config._settings_cache", None)
        monkeypatch.setattr("tyche.config._config_store", None)
        monkeypatch.setenv("TYCHE_DB_DIR", str(tmp_path))

        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

        invalidate_settings()
        s3 = get_settings()
        assert s3 is not s1
