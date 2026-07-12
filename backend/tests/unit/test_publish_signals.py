"""Tests for route-level signal publisher (GCP-C)."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pandas as pd

import pytest

from tyche.config import TycheSettings
from tyche.exceptions import PublishError
from tyche.market_data.alpha_store import AlphaSignalStore
from tyche.ops.run_manifest import RunManifest, new_run_id
from tyche.storage import read_json
from tyche.storage.paths import StorageContext
from tyche.persistence.published_route_registry import first_existing_path
from tyche.workflow.publish_signals import PublishConfig, run_publish_signals


@pytest.fixture
def settings(tmp_path: Path) -> TycheSettings:
    return TycheSettings(
        tradier_api_token="t",
        tradier_account_id="a",
        gemini_api_key="g",
        data_dir=str(tmp_path),
        data_backend="local",
        alpha_min_market_cap_millions=0,
        published_max_age_minutes=180,
    )


@pytest.fixture
def local_ctx(tmp_path: Path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


def _seed_alpha(tmp_path: Path, *, variant: str = "sustained") -> None:
    store = AlphaSignalStore(data_dir=str(tmp_path), variant=variant)
    store.write(
        [
            {
                "ticker": "PLTR",
                "alpha_score": 82.5,
                "signal": "strong_buy",
                "horizon": "swing",
                "factors": {
                    "momentum": 0.8,
                    "relative_strength": 0.7,
                    "trend_quality": 0.6,
                    "breakout": 0.5,
                    "volume_thrust": 0.4,
                },
                "breakout_prob_swing": 0.71,
                "last_close": 25.0,
                "regime": "revenue",
            },
            {
                "ticker": "AAPL",
                "alpha_score": 45.0,
                "signal": "watch",
                "horizon": "trend",
                "factors": {},
                "last_close": 200.0,
            },
        ],
        as_of=date(2026, 6, 6),
    )


class TestRunManifest:
    def test_new_run_id_unique(self) -> None:
        assert new_run_id() != new_run_id()

    def test_run_manifest_round_trip(self, local_ctx: StorageContext) -> None:
        manifest = RunManifest.start(job_name="publish_signals", data_backend="local")
        manifest.output_paths.append("published/manifest.json")
        manifest.finish(status="success")
        rel = manifest.write(ctx=local_ctx)
        loaded = read_json(rel, ctx=local_ctx)
        assert loaded["job_name"] == "publish_signals"
        assert loaded["status"] == "success"
        assert "published/manifest.json" in loaded["output_paths"]


class TestFirstExistingPath:
    def test_picks_first_candidate(self, local_ctx: StorageContext, tmp_path: Path) -> None:
        legacy = tmp_path / "alpha_signals_sustained.parquet"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(b"not-parquet")
        found = first_existing_path(
            ("signals/alpha/alpha_signals_sustained.parquet", "alpha_signals_sustained.parquet"),
            ctx=local_ctx,
        )
        assert found == "alpha_signals_sustained.parquet"


class TestPublishSignals:
    def test_publishes_alpha_and_manifest(
        self,
        tmp_path: Path,
        settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        _seed_alpha(tmp_path)
        config = PublishConfig(
            data_dir=str(tmp_path),
            ctx=local_ctx,
            settings=settings,
            alpha_row_limit=10,
            strict=True,
        )
        result = run_publish_signals(config)

        assert result.run_id
        assert len(result.routes) == 16

        alpha_art = read_json("published/routes/stocks_alpha.json", ctx=local_ctx)
        assert alpha_art["route"] == "/stocks/alpha/"
        assert alpha_art["status"] in ("ok", "stale")
        assert alpha_art["row_count"] == 2
        assert alpha_art["data"]["total"] == 2
        assert alpha_art["data"]["strong_buy_count"] == 1
        assert alpha_art["data"]["signals"][0]["ticker"] == "PLTR"

        manifest = read_json("published/manifest.json", ctx=local_ctx)
        assert manifest["run_id"] == result.run_id
        assert any(r["route"] == "/stocks/alpha/" for r in manifest["routes"])

        route_manifest = read_json(
            "published/route_manifests/stocks_alpha.json",
            ctx=local_ctx,
        )
        assert route_manifest["artifact"] == "published/routes/stocks_alpha.json"

        run_manifest = read_json(result.run_manifest_rel, ctx=local_ctx)
        assert run_manifest["status"] == "success"
        assert "published/manifest.json" in run_manifest["output_paths"]

    def test_strict_fails_without_alpha(
        self,
        tmp_path: Path,
        settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        config = PublishConfig(
            data_dir=str(tmp_path),
            ctx=local_ctx,
            settings=settings,
            strict=True,
        )
        with pytest.raises(PublishError, match="alpha snapshot missing"):
            run_publish_signals(config)

    def test_prefers_signals_alpha_layout(
        self,
        tmp_path: Path,
        settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        canonical = tmp_path / "signals" / "alpha" / "alpha_signals_sustained.parquet"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            [
                {
                    "ticker": "MU",
                    "alpha_score": 90.0,
                    "signal": "buy",
                    "horizon": "thematic",
                    "as_of_date": date.today().isoformat(),
                    "computed_at": "2026-06-07T12:00:00+00:00",
                }
            ]
        )
        df.to_parquet(canonical, index=False)

        config = PublishConfig(
            data_dir=str(tmp_path),
            ctx=local_ctx,
            settings=settings,
        )
        result = run_publish_signals(config)
        alpha = next(r for r in result.routes if r.route_key == "stocks_alpha")
        assert alpha.source_paths[0] == "signals/alpha/alpha_signals_sustained.parquet"
        alpha_art = read_json("published/routes/stocks_alpha.json", ctx=local_ctx)
        assert alpha_art["data"]["signals"][0]["ticker"] == "MU"

    @pytest.mark.asyncio
    async def test_runs_inside_active_event_loop(
        self,
        tmp_path: Path,
        settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        """Cloud Run calls execute_job under asyncio.run — publisher must not nest."""
        _seed_alpha(tmp_path)
        config = PublishConfig(
            data_dir=str(tmp_path),
            ctx=local_ctx,
            settings=settings,
            alpha_row_limit=10,
            strict=True,
        )

        result = await asyncio.to_thread(run_publish_signals, config)

        assert result.run_id
        assert len(result.routes) == 16
