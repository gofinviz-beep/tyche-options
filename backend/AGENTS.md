# Backend Agent Instructions

## Essentials

- Python 3.12+, FastAPI, pydantic-settings, structlog, OpenTelemetry
- Always run from the `backend/` directory — all relative paths assume it as cwd
- Use absolute imports: `from tyche.config import TycheSettings` (never relative)
- All settings via `TYCHE_*` env vars; `.env` is read by pydantic-settings directly — do NOT `source .env`

## Module Boundaries

- **Routes** (`api/routes/`) call services via `Depends()` from `api/deps.py` — never instantiate services directly
- **Workflows** (`workflow/`) orchestrate multi-step pipelines — they call engines, brokers, and agents
- **Engines** (`conviction/`, `strategy/`, `risk/`) are testable in isolation. Conviction uses a three-layer architecture: `features.py` (EMA computation + cache), `csp_policy.py` (stateless CSP gates), `engine.py` (backward-compat wrapper). All existing imports from `tyche.conviction.engine` continue to work.
- **Strategy** (`strategy/`) — `engine.py` parallelizes per-ticker scans via `asyncio.gather` + semaphore(10). `cash_secured_put.py` scores with 11 factors: `annualized_return × liquidity × dte × vrp × iv_rank × trend_confirm × rsi × earnings × dow × iv_catalyst × ml_factor`. `allocator.py` adds mega-cap bonus to MILP risk weights.
- **Clients** (`broker/tradier/`, `analysis/client.py`, `market_data/polygon.py`) wrap external APIs with retry/error handling
- **Persistence** (`persistence/`) uses distributed SQLite with named engines — `register_engine("scans", url)` → `get_session("scans")`. Includes `position_repository.py` (stock positions), `backtest_repository.py` (pullback profiles), `conviction_repository.py` (snapshots/transitions + `get_latest_snapshot_date()` for holiday-safe date fallback).
- **Models** (`models/`) — `backtest.py` has both backtest data (`PullbackEvent`, `TickerPullbackProfile`) and position tracking (`StockPosition`, `ExitSignal`)

## Data Sources

- **Polygon.io** — historical OHLCV data, ticker metadata including SIC codes/sector classification (bootstrap/backtest only), short interest, options flat files (via Massive S3)
- **Tradier** — live quotes, options chains, account operations, order execution
- **Finnhub** (`market_data/finnhub.py`) — demand data: Fundamental-1 standardized `/stock/financials` (primary D-FUND) + as-reported quarterly/annual fallbacks + Estimates-1 (D-EST). `get_standardized_financials()` merges IC/BS/CF, scales millions→absolute. Dual-class fetch via `market_data/dual_class.py` (GOOG→GOOGL, etc.). 300 rpm.
- **Benzinga via Massive** (`market_data/benzinga.py`) — Corporate Guidance → D-CAT guide-vs-consensus catalysts
- **Gemini LLM** — qualitative analysis only; all numbers come from broker data via `_resolve_numeric()`. Three model tiers: `gemini_model_fast` (scanner), `gemini_model_deep` (complex reasoning), `gemini_model_classify` (news/8-K classification, defaults to `gemini-2.5-flash-lite`)

## Key Conventions

- All API routes are mounted under `/api/v1`; health endpoints at root (`/health`, `/health/ready`)
- LLM analysis is optional (off by default, toggle in Scanner UI), per-ticker parallel with semaphore control (`llm_concurrency` setting)
- Blank Scanner / scheduled morning scan passes `watchlist=[]` — full equity universe. Settings `watchlist_symbols` is highlight-only for UI badges.
- `POST /scanner/scan` accepts `available_capital` query param for per-scan deploy capital override (MILP allocator input).
- `CCAnalysisEngine` exposes `ema_21_slope` on `CCSignal` for informational display — does not gate sell signal (extension-based).
- Scanner pipeline has 10 timed stages — each records OTel histogram metrics
- `PipelineStage.duration_ms` and `MorningScanResult.total_duration_ms` track performance
- Error branches in workflows are swallowed with logging + OTel counter — pipeline continues
- `MockBroker` in `broker/mock.py` provides deterministic test data (PL + AAPL)

## ML Baselines & Live Inference (Phase 1)

- `ml/` package: tabular feature extraction, label construction, XGBoost walk-forward evaluation, model persistence, live inference
- Vectorised features (`ml/features.py`) match `ConvictionFeatureEngine` formulas but run over full history
- Labels (`ml/labels.py`) use only raw OHLCV — no derived features in label construction (leakage prevention)
- Two model variants: per-stock features only vs. + sector-aggregated neighbor features
- Walk-forward: 126d train / 63d test windows, strict temporal splits, 39 windows (2015–2026)
- Model persistence (`ml/model_store.py`): XGBoost native JSON + metadata sidecar under `data/ml/models/`
- Live inference (`ml/inference.py`): `CSPSafetyPredictor` bridges `FeatureSignal` → XGBoost `predict_proba` → `csp_safety_prob`
- `csp_safety_prob` threaded through: `FeatureSignal` → `ConvictionSignal` → Parquet → SQLite → API → frontend
- Scanner scoring: `ml_factor = 0.5 + 0.5 * csp_safety_prob` (1.0 when model absent)
- Monthly retrain: APScheduler `CronTrigger` (1st of month, 2 AM ET). Manual: `POST /api/v1/system/ml/retrain`
- CLI: `python scripts/train_baselines.py` (build dataset + train + evaluate + save production model)
- Requires `pip install -e ".[ml]"` for `xgboost` and `scikit-learn`
- **Results (3.39M rows, 8,192 tickers):** single model = 88.0% acc / 0.915 AUC, neighbor model = 87.6% acc / 0.910 AUC → single model deployed (neighbor features don't help). Top feature: `price_to_21ema_pct` (51.3%). Relational features (ETF + correlation) contribute 8.1% of importance; SPY/QQQ betas rank 12th/14th.
- `compute_rolling_correlations()` auto-injects SPY/QQQ into the ticker list — they are ETFs excluded by `filter_equity_only()` but required for beta computation

## Directional Alpha — Demand Conviction Engine v2 (10X / Big-Move Signals)

Complements (does not replace) the CSP/CC income engine — finds large upside moves to buy. v2 **leads with demand evidence** (v1 led with momentum, which favored already-run names). Full reference: `docs/directional-alpha.md`.

- **Six demand dimensions** (each degrades to neutral when its store is absent): D-FUND (Finnhub fundamentals), D-EST (Finnhub estimates), D-CAT (news/8-K catalysts + Benzinga guide-vs-consensus), D-POL (`market_data/policy_calendar.py` `PolicyEventCalendar`), D-GRAPH (`market_data/supply_chain_graph.py` `SupplyChainGraph`), D-TECH (Polygon short interest).
- **Stores:** `FundamentalsStore` (`data/fundamentals/`), `EstimatesStore` (`data/estimates/`), `EstimateSnapshotStore` (`data/estimate_snapshots/` — wide EPS/revenue consensus per ingest day), `ShortInterestStore` (`data/short_interest/`), `CatalystSignalStore` (`data/catalyst_signals/`). Ingest: `workflow/demand_data.py` via `scripts/ingest_demand_data.py` (writes both estimate stores on each estimates fetch).
- **Audit:** `scripts/audit_demand_coverage.py`, `scripts/audit_estimate_snapshots.py`, `scripts/audit_finnhub_vs_edgar.py`.
- **Multi-bagger discovery (P1 done, P2+ next):** `alpha_discovery_enabled` + sub-flags in `config.py`; `build_alpha_score_engine()`, purged walk-forward, class weighting, percentile/DAE scoring. **Spec:** `docs/multibagger_discovery_engine_v8_cursor_composer_spec.md` (supersedes v7). P1 completion: `docs/alpha/p1_completion_note.md`. Production Alpha unchanged when discovery is off.
- **ADR equity type overrides:** `EQUITY_TYPE_OVERRIDES` in `market_data/data_store.py` (currently `ARM→CS`). Applied in `write_meta()`, `refresh_ticker_meta()`, and `ingest_data.py --meta` so ADRs pass `filter_equity_only()`.
- **Benzinga guide-vs-consensus** (`market_data/benzinga.py`): `derive_guidance_catalysts()` prefers guided-vs-consensus, falls back to same-period revision then YoY. Fiscal-calendar alignment via `_infer_fye_month()` (in `demand_data.py`) + `fiscal_quarter_end()` + `_match_consensus()` (nearest Finnhub period ≤ 46d). Comparator skipped (never wrong-matches) when FYE unknown.
- **Labels** (`ml/labels.py`): peak `big_move_up_{25,40,60}pct_{40,60,120}d` (touches target intra-window) **and** sustained `big_move_sustained_*` (still up at horizon END — realistic buy target). Raw OHLCV only.
- **Features** (`ml/features.py`): momentum/RS + demand groups (`FUNDAMENTAL/ESTIMATE/SHORT_INTEREST/CATALYST/GRAPH_FEATURE_COLS`), 97 cols total (`demand_feature_columns()`), via `get_feature_columns(include_demand=True, ...)`. **Demand augmenters MUST stay vectorized** (per-ticker `merge_asof` / numpy broadcast) — a per-row loop hangs `build_dataset` for 30–60 min.
- **Model** (`ml/breakout.py`): `BreakoutPredictor` loads per-horizon XGBoost artifacts; instantiate with `ALPHA_TARGETS` (peak) or `ALPHA_SUSTAINED_TARGETS` (sustained). Graceful degradation when no artifact.
- **Engine** (`strategy/alpha_engine.py`): `AlphaScoreEngine` → `composite = 0.55·ml_blend + 0.45·factor_blend`, then anti-chase `×(1 − 0.45·overextension)`, then regime-routed (`REGIME_REVENUE`/`REGIME_NARRATIVE`) demand multiplier `1 + 0.30·net` clamped `[0.70, 1.30]` (net=0 → v1-identical). 0–100 `AlphaScore` + `horizon` + `signal`. `AlphaSignal` carries `DemandDimensions`, `regime`, `demand_multiplier`, `overextension_*`, `market_cap`, `institutional_pct`.
- **Peak vs Sustained variants** (compare-only; page defaults to Sustained): `AlphaSignalStore(variant=)` → `data/alpha_signals.parquet` (peak) / `data/alpha_signals_sustained.parquet`. `alpha_sustained_enabled` (default true) gates the second. `run_alpha_batch(variants=[...])` builds features ONCE, scores each.
- **Batch** (`workflow/alpha_batch.py`): chained after nightly flatfile (`alpha_batch_after_flatfile`) else 16:20 ET cron. Build-net floor `alpha_min_market_cap_millions` (default $250M, common-stock only).
- **Route** (`api/routes/alpha.py`): `GET /alpha/scan?variant=sustained|peak` (read-time `min_market_cap_millions` floor + common-stock filter + meta; falls back to peak + reports served `variant`), `GET /alpha/signal/{ticker}` (any name regardless of rank — `/scan` only returns top `limit`), `POST /alpha/recompute`.
- **Training CLI:** `python scripts/train_alpha.py` (`--save-model`, `--feature-set momentum` for peak 62-feature models; `--feature-set demand --sustained` for demand ablation). **Demand gate:** `python scripts/run_demand_gate.py` — walk-forward ablation (momentum vs demand) + conditional promotion of `big_move_sustained_*` (97 features; net-new; never overwrites peak). After fundamentals repair: re-run gate + peak train + alpha batch + restart backend. Verdict → `data/ml/alpha_results/demand_gate_verdict.json`.

## Live Market Cap (shares × close)

- Polygon's `market_cap` reference field lags by **months** (not re-priced daily). Do NOT trust it as current.
- `ticker_meta.parquet` stores `shares_outstanding` (Polygon `weighted_shares_outstanding`). `recompute_market_caps_from_shares(meta_store, ohlcv_store)` derives `market_cap = shares × latest close` and writes it back into `market_cap`, so all `get_market_caps()` consumers are price-current with no code changes.
- Daily reprice runs after the OHLCV refresh (free — uses today's close). Shares are refreshed weekly (slow-moving). One-time/manual: `python scripts/backfill_shares_caps.py`.

## ETF Constituents & Correlation (Pre-GNN Relational Features)

- `market_data/etf_constituents.py`: static curated lists for 10 key ETFs (SPY, QQQ, DIA, XLK, XLF, XLE, XLV, SMH, SOXX, XLI)
- `market_data/etf_store.py`: `ETFConstituentStore` — Parquet-backed persistence of ETF membership + weights
- `market_data/etf_store.py`: `build_etf_data()` merges static lists with yfinance `funds_data.top_holdings` for weights
- `market_data/correlation_store.py`: `CorrelationStore` — Parquet-backed 60d rolling pairwise correlations + SPY/QQQ betas
- `market_data/correlation_store.py`: `compute_rolling_correlations()` — builds return matrix from OHLCV, produces top-N peers + betas
- `ml/features.py`: `ETF_FEATURE_COLS` (7) and `CORRELATION_FEATURE_COLS` (5) — added to XGBoost feature set
- `ml/features.py`: `add_etf_features()` and `add_correlation_features()` augment the dataset
- `ml/dataset.py`: `build_dataset()` auto-integrates ETF + correlation features (flags: `include_etf`, `include_correlation`)
- Ingestion: `python scripts/ingest_data.py --etf --correlations`
- Leakage prevention: correlation window = `[as_of_date - 60, as_of_date - 1]` (no same-day data)

## GCP Cloud Batch (production ingest)

When `TYCHE_DATA_BACKEND=gcs`, batch ingest runs in **Cloud Run Jobs** — not APScheduler.

- **Deploy:** `infra/gcp/README.md` — `deploy_jobs.sh`, `deploy_workflow.sh`, `deploy_scheduler.sh`
- **Spec:** `docs/tyche_gcp_minimal_migration_spec_v2.md` (§21 TODO: multi-task ingest sharding)
- **Entry:** `scripts/run_gcp_job.py` → `tyche/ops/gcp_jobs.py` (10 jobs)
- **Intelligence:** `ops/intelligence_export.py` — Parquet rollups, no `news.db` in cloud
- **Publish:** `workflow/publish_signals.py` → `published/routes/*.json`
- **Storage:** `tyche/storage/StoreBackend` — all stores GCS-aware
- **Scheduler:** evening 6 PM (ingest-data, ingest-demand-data, news, edgar — **no demand gate**) + morning 2:30 AM (flatfiles + alpha → optional demand-gate → publish → audit); `scheduler_enabled=false` in GCS mode
- **Demand gate:** morning-only optional job; retrains `big_move_sustained_*` models (~4–8h cloud). Publish needs alpha-batch, not gate.
- **Local ingest:** `ingest_data.py` uses `storage_context_from_settings()`; `IntradayStore` requires `ctx` for `_MetadataCache`

## Automated Data Pipelines (local APScheduler)

When `TYCHE_DATA_BACKEND=local`, data operations run via APScheduler. Full runbook: `docs/data-operations.md`.

- **Daily after close:** OHLCV refresh (16:02, then reprices market caps from shares × close) → conviction batch (16:08) → exit monitor (16:05), options snapshot (16:10) → bridge Tradier IV (16:45). **Alpha batch** runs chained after the nightly S3 flatfile ingest (`alpha_batch_after_flatfile`, ~02:00 ET) — peak + sustained snapshots — else a standalone 16:20 ET cron.
- **Daily demand data (03:00 ET, `demand_data_enabled` / `demand_data_refresh_time`):** Finnhub fundamentals + estimates, Polygon short interest, Benzinga guidance → catalysts (`_scheduled_demand_data` → `ingest_demand_data`). Skipped with a warning if credentials are absent.
- **Weekly:** Ticker meta refresh (Sundays 02:00 ET) — Polygon reference data + shares-outstanding refresh + live cap reprice (the separate Polygon market-cap backfill is no longer run here — superseded by the daily shares × close reprice)
- **Monthly:** Correlation refresh (28th, 22:00 ET) → ML retrain (1st, 02:00 ET, includes ETF + correlation features)
- **Quarterly (Mar/Jun/Sep/Dec):** ETF constituents (1st, 03:00) → sector/SIC + institutional ownership (1st, 03:30)
- **Config knobs:** `conviction_batch_after_ohlcv`, `alpha_batch_enabled`, `alpha_batch_after_flatfile`, `alpha_sustained_enabled`, `demand_data_enabled`, `bridge_tradier_iv_enabled`, `correlation_refresh_enabled`, `etf_refresh_enabled`, `quarterly_meta_refresh_enabled`, `weekly_meta_refresh_enabled` — all default `true`
- Handler functions in `app.py`, scheduler methods in `workflow/scheduler.py`

## Testing

- ~1240 unit tests in `tests/unit/`, run with `pytest`
- External APIs are always mocked — no network calls in tests
- Use `AsyncMock` for async broker/LLM calls, `MagicMock` for data stores
- `morning_scan.py`, `analysis/client.py` have 100% coverage; `exit_monitor.py` at 95%
- Conviction tests use `_fresh_uptrend()` helper to generate valid EMA streak data

## Observability

- structlog JSON with OTel `trace_id`/`span_id` injected into every log event
- OpenTelemetry histograms: `scanner.stage.duration`, `http.server.request.duration`, `llm.call.duration`
- OpenTelemetry counters: `scanner.errors`, `api.errors`
- GCP exporters when `TYCHE_GCP_PROJECT_ID` is set; console exporters otherwise
