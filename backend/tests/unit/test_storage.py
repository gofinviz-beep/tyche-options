"""Tests for local/GCS storage abstraction (GCP-A)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tyche.config import TycheSettings
from tyche.exceptions import DataStoreError
from tyche.storage import (
    StorageContext,
    exists,
    is_gcs_path,
    join_uri,
    list_files,
    read_json,
    read_parquet,
    resolve_data_path,
    storage_context_from_settings,
    write_json,
    write_parquet,
)


@pytest.fixture
def local_ctx(tmp_path: Path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


@pytest.fixture
def gcs_ctx() -> StorageContext:
    return StorageContext(
        backend="gcs",
        local_root=Path("data"),
        gcs_bucket="tyche-data-prod",
        gcs_prefix="curated",
    )


class TestPaths:
    def test_is_gcs_path(self) -> None:
        assert is_gcs_path("gs://bucket/key.parquet")
        assert not is_gcs_path("/tmp/foo.parquet")
        assert not is_gcs_path("signals/alpha.parquet")

    def test_join_uri_relative(self) -> None:
        assert join_uri("signals", "alpha", "file.parquet") == (
            "signals/alpha/file.parquet"
        )

    def test_join_uri_gcs(self) -> None:
        assert join_uri("gs://bkt/a", "b", "c.parquet") == (
            "gs://bkt/a/b/c.parquet"
        )

    def test_resolve_local_path(self, local_ctx: StorageContext) -> None:
        resolved = resolve_data_path("ohlcv_daily/PL.parquet", ctx=local_ctx)
        assert resolved == local_ctx.local_root / "ohlcv_daily/PL.parquet"

    def test_resolve_gcs_path(self, gcs_ctx: StorageContext) -> None:
        resolved = resolve_data_path("prices/PL.parquet", ctx=gcs_ctx)
        assert resolved == "gs://tyche-data-prod/curated/prices/PL.parquet"

    def test_resolve_gcs_without_bucket_raises(self) -> None:
        with pytest.raises(DataStoreError, match="gcs_bucket"):
            StorageContext(backend="gcs", local_root=Path("data"))

    def test_storage_context_from_settings_local(self) -> None:
        settings = TycheSettings(
            tradier_api_token="t",
            tradier_account_id="a",
            gemini_api_key="g",
            data_dir="custom-data",
            data_backend="local",
        )
        ctx = storage_context_from_settings(settings)
        assert ctx.backend == "local"
        assert ctx.local_root == Path("custom-data")

    def test_storage_context_from_settings_gcs(self) -> None:
        settings = TycheSettings(
            tradier_api_token="t",
            tradier_account_id="a",
            gemini_api_key="g",
            data_backend="gcs",
            gcs_bucket="tyche-data-dev",
            gcs_prefix="signals/",
        )
        ctx = storage_context_from_settings(settings)
        assert ctx.backend == "gcs"
        assert ctx.gcs_bucket == "tyche-data-dev"
        assert ctx.gcs_prefix == "signals"


class TestParquetIo:
    def test_local_parquet_roundtrip(self, local_ctx: StorageContext) -> None:
        df = pd.DataFrame({"ticker": ["PL"], "close": [24.5]})
        rel = "ohlcv_daily/PL.parquet"
        write_parquet(df, rel, ctx=local_ctx)
        out = read_parquet(rel, ctx=local_ctx)
        pd.testing.assert_frame_equal(out, df)

    def test_local_atomic_write_no_temp_left(
        self, local_ctx: StorageContext
    ) -> None:
        df = pd.DataFrame({"x": [1]})
        rel = "nested/out.parquet"
        write_parquet(df, rel, atomic=True, ctx=local_ctx)
        target = local_ctx.local_root / rel
        assert target.exists()
        temps = list(local_ctx.local_root.rglob("*.tmp.*"))
        assert temps == []

    def test_local_exists_and_list(self, local_ctx: StorageContext) -> None:
        df = pd.DataFrame({"a": [1]})
        write_parquet(df, "alpha/a.parquet", ctx=local_ctx)
        write_parquet(df, "alpha/b.parquet", ctx=local_ctx)
        assert exists("alpha/a.parquet", ctx=local_ctx)
        listed = list_files("alpha", suffix=".parquet", ctx=local_ctx)
        assert len(listed) == 2
        assert all(p.endswith(".parquet") for p in listed)

    def test_read_missing_raises(self, local_ctx: StorageContext) -> None:
        with pytest.raises(DataStoreError, match="not found"):
            read_parquet("missing.parquet", ctx=local_ctx)

    def test_gcs_write_uses_atomic_promote(self, gcs_ctx: StorageContext) -> None:
        df = pd.DataFrame({"ticker": ["AAPL"], "close": [190.0]})
        fs = MagicMock()
        with (
            patch("tyche.storage.parquet_io.get_gcs_filesystem", return_value=fs),
            patch.object(pd.DataFrame, "to_parquet") as mock_to_parquet,
        ):
            write_parquet(df, "signals/alpha.parquet", ctx=gcs_ctx)

        mock_to_parquet.assert_called_once()
        written_uri = str(mock_to_parquet.call_args[0][0])
        assert written_uri.startswith("gs://tyche-data-prod/curated/signals/")
        assert ".tmp." in written_uri
        fs.cp.assert_called_once()
        src, dst = fs.cp.call_args[0]
        assert ".tmp." in src
        assert dst == "gs://tyche-data-prod/curated/signals/alpha.parquet"
        fs.rm.assert_called_once()

    def test_gcs_promote_ignores_missing_temp_on_cleanup(
        self, gcs_ctx: StorageContext
    ) -> None:
        fs = MagicMock()
        fs.rm.side_effect = FileNotFoundError(
            ["gs://tyche-data-prod/curated/signals/alpha.parquet.tmp.abc"]
        )
        with (
            patch("tyche.storage.parquet_io.get_gcs_filesystem", return_value=fs),
            patch.object(pd.DataFrame, "to_parquet"),
        ):
            write_parquet(
                pd.DataFrame({"x": [1]}),
                "signals/alpha.parquet",
                ctx=gcs_ctx,
            )

        fs.cp.assert_called_once()
        fs.rm.assert_called_once()

    def test_gcs_exists_mocked(self, gcs_ctx: StorageContext) -> None:
        fs = MagicMock()
        fs.exists.return_value = True
        with patch("tyche.storage.parquet_io.get_gcs_filesystem", return_value=fs):
            assert exists("signals/alpha.parquet", ctx=gcs_ctx)
        fs.exists.assert_called_with(
            "gs://tyche-data-prod/curated/signals/alpha.parquet"
        )


class TestJsonIo:
    def test_local_json_roundtrip(self, local_ctx: StorageContext) -> None:
        payload = {"route": "/stocks/alpha/", "row_count": 3}
        rel = "published/routes/stocks_alpha.json"
        write_json(payload, rel, ctx=local_ctx)
        loaded = read_json(rel, ctx=local_ctx)
        assert loaded == payload

    def test_local_json_atomic(self, local_ctx: StorageContext) -> None:
        write_json({"ok": True}, "manifest.json", atomic=True, ctx=local_ctx)
        path = local_ctx.local_root / "manifest.json"
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}

    def test_read_invalid_json_raises(self, local_ctx: StorageContext) -> None:
        bad = local_ctx.local_root / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(DataStoreError, match="Invalid JSON"):
            read_json(str(bad), ctx=local_ctx)


class TestConfigDefaults:
    def test_gcp_storage_defaults(self) -> None:
        settings = TycheSettings(
            tradier_api_token="t",
            tradier_account_id="a",
            gemini_api_key="g",
        )
        assert settings.data_backend == "local"
        assert settings.gcs_bucket is None
        assert settings.gcs_prefix == ""
        assert settings.run_env == "dev"
        assert settings.api_prefer_published_signals is True
        assert settings.api_allow_curated_fallback is False
        assert settings.published_max_age_minutes == 180
