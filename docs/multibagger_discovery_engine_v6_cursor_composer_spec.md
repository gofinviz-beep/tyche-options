# Multi-Bagger Discovery Engine v6

## Cursor Composer 2.5 implementation playbook for `tyche-options-main`

Audience: Cursor Composer 2.5 Fast, operating with the `tyche-options-main` repo open.

This is the final curated build sheet. It merges the original Directional Alpha documentation, the v2/v3/v4/v5 companion recommendations, and a repo-wide code review. It is not an investment thesis and not financial advice. It is an engineering specification for converting Directional Alpha from a conservative precision filter into a recall-first multi-bagger discovery engine while preserving the current conservative path.

Primary idea:

> Do not penalize extension. Penalize price that has outrun validated demand, measured within the right peer, size, and coverage tier. If validated demand is still accelerating faster than price, surface the name, but route it to the correct entry, sizing, and risk state.

---

## 0. How Composer must use this document

### 0.1 Agent contract

Follow these rules exactly:

1. Execute tasks in order unless the user explicitly directs otherwise.
2. One task equals one commit-sized diff.
3. Default behavior of the existing Directional Alpha page must remain unchanged unless discovery flags are enabled.
4. Every behavior change must be gated behind a config flag or isolated in a new discovery path.
5. Do not refactor unrelated files.
6. Do not rename public routes, dataclasses, schemas, or stores unless the task says so.
7. If an anchor does not match, stop and report the mismatch. Do not guess.
8. Do not change the point-in-time fundamentals merge in `ml/features.py`: it uses `filing_dt` with backward `merge_asof`; that is correct and leakage-safe.
9. Preserve train/serve feature parity. Any feature added at training must exist at inference with the same name and units.
10. Add or update tests with every behavior-changing task.

### 0.2 Stop conditions

Stop and report rather than improvising when:

- A referenced file or symbol is missing.
- A task would change conservative behavior with all discovery flags off.
- A training-only feature cannot be produced at inference.
- A store schema change would break existing readers instead of being additive.
- A requested feature needs unavailable data and no graceful fallback exists.
- A validation task would let LLM or secondary-news claims directly raise a score.

### 0.3 Commit style

Use commit messages like:

- `audit: add alpha funnel diagnostic`
- `ml: add rare-class weighting for alpha targets`
- `alpha: add demand-adjusted extension behind flag`
- `features: add D-FLOW volume features for discovery`
- `discovery: add evidence event store`

---

## 1. Repo truths confirmed from code

These are the anchors Composer should keep in working memory. Line numbers can drift; match the quoted symbols and code shape.

### 1.1 Current scoring bottleneck

`backend/src/tyche/strategy/alpha_engine.py`

- `HORIZON_TARGETS` maps swing/trend/thematic to `big_move_up_25pct_40d`, `big_move_up_40pct_60d`, and `big_move_up_60pct_120d`.
- Composite weights are `_ML_WEIGHT = 0.55` and `_FACTOR_WEIGHT = 0.45`.
- Anti-chase uses `_OVEREXTENSION_FLOOR = 0.55`.
- Demand multiplier is clamped to `[0.70, 1.30]`.
- Thresholds default to `strong_buy=72`, `buy=58`, `watch=44`.
- `score_from_features()` does:

```python
composite = _ML_WEIGHT * ml_blend + _FACTOR_WEIGHT * factor_blend
penalty = 1.0 - (1.0 - _OVEREXTENSION_FLOOR) * overext
composite *= penalty
demand_mult = max(_DEMAND_MULT_FLOOR, min(_DEMAND_MULT_CEIL, 1.0 + _DEMAND_SENSITIVITY * dims.net))
composite = min(1.0, composite * demand_mult)
alpha_score = round(100.0 * composite, 1)
```

This is why deflated ML probabilities can cap good names below `buy` even when factor scores are strong.

### 1.2 Current ML training gaps

`backend/src/tyche/ml/xgb_baseline.py`

- `_DEFAULT_XGB_PARAMS` has no `scale_pos_weight`.
- `walk_forward_evaluate()` fills `X_train` and `X_test` with `-999` and fits without sample weights.
- `train_production_model()` fills with `-999` and fits without class weights.
- `demand_feature_columns()` is the conservative demand feature list. Discovery features should be a separate helper, not a mutation of this list.

### 1.3 Current labels do not train for 2x to 8x

`backend/src/tyche/ml/labels.py`

Current `BIG_MOVE_SPECS` are:

```python
[(40, 25.0), (60, 40.0), (120, 60.0)]
```

These are useful breakout labels, but they are not multi-bagger labels. Discovery needs separate `+100%`, `+200%`, `+400%` and path-aware labels.

### 1.4 Current estimate units have a likely live scoring bug

`ml/features.py` stores `e_eps_revision_90d` and `e_rev_revision_90d` as percent-points because `_pct_change_arr()` multiplies by `100.0`.

`alpha_engine.py` currently ramps them like fractions:

```python
eps_rev = _ramp(rev90, -0.05, 0.10)
rev_rev = _ramp(row.get("e_rev_revision_90d"), -0.05, 0.10)
```

Discovery work should fix this to percent-point bounds, after confirming `e_eps_surprise_avg4` units.

### 1.5 Current fundamentals merge is correct

`ml/features.py` uses `pd.merge_asof(..., left_on="_date", right_on="filing_dt", direction="backward")` for fundamentals. Do not change this to period-end.

### 1.6 Current options are underused

`ml/features.py` only merges derived volatility fields: `iv_rank`, `iv_percentile`, `atm_iv`, `vrp`, `rv_20d`.

Actual available stores:

- `market_data/options_history_store.py` has daily per-contract option bars with date, option ticker, underlying, expiration, strike, option type, OHLC, volume, transactions, and DTE. It does not include open interest, Greeks, or IV.
- `market_data/data_store.py` `OptionsChainStore` has chain snapshots with bid, ask, mid, last, volume, open interest, implied volatility, delta, gamma, theta, vega, rho, and underlying price.
- `workflow/options_snapshot.py` defaults `puts_only=True`, which is wrong for directional call-flow discovery unless changed or a separate discovery snapshot job is added.

Therefore D-FLOW must be phased:

- v1 from `OptionsHistoryStore`: call volume, put volume, call/put volume ratio, dollar-volume proxies, options-vs-stock volume, repeat call activity, near-expiry call ratio.
- v2 from `OptionsChainStore`: open-interest persistence, skew, term structure, premium ratios, delta-bucket demand, but only if daily broad call+put snapshots exist.

### 1.7 Current EDGAR/Form 4 path is valuable but disconnected

- `workflow/edgar_pipeline.py` ingests 8-K and Form 4, classifies 8-Ks, and rebuilds filing signals.
- `market_data/form4_parser.py` parses structured insider transactions.
- `market_data/filing_signals.py` computes `insider_net_shares_30d`, buy/sell counts, and cluster sell.
- This path does not become alpha `cat_*`, D-SMART, D-RISK, or evidence-ledger features.

The bridge is a high-value addition.

### 1.8 Current product surface is a score table

- `api/routes/alpha.py` exposes `GET /alpha/scan`, `GET /alpha/signal/{ticker}`, and `POST /alpha/recompute`.
- `AlphaSignalStore` writes `alpha_signals.parquet` and `alpha_signals_sustained.parquet`.
- `frontend/src/pages/stocks/Alpha.tsx` is a ranked table with expanded factor and demand details.

Discovery needs a parallel cockpit: state, evidence, theme, diagnostics, and risk mode.

---

## 2. Target architecture

Do not replace Directional Alpha. Add Discovery beside it.

### 2.1 Conservative path

Purpose: precision-oriented held-to-horizon breakout signals.

Keep:

- peak/sustained variants;
- existing alpha snapshots;
- existing `/stocks/alpha` behavior;
- existing `strong_buy/buy/watch/avoid` thresholds when discovery flags are off;
- existing `demand_feature_columns()`.

### 2.2 Discovery path

Purpose: recall-first discovery of asymmetric candidates, then precision recovery downstream.

Add:

- discovery-specific feature list;
- discovery model artifact namespace;
- evidence ledger;
- validation gate;
- demand-adjusted extension;
- percentile-based ranking;
- multi-bagger labels;
- state machine;
- P&L/top-k backtest;
- discovery cockpit.

### 2.3 Discovery state machine

Do not collapse selection, entry, and risk into one score.

States:

```text
Ignore -> Monitor -> Validate Now -> Wave Watchlist -> Buy Candidate
       -> Pursue Despite Extension -> Wait for Entry -> Second-Chance Entry
       -> De-risk -> Disqualified
```

Routers:

- Selection: Is there validated, accelerating demand?
- Continuation: Is evidence still improving or decaying?
- Entry: Buy now, stage, pullback watch, breakout continuation, or second-chance.
- Risk: normal, reduced, defined-risk, no-trade, de-risk, or disqualified.

### 2.4 Validation gate

Mandatory rule:

> LLM output, secondary news, and 13F articles may set state to `Validate Now`, but they may not increase score until verified by deterministic checks against primary filings or paid vendor data.

Examples:

- A secondary article claims Micron guidance is up 200%: create evidence event and validation task; do not raise score until Benzinga/Finnhub/filing data confirms.
- A famous manager owns a stock: structural context only; apply staleness, crowding, price move since effective date, and possible exit risk.
- A Form 4 cluster buy: closer to timing evidence, but still validate transaction code, acquisition/disposition, insider role, and dollar value.

---

## 3. Phase order

Build in this order:

```text
P0 Diagnostics, no behavior change
P1 Mechanical unblock, gated
P2 Discovery labels and model namespace
P3 D-EST, D-CAT, D-FLOW, and peer-tier normalization
P4 Evidence ledger and EDGAR/Form 4 bridge
P5 D-SMART and D-RISK
P6 Dynamic theme/cohort graph and event-driven rescore
P7 Discovery state machine, API, and UI
P8 P&L backtest and acceptance gate
```

P0 and P1 are not optional. They explain why the current system surfaces only a few names and prevent later product work from sitting on a broken score distribution.

---

## 4. Phase P0 - Diagnostics only

No behavior changes. No model changes. No UI changes.

### Task P0.1 - Add funnel diagnostic script

File: `backend/scripts/audit_alpha_funnel.py`

Goal: Show where the universe collapses.

Requirements:

- Read latest `AlphaSignalStore(variant="sustained")`; fall back to `peak` only if sustained is missing.
- Emit counts:
  - universe
  - has `f_rev_growth_yoy`
  - has `e_eps_revision_90d`
  - has `cat_demand_score`
  - has `si_days_to_cover`
  - has ML probabilities
  - `alpha_score >= 44`
  - `alpha_score >= 58`
  - `alpha_score >= 72`
- Print top 25 by `alpha_score` with ticker, score, signal, regime, `overextension_penalty`, `demand_multiplier`, and demand net.
- Write `data/ml/alpha_results/funnel_audit.json`.

Verify:

```bash
cd backend
python scripts/audit_alpha_funnel.py
```

Commit:

```text
audit: add alpha funnel diagnostic
```

### Task P0.2 - Add missed-winners probe

File: `backend/scripts/audit_missed_winners.py`

Goal: Push known winners through the current system and explain why they did or did not surface.

Requirements:

- Default tickers: `MU`, `AVGO`, `SNDK`, `STX`, `ARM`; support `--tickers` override.
- Modes:
  - `--source snapshot`: read stored row from `AlphaSignalStore`.
  - `--source engine`: rebuild latest features for those tickers, run predictor, and score with `AlphaScoreEngine`.
- Output per ticker:
  - `ml_blend`
  - `factor_blend`
  - `composite_before_penalty`
  - `overextension_penalty`
  - `demand_multiplier`
  - `alpha_score`
  - `signal`
  - `regime`
  - missing demand dimensions
  - `killed_by`: `low_ml_prob`, `anti_chase`, `missing_demand`, `below_threshold`, `not_in_snapshot`.
- Write `data/ml/alpha_results/missed_winners.csv`.
- Optional CLI: `--low-ml-threshold` (default `0.30`), `--anti-chase-threshold` (default `0.80`), `--missing-demand-threshold` (default `3`).
- Engine mode: exact counterfactual columns (`composite_after_penalty`, `composite_final`, `score_without_overextension_penalty`, `score_with_neutral_demand_multiplier`, `score_before_penalty_and_demand`, `counterfactual_mode=exact`).
- Snapshot mode: best-effort counterfactuals from stored penalty/multiplier (`counterfactual_mode=approx_from_snapshot`).
- Add `score_percentile_within_probe` (local rank within the probe list only).

`killed_by` labels are diagnostic bookkeeping. Make thresholds configurable, but do not treat a change in `killed_by` tallies as a changed finding. The measured fields — ML blend, factor blend, overextension penalty, demand multiplier, composite values, and score — are the source of truth.

Verify:

```bash
cd backend
python scripts/audit_missed_winners.py --source snapshot
python scripts/audit_missed_winners.py --source engine --tickers MU AVGO SNDK STX ARM
```

Commit:

```text
audit: add missed-winners probe
```

### Task P0.3 - Estimate revision coverage audit

**File (new):** `backend/scripts/audit_estimates_coverage.py`

**Goal:** Determine why `e_eps_revision_90d` and `e_rev_revision_90d` are empty or unpopulated in latest feature rows before applying any estimate-ramp unit fix.

**Rules:** Read-only. Do not ingest data. Do not modify feature code. Do not change `_est_quality` ramp bounds in this task.

**Outputs:**

- `data/ml/alpha_results/estimates_coverage_audit.csv`
- `data/ml/alpha_results/estimates_coverage_summary.json`

**Verify:** Run on known AI-infra tickers and a 250-ticker sample. The report must classify precise root causes (not broad `feature_builder_join_miss`): `no_estimate_store`, `empty_estimate_store`, `store_has_only_latest_snapshot`, `missing_eps_rev_estimate_metrics`, `front_period_selection_failure`, `missing_current_estimate_value`, `missing_90d_prior_estimate_value`, `prior_value_zero_or_invalid`, `revision_feature_assignment_bug`, `revision_columns_populated`, or `revision_population_unknown`. Compare manual `eps_revision_manual_90d` / `rev_revision_manual_90d` (same logic as `add_estimate_features`) against feature columns.

**Commit:** `audit: add estimates coverage and revision-population probe`

### Task P0.4 - Add conservative fixture capture

File: `backend/tests/fixtures/alpha_conservative_fixture.json` and helper script if useful.

Goal: Before changing scoring, capture a small deterministic fixture so all later gated changes can prove conservative invariance.

Requirements:

- Use a synthetic DataFrame, not live vendor data.
- Cover revenue regime, narrative regime, missing estimates, high overextension, no ML, and ML-present cases.
- Store expected signals/scores with all discovery flags off.

Verify:

```bash
cd backend
pytest tests -k alpha_conservative
```

Commit:

```text
test: add conservative alpha scoring fixture
```

---

## 5. Phase P1 - Mechanical unblock, gated

P1 fixes the score distribution and visibility problem. All behavior changes must be behind flags or discovery-only paths.

**Recommended P1 execution order after patched P0:**

1. P1.1 config flags.
2. P1.2 per-target class weighting.
3. P1.5 percentile discovery signals.
4. P0.3 / P1.5-A estimate revision coverage and population diagnosis.
5. P1.8 guidance tail preservation.
6. P1.6 demand-adjusted extension.
7. P1.3 missingness indicators with strict train/serve parity.
8. P1.9 purged/embargoed walk-forward.
9. P1.5-B estimate unit/ramp fix only after real populated values prove the units (do not apply `(-5.0, 10.0)` until P1.5-A shows populated revisions and quantiles).

### Task P1.1 - Add discovery config flags

File: `backend/src/tyche/config.py`

Add near existing alpha settings:

```python
# --- Directional Alpha discovery mode ---
alpha_discovery_enabled: bool = False
alpha_percentile_signals_enabled: bool = False
alpha_demand_adjusted_extension_enabled: bool = False
alpha_peer_tier_normalization_enabled: bool = False
alpha_class_weighting_enabled: bool = True
alpha_purged_walk_forward_enabled: bool = True
alpha_discovery_train_min_market_cap_millions: float = 250.0
alpha_demand_mult_ceil_discovery: float = 1.45
alpha_discovery_snapshot_enabled: bool = False
```

Note:

- Defaults must not change existing page behavior.
- `alpha_class_weighting_enabled` is training-only. It can default true because it only affects new trainings, but allow scripts to override.

Verify:

```bash
cd backend
python - <<'PY'
from tyche.config import get_settings
s = get_settings()
print(s.alpha_discovery_enabled, s.alpha_class_weighting_enabled)
PY
```

Commit:

```text
config: add alpha discovery mode flags
```

### Task P1.2 - Add per-target class weighting

File: `backend/src/tyche/ml/xgb_baseline.py`

Goal: Stop systematically deflating probabilities on rare labels.

Production train change:

After `y = valid[target]`, add:

```python
pos = float((y == 1).sum())
neg = float((y == 0).sum())
if pos > 0 and "scale_pos_weight" not in params:
    params["scale_pos_weight"] = min(max(neg / pos, 1.0), 50.0)
```

Walk-forward change:

Inside the per-fold loop, after `y_train = train_df[target].copy()` and before model creation:

```python
if not is_multiclass:
    _pos = float((y_train == 1).sum())
    _neg = float((y_train == 0).sum())
    fold_params = dict(params)
    if _pos > 0:
        fold_params["scale_pos_weight"] = min(max(_neg / _pos, 1.0), 50.0)
else:
    fold_params = dict(params)
```

Then use:

```python
model = xgb.XGBClassifier(**fold_params)
```

Do not put `scale_pos_weight` into `_DEFAULT_XGB_PARAMS`, because multiclass models share those defaults.

Also log `scale_pos_weight` per target/fold at info level for demand/discovery runs.

Verify:

```bash
cd backend
python scripts/run_demand_gate.py --max-tickers 40 --no-promote
```

Expected: logs show `scale_pos_weight > 1` for rare sustained targets.

Commit:

```text
ml: add per-target rare-class weighting for alpha labels
```

### Task P1.3 - Add missingness indicators without train/serve mismatch

File: `backend/src/tyche/ml/xgb_baseline.py` plus inference parity in `ml/breakout.py` or feature builder.

Goal: Let XGBoost distinguish `missing analyst coverage` from `bad analyst signal`.

Coverage-sensitive columns:

```python
[
    "e_eps_revision_90d",
    "e_rev_revision_90d",
    "e_rec_score",
    "e_price_target_upside",
    "f_rev_growth_yoy",
    "si_days_to_cover",
]
```

Implementation rules:

- Add helper `add_missingness_indicators(X, cols)` that adds `f"{c}__isna"` for columns present in X.
- Apply it before `fillna(-999)`.
- Persist augmented feature columns in model metadata.
- At inference, create the same indicator columns before selecting `meta.feature_cols`.
- If inference parity is not guaranteed, do not ship this task; report the mismatch.

Preferred implementation:

- Put the helper in `ml/features.py` or `ml/xgb_baseline.py` and reuse it from `ml/breakout.py`.
- Apply to discovery models first if conservative artifact parity is risky.

Verify:

```bash
cd backend
python scripts/run_demand_gate.py --max-tickers 40 --no-promote
python - <<'PY'
# Load a trained/discovery meta after a smoke train and assert __isna columns exist.
PY
```

Commit:

```text
ml: add coverage missingness indicators with train-serve parity
```

### Task P1.5-A - Fix estimate-revision population

**Goal:** Make `e_eps_revision_90d` and/or `e_rev_revision_90d` populate for covered tickers where the store has valid current and 90-day-prior EPS/revenue estimate values.

**Rules:**

- Do not change `_est_quality` ramp bounds in this task.
- Use `audit_estimates_coverage.py` to confirm the failure mode (missing metrics, missing as-of values, assignment bug, etc.) before changing feature code.

**Verify:** `audit_estimates_coverage.py --sample-size 250` shows `revision_populated_count > 0`; summary includes real quantiles for populated revision columns; several known winners have non-null manual and feature revision values.

**Commit:** `features: populate estimate revision columns from available history`

### Task P1.5-B - Estimate revision unit/ramp fix (gated; after P1.5-A only)

**Goal:** Change `_est_quality` ramp bounds only after revision columns populate and units are verified.

**Hard rule:** Do not apply `(-5.0, 10.0)` until P1.5-A succeeds. Print p05/p50/p95/p99 and example ticker values first. Use fractional bounds, percent-point bounds, or winsorized transforms according to observed units.

**Verify:** Unit test matches observed convention; a realistic +8% revision produces a graded `_est_quality`, not a pinned constant.

**Commit:** `fix: align estimate revision ramps to observed feature units`

### Task P1.5 - Add percentile-based discovery signals

File: `backend/src/tyche/strategy/alpha_engine.py`

Goal: Discovery should always produce a ranked funnel even when absolute probability scales shift.

Rules:

- Conservative path unchanged.
- Add optional `percentile_signals: bool = False` to `AlphaScoreEngine.__init__`.
- Add `score_percentile: float | None = None` to `AlphaSignal`.
- With `percentile_signals=False`, use existing `_classify_signal()` unchanged.
- With `percentile_signals=True`, after all scores are computed for the batch:
  - top 1% => `strong_buy`
  - top 1-5% => `buy`
  - top 5-15% => `watch`
  - rest => `avoid`

Implementation note:

- `score_from_features()` currently classifies inside the loop. Keep that for conservative path; add a second pass only when `percentile_signals` is true.

Test:

- With flag off, output is byte-identical to fixture.
- With flag on and a 500-row synthetic batch, about 5 rows are `strong_buy`.

Commit:

```text
alpha: add percentile-based discovery signal classification
```

### Task P1.6 - Add demand-adjusted extension

File: `backend/src/tyche/strategy/alpha_engine.py`

Goal: Replace pure price-extension penalty with demand-relative extension when enabled.

Initial DAE lift may be modest because current `dims.net` can understate demand while D-EST is empty and D-CAT/D-FLOW/D-SMART are incomplete. Do not judge DAE only on first-pass score lift. Its purpose is to remove the price-only anti-chase guillotine now and become stronger as validated demand features are added.

Add optional constructor arg:

```python
demand_adjusted_extension: bool = False
```

Current path remains:

```python
penalty = 1.0 - (1.0 - _OVEREXTENSION_FLOOR) * overext
```

Discovery path:

```python
price_mom = 2.0 * factors.momentum - 1.0
demand_mom = dims.net
dae = max(-1.0, min(1.0, price_mom - demand_mom))
penalty = 1.0 - 0.45 * max(0.0, dae) + 0.30 * max(0.0, -dae)
penalty = max(_OVEREXTENSION_FLOOR, min(1.30, penalty))
```

Important:

- Compute `dims` before the penalty in this path. The current code computes `dims` after anti-chase; reorder carefully without changing conservative output.
- Store `demand_adjusted_extension` on `AlphaSignal` if discovery is enabled.
- When demand evidence is absent, the logic should not produce accidental bonus.

Discovery demand multiplier:

- Keep `[0.70, 1.30]` for conservative.
- In discovery, allow ceiling `settings.alpha_demand_mult_ceil_discovery`, but only when at least two independent demand dimensions are present.

Test:

- Two equally extended synthetic rows:
  - row A `dims.net=+0.8` => penalty or multiplier effect above 1.0.
  - row B `dims.net=-0.5` => below 1.0.
- With flag off, scores equal fixture.

Commit:

```text
alpha: add gated demand-adjusted extension
```

### Task P1.7 - Align train/live universe floors

Files:

- `backend/scripts/run_demand_gate.py`
- `backend/scripts/train_alpha.py`

Current script defaults are around `$4B` while live alpha can build down to `$250M`.

Change:

- Do not silently train the conservative model at $250M.
- Make floor explicit and log it.
- Recommend default script floor of `$2B` for conservative smoke/full runs.
- Add a separate discovery training flag or script path for `$250M-$500M`.

Example help text:

```python
parser.add_argument(
    "--min-market-cap",
    type=float,
    default=2e9,
    help="Min market cap; use 250e6-500e6 for discovery training, not conservative retrain",
)
```

Log:

```python
logger.info("train_universe", min_market_cap=args.min_market_cap)
```

Commit:

```text
train: surface alpha train universe floor
```

### Task P1.8 - Preserve guidance tail magnitude

File: `backend/src/tyche/market_data/benzinga.py`

Current:

```python
impact = min(1.0, max(0.1, abs(pct) / full_pct))
```

Problem:

- trivial moves get a floor;
- large outliers saturate like routine beats;
- no raw delta survives into features.

Change impact transform:

```python
impact = math.tanh(math.log1p(abs(pct) / full_pct))
```

Why this transform:

- no artificial 0.1 floor;
- monotonic;
- separates routine from extreme better than a hard clip;
- bounded for model stability.

Also persist raw delta:

- Add `raw_delta_pct` to guidance-derived catalyst records if store changes are additive.
- If changing `CatalystSignalStore` risks compatibility, put raw numeric facts in the new `EvidenceEventStore` first and leave legacy `cat_*` unchanged.

Test values with `full_pct=0.10`:

- `pct=0.005` should be near 0.
- `pct=0.05` < `pct=0.11` < `pct=0.50` < `pct=2.00`.

Commit:

```text
benzinga: use tail-preserving guidance impact transform
```

### Task P1.9 - Add purged/embargoed walk-forward splits

File: new `backend/src/tyche/ml/validation.py` and integration in `ml/xgb_baseline.py`

Goal: Prevent overlapping-label leakage. Sustained labels look forward as far as 120 trading days; discovery labels can look forward 252 to 756 days.

Add:

```python
def purged_walk_forward_splits(dates, train_days, test_days, embargo_days):
    ...
```

Invariant:

```text
max(train_date) + embargo_days <= min(test_date)
```

Target horizon parsing:

- Parse trailing `_{N}d` from target name.
- Use that as embargo unless caller passes explicit override.

Gating:

- Use purged splitter when `alpha_purged_walk_forward_enabled` or script flag is on.
- Keep old splitter available for comparison.

Test:

```bash
cd backend
pytest tests -k purged_walk_forward
```

Commit:

```text
ml: add purged walk-forward validation splits
```

---

## 6. Phase P2 - Discovery labels and model namespace

### Task P2.1 - Add multi-bagger labels

File: `backend/src/tyche/ml/labels.py`

Add without altering current `BIG_MOVE_SPECS`:

```python
MULTIBAGGER_SPECS = [
    (252, 100.0),
    (504, 200.0),
    (756, 400.0),
]
```

Emit:

- `big_move_sustained_100pct_252d`
- `big_move_sustained_200pct_504d`
- `big_move_sustained_400pct_756d`
- `max_forward_return_252d`
- `max_forward_return_504d`
- `max_forward_return_756d`
- `hit_target_before_stop_100pct_252d_35dd`
- `hit_target_before_stop_200pct_504d_35dd`
- `time_to_100pct_252d`
- `max_drawdown_before_hit_100pct_252d`

Do not add these to the conservative target list.

Commit:

```text
labels: add discovery multi-bagger and path-aware labels
```

### Task P2.2 - Separate discovery model artifacts

Files:

- `ml/xgb_baseline.py`
- `ml/model_store.py`
- `ml/breakout.py`
- scripts

Add:

```python
ALPHA_DISCOVERY_TARGETS = [
    "big_move_sustained_100pct_252d",
    "big_move_sustained_200pct_504d",
    "big_move_sustained_400pct_756d",
    "hit_target_before_stop_100pct_252d_35dd",
]
```

Artifact namespace:

```text
data/ml/models/discovery/{target}.json
data/ml/models/discovery/{target}_meta.json
```

Do not overwrite:

```text
data/ml/models/big_move_up_*.json
data/ml/models/big_move_sustained_*.json
```

Implementation option:

- Add optional `namespace: str | None = None` to `save_model()` and `load_model()`.
- Namespace changes model directory only.

Commit:

```text
ml: add discovery model namespace
```

### Task P2.3 - Add discovery payoff blend

File: new `strategy/discovery_scoring.py` or inside `strategy/discovery_engine.py`

Initial simple payoff score:

```text
payoff = 0.25*P(+25%) + 0.60*P(+60%) + 1.00*P(+100%) + 2.00*P(+200%) + 4.00*P(+400%)
```

Use this as one component of `discovery_score`, not as the whole decision.

Commit:

```text
discovery: add payoff-weighted model blend
```

---

## 7. Phase P3 - Discovery feature families

Add these features to a new helper, not to `demand_feature_columns()`:

```python
def discovery_feature_columns() -> list[str]:
    ...
```

### Task P3.1 - D-EST velocity, breadth, dispersion

File: `backend/src/tyche/ml/features.py`

Add percent-point features with explicit names:

- `e_eps_revision_7d_pct`
- `e_eps_revision_14d_pct`
- `e_eps_revision_30d_pct`
- `e_rev_revision_7d_pct`
- `e_rev_revision_14d_pct`
- `e_rev_revision_30d_pct`
- `e_revision_acceleration_pct`
- `e_revision_breadth_30d`
- `e_dispersion_change_30d_pct`
- `e_post_event_revision_response_pct`

Rules:

- Keep units in names.
- Do not mix fractions and percent-points.
- Missing coverage should become missing, not negative.

Commit:

```text
features: add D-EST velocity and breadth for discovery
```

### Task P3.2 - D-CAT split and tail-preserving event features

Files:

- `market_data/benzinga.py`
- `market_data/catalyst_store.py` if additive
- `ml/features.py`

Add features:

- `cat_guide_vs_consensus_pct`
- `cat_guide_vs_consensus_raw`
- `cat_yoy_implied_growth_pct`
- `cat_high_magnitude_event_score`
- `cat_positive_event_count_30d`
- `cat_positive_event_count_90d`
- `cat_negative_event_count_30d`
- `cat_negative_event_count_90d`
- `cat_event_magnitude_max_180d`
- `cat_event_source_quality_max_180d`

Keep legacy `cat_demand_score`, `cat_policy_score`, `cat_count_90d`, and `cat_recency_days`.

Commit:

```text
features: split catalyst magnitude and counts for discovery
```

### Task P3.3 - D-FLOW v1 from options history

Files:

- `market_data/options_history_store.py`
- `ml/features.py`
- `ml/dataset.py`

Add `add_flow_features()`.

Buildable from `OptionsHistoryStore`:

- `flow_call_volume_z_20d`
- `flow_put_volume_z_20d`
- `flow_call_put_volume_ratio`
- `flow_call_dollar_volume_z_20d`
- `flow_options_vs_stock_volume`
- `flow_near_expiry_call_ratio`
- `flow_repeat_call_activity_5d`
- `flow_call_transactions_z_20d`

Implementation notes:

- Use option `close * volume * 100` as a rough premium/dollar-volume proxy.
- Aggregate by underlying/date first, then backward as-of into feature rows.
- For names without options data, output NaN or neutral values consistently; do not penalize no-options small caps unless risk logic says liquidity is insufficient.
- Add to `discovery_feature_columns()` only.

Commit:

```text
features: add D-FLOW volume features from options history
```

### Task P3.4 - D-FLOW v2 from chain snapshots

Files:

- `workflow/options_snapshot.py`
- `market_data/data_store.py` `OptionsChainStore`
- `ml/features.py`

Prerequisite:

- Ensure daily snapshots include calls and puts. Current workflow defaults `puts_only=True`; discovery flow needs `puts_only=False` for the relevant universe.

Features:

- `flow_oi_change_persistence_5d`
- `flow_call_put_premium_ratio`
- `flow_25_delta_call_skew`
- `flow_term_structure_shift`
- `flow_delta_bucket_call_demand`
- `flow_iv_up_call_volume_up`

If daily call+put snapshots are not available, skip v2 and keep v1.

Commit:

```text
features: add D-FLOW chain snapshot features
```

### Task P3.5 - Peer-tier normalization

File: new `backend/src/tyche/ml/peer_tiers.py` and integration in discovery scoring.

Goal: Compare small/no-coverage names against similar names, not against mega-cap winners.

Suggested tiers:

- market-cap bucket: micro/small/mid/large/mega;
- sector;
- regime: revenue/narrative;
- coverage tier: no estimates, sparse estimates, covered;
- options coverage: liquid options, sparse options, no options.

Outputs:

- `tier_id`
- `price_mom_tier_z`
- `demand_mom_tier_z`
- `flow_tier_z`
- `estimate_revision_tier_z`
- `cat_magnitude_tier_z`

Use these in demand-adjusted extension once available:

```text
dae = clip(price_mom_tier_z - demand_mom_tier_z, -1, 1)
```

Commit:

```text
ml: add peer-tier normalization for discovery
```

---

## 8. Phase P4 - Evidence ledger and EDGAR/Form 4 bridge

### Task P4.1 - Add EvidenceEventStore

File: `backend/src/tyche/market_data/evidence_store.py`

Schema fields:

```python
{
    "evidence_id": str,
    "tickers": list[str],
    "theme_ids": list[str],
    "event_type": str,
    "source": str,
    "source_quality": str,  # primary | paid_vendor | secondary | rumor
    "event_date": date,
    "ingest_date": datetime,
    "effective_date": date | None,
    "claim_text": str,
    "numeric_facts": dict,
    "validation_status": str,  # unverified | verified | contradicted | stale
    "decay_half_life_days": float,
    "confidence": float,
    "linked_feature_names": list[str],
    "ref_id": str,
}
```

Store layout:

```text
data/evidence_events/{TICKER}.parquet
```

Rules:

- Additive only; do not modify `CatalystSignalStore` yet unless needed.
- Dedupe by `(source, ref_id, event_type, ticker)`.
- Provide `read_ticker()`, `write_records()`, `read_unverified()`, and `mark_validated()`.

Commit:

```text
evidence: add EvidenceEventStore
```

### Task P4.2 - LLM claim extraction writes unverified evidence

Files:

- `analysis/news_classifier.py`
- new helper `analysis/thesis_extractor.py` if cleaner
- evidence store integration in workflow

Do not replace the existing classifier. Add a discovery-specific extractor.

Extract:

- theme
- actors and roles: beneficiary, supplier, customer, source-of-funds, competitor, bellwether
- claims
- numeric facts
- dates: article date, filing date, holding-as-of date, effective date
- validation tasks
- source quality

Output unverified EvidenceEvents.

Rule:

- LLM output can move product state to `Validate Now`.
- LLM output cannot raise score.

Commit:

```text
evidence: write unverified thesis events from news extraction
```

### Task P4.3 - Deterministic validation workers

File: new `backend/src/tyche/workflow/evidence_validation.py`

Validation sources:

- Finnhub estimates/fundamentals;
- Benzinga guidance via Massive;
- EDGAR 8-K/10-Q/10-K text/metadata;
- Form 4 parser;
- options stores;
- OHLCV reaction if needed.

Validation outputs:

- `verified`
- `contradicted`
- `stale`
- keep `unverified` if data is not present.

Commit:

```text
evidence: add deterministic validation workflow
```

### Task P4.4 - EDGAR 8-K to evidence and catalyst bridge

Files:

- `workflow/edgar_pipeline.py`
- `market_data/filing_store.py`
- `market_data/evidence_store.py`
- `ml/features.py`

Map classified 8-Ks to EvidenceEvents. For verified primary-source positives, bridge to discovery catalyst features.

Important event types:

- earnings/results;
- guidance;
- material agreement;
- contract win;
- customer win;
- backlog/RPO;
- capacity sold out;
- offering/shelf/ATM;
- legal/regulatory material events.

Feature outputs:

- `cat_validated_primary_score_90d`
- `f_backlog_yoy`
- `f_rpo_yoy`
- `f_book_to_bill`
- `risk_shelf_registration`
- `risk_atm_program`
- `risk_secondary_offering`
- `risk_convertible_issuance`

Commit:

```text
evidence: bridge EDGAR events into discovery features
```

### Task P4.5 - Form 4 cluster buy features

Files:

- `market_data/filing_signals.py`
- `ml/features.py`

Current code detects cluster sells. Add cluster buys.

Features:

- `insider_cluster_buy_30d`
- `insider_net_buy_value_30d`
- `insider_buy_count_30d`
- `insider_sell_pressure_90d`
- `insider_role_weighted_buy_value_30d`

Rules:

- Transaction code `P` and acquisition/disposition `A` matter.
- Weight officers/directors/10% owners differently.
- Ignore awards/grants as buy evidence.

Commit:

```text
features: add Form 4 insider cluster buy features
```

---

## 9. Phase P5 - D-SMART and D-RISK

### Task P5.1 - D-SMART structural ownership flow

Add after Form 4 bridge, not before.

Sources:

- 13F;
- 13D/13G;
- static institutional ownership as fallback only.

Features:

- `sm_initiations_90d`
- `sm_adds_90d`
- `sm_exits_90d`
- `sm_net_flow_z`
- `sm_marquee_exit`
- `sm_staleness_days`
- `sm_price_move_since_effective_date`
- `sm_crowding_score`

Rules:

- 13F is structural context with roughly quarterly half-life.
- Never treat a 13F article as a same-day buy trigger.
- Adjust for public filing lag, effective date, price move since effective date, and possibility of exit.

Commit:

```text
features: add D-SMART structural ownership flow
```

### Task P5.2 - D-RISK hard disqualifiers

Files:

- `ml/features.py`
- `strategy/discovery_engine.py`
- EDGAR/evidence bridge

Features and actions:

- `risk_shelf_registration` -> De-risk or Disqualified if active and likely near-term.
- `risk_atm_program` -> De-risk.
- `risk_secondary_offering` -> De-risk.
- `risk_convertible_issuance` -> De-risk.
- `risk_share_count_acceleration` -> risk penalty.
- `risk_liquidity_trap` -> Disqualified for position sizes above liquidity capacity.
- `risk_customer_concentration` -> reduced size or wait for validation.
- `risk_hype_no_estimate_confirmation` -> Wait/Validate.
- `risk_policy_reversal` -> De-risk.

Commit:

```text
risk: add discovery disqualifier features and routing
```

---

## 10. Phase P6 - Theme graph and event-driven rescore

### Task P6.1 - Dynamic theme/cohort store

Files:

- new `market_data/theme_cohort_store.py`
- existing `market_data/supply_chain_graph.py`
- `ml/features.py`

Graph edge components:

```text
edge_weight = revenue_corr + news_co_mention + shared_customer + product_similarity + estimate_revision_corr + flow_corr
```

Features:

- `graph_theme_signal`
- `graph_theme_breadth`
- `graph_unreported_peer_lift`
- `graph_customer_capex_lag`
- `graph_crowding_score`
- `theme_momentum_30d`
- `theme_evidence_velocity_30d`

Goal:

- A verified catalyst on one memory/storage/AI-hardware member should lift peers before they report, with decay by edge weight.

Commit:

```text
features: add dynamic theme cohort graph
```

### Task P6.2 - Event-driven rescore

Files:

- `workflow/alpha_batch.py`
- new `workflow/discovery_rescore.py`
- API route for recompute

Current alpha batch is nightly. Discovery should also rescore affected tickers when material evidence arrives.

Trigger sources:

- verified guidance event;
- EDGAR 8-K;
- Form 4 cluster buy/sell;
- options flow spike;
- theme peer event;
- major estimate revision.

Output:

- update discovery snapshot for affected tickers;
- append state history;
- do not mutate conservative alpha snapshots unless current batch does.

Commit:

```text
discovery: add event-driven affected-ticker rescore
```

---

## 11. Phase P7 - Discovery engine, API, and UI

### Task P7.1 - DiscoverySignal dataclass and store

Files:

- new `strategy/discovery_engine.py`
- new `market_data/discovery_signal_store.py`

Dataclass:

```python
@dataclass
class DiscoverySignal:
    ticker: str
    discovery_score: float
    conservative_alpha_score: float | None
    score_percentile: float | None
    state: str
    entry_mode: str
    risk_mode: str
    thesis: str
    theme_ids: list[str]
    evidence_momentum: float
    demand_mom_tier: float | None
    price_mom_tier: float | None
    demand_adjusted_extension: float | None
    validation_summary: dict
    risk_flags: list[str]
    top_evidence_ids: list[str]
    model_probs: dict[str, float]
    expected_upside_capture: float | None
    max_drawdown_risk: float | None
```

Score formula v1:

```text
discovery_score =
    0.20 * thesis
  + 0.20 * evidence_momentum
  + 0.15 * expected_upside_capture
  + 0.15 * multibagger_payoff
  + 0.10 * continuation
  + 0.10 * theme_wave
  + 0.05 * breakout_timing
  + 0.05 * options_confirmation
  - risk_penalty
```

Commit:

```text
discovery: add signal dataclass and parquet store
```

### Task P7.2 - Discovery API

File: `backend/src/tyche/api/routes/alpha.py` or new `routes/discovery.py`

Additive endpoints:

```text
GET  /alpha/scan?mode=conservative|discovery
GET  /alpha/discovery/signal/{ticker}
GET  /alpha/evidence/{ticker}
GET  /alpha/theme/{id}
GET  /alpha/diagnostics/funnel
GET  /alpha/diagnostics/missed-winners
POST /alpha/discovery/recompute
```

Rules:

- Existing `GET /alpha/scan` must continue to work.
- If adding `mode`, default must preserve current behavior.
- Discovery responses should include state, entry mode, risk mode, top evidence, and validation status.

Commit:

```text
api: add discovery alpha endpoints
```

### Task P7.3 - Discovery frontend cockpit

Files:

- `frontend/src/pages/stocks/Alpha.tsx` or new page under stocks
- `frontend/src/types/index.ts`
- `frontend/src/hooks/useApi.ts`

UI blocks:

- Mode toggle: Conservative / Discovery.
- Discovery funnel: top 50-100, triage 15-25, action 3-10.
- State chips: Validate Now, Wave Watchlist, Pursue Despite Extension, Wait for Entry, De-risk, Disqualified.
- Evidence timeline: verified/unverified/contradicted/stale.
- Demand-adjusted extension readout: price momentum vs validated demand momentum.
- Theme/cohort panel.
- Risk flags/disqualifiers.
- Diagnostics links.

Do not replace the current table; add a discovery view.

Commit:

```text
frontend: add discovery cockpit for alpha signals
```

---

## 12. Phase P8 - P&L backtest and acceptance gate

### Task P8.1 - Discovery portfolio backtest

Files:

- new `backend/src/tyche/backtest/discovery_portfolio.py`
- new `backend/scripts/backtest_discovery.py`

Backtests:

- top 25, 50, 100 weekly baskets;
- hold 40, 60, 120, 252 trading days;
- equal-weight and volatility-weight;
- state-routed entry:
  - buy candidate now;
  - wait-for-entry uses pullback/breakout trigger;
  - de-risk/disqualified excluded;
- slippage and liquidity capacity;
- benchmarks: SPY, QQQ, sector ETF, momentum baseline, current conservative alpha.

Report:

- CAGR;
- max drawdown;
- Sortino/Sharpe if available;
- hit rate;
- average winner/loser;
- skew;
- turnover;
- slippage;
- future +100% and +200% capture rate;
- missed-winner reasons.

Acceptance gate:

- Do not promote discovery models based on AUC alone.
- Require top-k portfolio improvement and survivable drawdown.

Commit:

```text
backtest: add discovery top-k portfolio acceptance gate
```

---

## 13. Acceptance and regression tests

Add under `backend/tests/`.

Required tests:

1. Conservative invariance: with all discovery flags off, `score_from_features()` matches fixture.
2. Demand-adjusted extension: equally extended rows with opposite demand get opposite treatment.
3. Estimate units: +8 percent-point revision gives graded `_est_quality`.
4. Class weighting: binary rare target training sets `scale_pos_weight > 1`.
5. Missingness parity: any trained `__isna` feature exists at inference.
6. Percentile signals: top 1% of batch become `strong_buy` when enabled.
7. Purged split: every train/test split respects embargo.
8. No-coverage small cap: strong catalyst + growth acceleration + D-FLOW can reach Watchlist without estimates.
9. Validation gate: unverified 13F/news claim sets `Validate Now` but does not raise score.
10. Dilution disqualifier: active shelf/ATM routes to De-risk or Disqualified.
11. Evidence store compatibility: additive store schema does not break legacy catalyst reads.
12. Discovery artifact namespace: discovery training does not overwrite peak/sustained artifacts.

Frontend checks:

- Conservative alpha page still renders.
- Discovery tab renders with empty states when no discovery snapshot exists.
- Evidence timeline handles unverified and contradicted items.

---

## 14. Composer work packets

Use these as prompts or issue templates.

### Packet A - P0 diagnostics

```text
Implement only Phase P0 from multibagger_discovery_engine_v6_cursor_composer_spec.md.
Do not change scoring, models, feature builders, stores, routes, or frontend.
Add audit_alpha_funnel.py, audit_missed_winners.py, and a conservative scoring fixture.
Run the specified smoke commands and tests.
Stop if AlphaSignalStore or AlphaScoreEngine signatures differ from the spec.
```

### Packet B - P1 unblock

```text
Implement Phase P1 tasks one at a time. Preserve conservative behavior with all flags off.
Add config flags first. Then class weighting, missingness indicators with train/serve parity,
estimate unit fix, percentile signals, demand-adjusted extension, train universe logging,
guidance tail transform, and purged validation. Add tests for each behavior change.
Do not add discovery labels or UI in this packet.
```

### Packet C - Discovery models

```text
Implement Phase P2 only. Add multi-bagger/path-aware labels and discovery model artifact namespace.
Do not overwrite current peak or sustained model artifacts. Add smoke training support and tests.
```

### Packet D - Discovery features

```text
Implement Phase P3 only. Add discovery_feature_columns(), D-EST velocity, D-CAT split, D-FLOW v1,
and peer-tier normalization. Do not mutate demand_feature_columns(). Add feature coverage tests.
```

### Packet E - Evidence and validation

```text
Implement Phase P4 only. Add EvidenceEventStore, unverified LLM/secondary-news evidence capture,
deterministic validation workflow, EDGAR evidence bridge, and Form 4 cluster buy features.
Unverified evidence may change state but must not raise score.
```

### Packet F - Product surface

```text
Implement Phases P7 and P8 only after P0-P6 are merged. Add DiscoverySignal store, API endpoints,
frontend cockpit, and portfolio backtest. Existing /alpha/scan conservative behavior must remain unchanged.
```

---

## 15. Implementation priorities if time is limited

If implementing incrementally, the highest ROI sequence is:

1. Funnel audit and missed-winners probe.
2. Class weighting.
3. Estimate unit fix.
4. Percentile discovery signals.
5. Demand-adjusted extension.
6. Guidance tail transform.
7. D-FLOW v1 from options history.
8. Evidence validation gate.
9. EDGAR/Form 4 bridge.
10. Discovery state machine and top-k P&L backtest.

Do not start with UI. UI before P0/P1 will make the product look better without making the engine better.

---

## 16. Final design stance

Directional Alpha should remain the conservative precision engine.

Discovery should be a parallel recall-first engine that:

- does not exclude extended winners when demand is still accelerating;
- does not hide small/no-coverage names because analyst fields are missing;
- treats news claims as validation tasks, not facts;
- uses options flow, EDGAR, Form 4, guidance, estimates, and theme propagation as evidence;
- routes names into state, entry, and risk modes instead of one opaque score;
- proves value through top-k portfolio capture and drawdown-aware backtests.

The intended funnel is not two names. It is approximately:

```text
Discovery: 50-100 names
Triage: 15-25 names
Action: 3-10 names
```

The engine is successful when it can explain both sides of the same question:

- why an extended stock is still worth pursuing because validated demand is outrunning price;
- why another extended stock should be avoided because price has outrun stale or unverified demand.
