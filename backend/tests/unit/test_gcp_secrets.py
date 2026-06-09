"""Tests for GCP Secret Manager env hydration."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from tyche.ops.gcp_secrets import (
    SECRET_TO_ENV,
    hydrate_env_from_secret_manager,
    should_load_gcp_secrets,
)


class TestShouldLoadGcpSecrets:
    def test_explicit_true(self, monkeypatch) -> None:
        monkeypatch.setenv("TYCHE_LOAD_GCP_SECRETS", "true")
        monkeypatch.delenv("TYCHE_RUN_ENV", raising=False)
        assert should_load_gcp_secrets() is True

    def test_prod_run_env(self, monkeypatch) -> None:
        monkeypatch.delenv("TYCHE_LOAD_GCP_SECRETS", raising=False)
        monkeypatch.setenv("TYCHE_RUN_ENV", "prod")
        assert should_load_gcp_secrets() is True

    def test_dev_default(self, monkeypatch) -> None:
        monkeypatch.setenv("TYCHE_LOAD_GCP_SECRETS", "false")
        monkeypatch.setenv("TYCHE_RUN_ENV", "dev")
        assert should_load_gcp_secrets() is False


class TestHydrateEnvFromSecretManager:
    def test_loads_missing_env_vars(self, monkeypatch) -> None:
        monkeypatch.setenv("TYCHE_GCP_PROJECT_ID", "tyche-platform")
        for env_key in SECRET_TO_ENV.values():
            monkeypatch.delenv(env_key, raising=False)

        mock_client = MagicMock()
        mock_client.access_secret_version.return_value.payload.data = b"secret-value"

        with patch(
            "tyche.ops.gcp_secrets._secret_manager_client",
            return_value=mock_client,
        ):
            loaded = hydrate_env_from_secret_manager(project_id="tyche-platform")

        assert "POLYGON_API_KEY" in loaded
        assert os.environ["TYCHE_POLYGON_API_KEY"] == "secret-value"

    def test_skips_when_env_already_set(self, monkeypatch) -> None:
        monkeypatch.setenv("TYCHE_GCP_PROJECT_ID", "tyche-platform")
        monkeypatch.setenv("TYCHE_POLYGON_API_KEY", "existing")

        mock_client = MagicMock()
        with patch(
            "tyche.ops.gcp_secrets._secret_manager_client",
            return_value=mock_client,
        ):
            loaded = hydrate_env_from_secret_manager(
                project_id="tyche-platform",
                secret_map={"POLYGON_API_KEY": "TYCHE_POLYGON_API_KEY"},
            )

        assert loaded == []
        mock_client.access_secret_version.assert_not_called()
