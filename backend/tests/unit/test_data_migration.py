"""Tests for one-time GCS data migration (GCP-E)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tyche.ops.data_migration import (
    MigrationConfig,
    discover_local_files,
    gcs_object_uri,
    parse_gcs_uri,
    run_data_migration,
    should_skip_relative,
)


class TestParseGcsUri:
    def test_bucket_only(self) -> None:
        assert parse_gcs_uri("gs://tyche-data-prod") == ("tyche-data-prod", "")

    def test_bucket_with_prefix(self) -> None:
        assert parse_gcs_uri("gs://tyche-data-prod/curated") == (
            "tyche-data-prod",
            "curated",
        )

    def test_invalid(self) -> None:
        with pytest.raises(ValueError, match="gs://"):
            parse_gcs_uri("s3://nope")


class TestDiscoverLocalFiles:
    def test_discovers_parquet_and_skips_tmp(self, tmp_path: Path) -> None:
        (tmp_path / "ohlcv_daily").mkdir()
        (tmp_path / "ohlcv_daily" / "AAPL.parquet").write_bytes(b"pq")
        (tmp_path / "published" / "routes").mkdir(parents=True)
        (tmp_path / "published" / "routes" / "stocks_alpha.json").write_text("{}")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "junk.pyc").write_bytes(b"x")
        (tmp_path / "alpha_signals.parquet.tmp.abc123").write_bytes(b"x")

        files = discover_local_files(tmp_path)
        rels = {f.relative for f in files}
        assert "ohlcv_daily/AAPL.parquet" in rels
        assert "published/routes/stocks_alpha.json" in rels
        assert not any("pycache" in r for r in rels)
        assert not any(".tmp." in r for r in rels)

    def test_include_prefix_filter(self, tmp_path: Path) -> None:
        (tmp_path / "published").mkdir()
        (tmp_path / "published" / "manifest.json").write_text("{}")
        (tmp_path / "ohlcv_daily" / "X.parquet").parent.mkdir(parents=True)
        (tmp_path / "ohlcv_daily" / "X.parquet").write_bytes(b"pq")

        files = discover_local_files(tmp_path, include_prefixes=("published",))
        assert {f.relative for f in files} == {"published/manifest.json"}


class TestShouldSkip:
    def test_skip_rules(self) -> None:
        assert should_skip_relative("__pycache__/mod.pyc")
        assert should_skip_relative("foo.tmp.abc")
        assert not should_skip_relative("ohlcv_daily/PL.parquet")


class TestGcsObjectUri:
    def test_with_prefix(self) -> None:
        assert gcs_object_uri(
            "gs://bkt/curated",
            "ohlcv_daily/PL.parquet",
        ) == "gs://bkt/curated/ohlcv_daily/PL.parquet"


class TestRunMigration:
    def test_dry_run_writes_manifest(self, tmp_path: Path) -> None:
        df = pd.DataFrame({"a": [1, 2, 3]})
        pq = tmp_path / "sample.parquet"
        df.to_parquet(pq, index=False)

        config = MigrationConfig(
            local_data_root=tmp_path,
            gcs_uri="gs://tyche-data-test",
            dry_run=True,
        )
        result = run_data_migration(config)
        assert result.dry_run is True
        assert result.file_count == 1
        assert result.total_bytes == pq.stat().st_size
        manifest_path = tmp_path / result.manifest_rel
        assert manifest_path.exists()

    @patch("tyche.ops.data_migration.get_gcs_filesystem")
    @patch("tyche.ops.data_migration.read_parquet")
    def test_execute_uploads_and_readback(
        self,
        mock_read: MagicMock,
        mock_fs_factory: MagicMock,
        tmp_path: Path,
    ) -> None:
        df = pd.DataFrame({"ticker": ["AAPL"], "close": [100.0]})
        rel = "ohlcv_daily/AAPL.parquet"
        path = tmp_path / rel
        path.parent.mkdir(parents=True)
        df.to_parquet(path, index=False)

        mock_fs = MagicMock()
        mock_fs_factory.return_value = mock_fs
        mock_read.return_value = df

        config = MigrationConfig(
            local_data_root=tmp_path,
            gcs_uri="gs://tyche-data-test",
            dry_run=False,
            verify_sample_count=1,
        )
        result = run_data_migration(config)
        assert result.dry_run is False
        assert result.uploaded_count == 1
        assert result.errors == []
        assert mock_fs.put.called
        assert result.sample_readback[0]["ok"] is True
