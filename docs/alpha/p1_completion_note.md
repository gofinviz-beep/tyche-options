# P1 Completion Note (Accepted with Caveats)

**Status:** P1 validation accepted with caveats — **2026-06-06**  
**Authority:** [multibagger_discovery_engine_v8_cursor_composer_spec.md](../multibagger_discovery_engine_v8_cursor_composer_spec.md) (supersedes v7)  
**P0.3 reference:** [estimate_snapshot_findings.md](estimate_snapshot_findings.md)

**Explicitly out of scope (unchanged):** estimate-revision ramp unit fix (old P1.5-B); same-period revision features in ML (`demand_feature_columns()` / P1.5-C). Estimate revision velocity remains **forward-maturing local snapshot infrastructure** only.

---

## 1. P1 tasks implemented

| ID | Deliverable | Location |
|----|-------------|----------|
| **P1.1** | Discovery config flags | `backend/src/tyche/config.py` |
| **P1.2** | Per-target `scale_pos_weight` (binary targets) | `backend/src/tyche/ml/xgb_baseline.py` |
| **P1.3** | `__isna` missingness indicators + `BreakoutPredictor` train/serve parity | `backend/src/tyche/ml/features.py`, `backend/src/tyche/ml/inference.py` |
| **P1.4** | Percentile discovery signals + `build_alpha_score_engine()` | `backend/src/tyche/strategy/alpha_engine.py`, `backend/src/tyche/api/deps.py`, `backend/src/tyche/workflow/alpha_batch.py` |
| **P1.5-A** | `EstimateSnapshotStore`, `get_consensus_snapshot_rows()`, daily ingest wiring | `backend/src/tyche/market_data/estimate_snapshot_store.py`, `finnhub.py`, `workflow/demand_data.py` |
| **P1.5-B** | Snapshot cadence audit | `backend/scripts/audit_estimate_snapshots.py` |
| **P1.7** | Guidance `tanh(log1p)` impact; train-universe logging ($2B default, `--discovery-train` → $250M) | `backend/src/tyche/market_data/benzinga.py`, `scripts/train_alpha.py`, `scripts/run_demand_gate.py` |
| **P1.8** | Gated demand-adjusted extension | `backend/src/tyche/strategy/alpha_engine.py` |
| **P1.9** | Purged + embargoed walk-forward splits | `backend/src/tyche/ml/validation.py`, integrated in `xgb_baseline.py` |

**Not implemented (by design):** P1.5-C (same-period revision ML features), P1.5-D/E/F (active-forward naming, training guardrails beyond snapshots, ramp calibration).

**Tests:** `test_alpha_discovery.py`, `test_purged_walk_forward.py`, `test_estimate_snapshot_store.py`, `test_alpha_conservative.py` (fixture invariance).

---

## 2. Flags that control discovery behavior

All in `TycheSettings` (`config.py`); overridable via `config.db` after first startup.

| Flag | Default | Effect |
|------|---------|--------|
| `alpha_discovery_enabled` | **`false`** | Master gate. When off, `build_alpha_score_engine()` returns plain `AlphaScoreEngine()` — percentile + demand-adjusted extension sub-flags ignored. |
| `alpha_percentile_signals_enabled` | `false` | Cross-sectional percentile `strong_buy` / signal mapping (only if discovery on). |
| `alpha_demand_adjusted_extension_enabled` | `false` | Demand-net softens anti-chase penalty (only if discovery on). |
| `alpha_peer_tier_normalization_enabled` | `false` | Reserved for peer-tier normalization (discovery path). |
| `alpha_class_weighting_enabled` | **`true`** | `scale_pos_weight` in walk-forward + production training (`xgb_baseline.py`). |
| `alpha_purged_walk_forward_enabled` | **`true`** | Purged/embargoed date windows in walk-forward eval. |
| `alpha_discovery_train_min_market_cap_millions` | `250` | Floor logged / used with `--discovery-train` on train scripts. |
| `alpha_demand_mult_ceil_discovery` | `1.45` | Upper demand multiplier cap when discovery engine is built with discovery on. |
| `alpha_discovery_snapshot_enabled` | `false` | Reserved snapshot hook for discovery namespace (not production alpha page default). |

Train scripts also accept `--discovery-train` to log/train at the $250M discovery floor vs the default $2B logging floor.

---

## 3. Conservative Directional Alpha when discovery flags are off

**Live scoring / nightly alpha batch (page path):** **Yes — byte-identical to pre-P1 discovery path.**

- `alpha_discovery_enabled=false` (default) → `build_alpha_score_engine()` → `AlphaScoreEngine()` with no percentile signals and no demand-adjusted extension.
- `net=0` demand still yields v1-identical multiplier (`1.0`).
- Locked by `tests/fixtures/alpha_conservative_fixture.json` + `test_alpha_conservative.py` and `test_conservative_engine_matches_default_fixture_behavior` in `test_alpha_discovery.py`.

**Caveat — ML training path:** `alpha_class_weighting_enabled` and `alpha_purged_walk_forward_enabled` default **`true`** and apply in `train_alpha.py` / `run_demand_gate.py` **independently of** `alpha_discovery_enabled`. Re-trained production models may differ from pre-P1 models unless those training flags are disabled in `config.db`. Missingness indicators in training run only when `alpha_discovery_enabled=true` **and** `--feature-set demand`.

---

## 4. Did class-weighted training actually run?

**Implemented and wired:** yes.

- `_scale_pos_weight_for_labels()` in `xgb_baseline.py`; logged as `train_scale_pos_weight` / `walk_forward_scale_pos_weight`.
- Enabled when `alpha_class_weighting_enabled=true` (default) in `train_alpha.py`, `run_demand_gate.py`, and `train_production_model()`.
- Applied to **binary** targets only (not multiclass); clamped `neg/pos` to `[1.0, 50.0]`.

**Whether your deployed `data/ml/models/*.json` artifacts were re-trained after P1** depends on having run `train_alpha.py` / `run_demand_gate.py` post-merge. Check logs for `scale_pos_weight` lines or model `_meta.json` `trained_at` timestamps.

---

## 5. Did purged walk-forward actually run?

**Implemented and wired:** yes.

- `purged_walk_forward_splits()` in `ml/validation.py` (embargo defaults to label horizon from target name).
- Used when `use_purged_splits=true` → `settings.alpha_purged_walk_forward_enabled` (default **`true`**) in `walk_forward_evaluate()`.
- Walk-forward reports record `purged=true` in summary metadata.

Same caveat as §4: runs on next `train_alpha.py` / `run_demand_gate.py` invocation; not automatic on backend restart alone.

---

## 6. Metrics and artifact locations

| Artifact | Path |
|----------|------|
| Production XGBoost models | `backend/data/ml/models/{target}.json` + `{target}_meta.json` |
| Walk-forward / baseline reports | `backend/data/ml/results/` (per-run JSON from `train_alpha.py --results-dir`) |
| Demand gate verdict | `backend/data/ml/alpha_results/demand_gate_verdict.json` (`run_demand_gate.py`) |
| Alpha training panel | `backend/data/ml/alpha_dataset.parquet` (cached demand dataset) |
| Estimate snapshot cadence audit | `backend/data/ml/alpha_results/estimate_snapshot_cadence_v1.json` (`audit_estimate_snapshots.py`) |
| Demand coverage audit | `backend/data/ml/demand_audit_summary.json`, `demand_audit_report.csv` |
| Live alpha snapshots | `backend/data/alpha_signals.parquet`, `backend/data/alpha_signals_sustained.parquet` |
| Estimate snapshots (wide) | `backend/data/estimate_snapshots/{TICKER}.parquet` |
| Tidy estimates (legacy) | `backend/data/estimates/{TICKER}.parquet` |
| P0.3 probe write-up | `docs/alpha/estimate_snapshot_findings.md` |

---

## 7. Estimate snapshot persistence semantics

**Append/upsert — prior `snapshot_date` rows are retained.**

`EstimateSnapshotStore.write_snapshots()` (`estimate_snapshot_store.py`):

1. Reads existing Parquet for the ticker (if any).
2. `concat` new rows with existing.
3. Dedupes on **`(ticker, vendor, metric, freq, period, snapshot_date)`** — `keep="last"` for same-day re-ingest.
4. **Does not delete** rows from other `snapshot_date` values.

Wide schema stores `metric` ∈ `{eps, revenue}` and `period` (vendor fiscal period string, e.g. `2026-06-30`). There is no separate `fiscal_period` column — **`period` is the fiscal-period key**.

`ingested_at` is UTC ingest time; `snapshot_date` is the calendar ingest day (`date.today()` in `ingest_demand_data()`), **not** a Finnhub historical as-of (see P0.3).

Same-period revision velocity (P1.5-C) is **not** computed in ML features yet; snapshots exist to mature that series locally over daily ingests.

---

## Operational follow-ups (caveats)

1. **Daily `ingest_demand_data`** — accumulate multiple `snapshot_date` values before enabling P1.5-C revision features.
2. **Re-train / demand gate** — run after fundamentals repair to refresh models with class weighting + purged WF under current defaults.
3. **ARM-style meta gaps** — tickers missing from `ticker_meta.parquet` are excluded from demand ingest until meta is seeded (OHLCV alone is insufficient).
4. **Do not** apply estimate-ramp unit bounds until populated revision distributions exist (P1.5-F).

**Next phase:** P2 per v8 spec (multi-bagger labels, discovery namespace).

**Checkpoint (2026-06-07):** ARM `ADRC→CS` override in `EQUITY_TYPE_OVERRIDES`; options backfill via `--tickers ARM --force`.
