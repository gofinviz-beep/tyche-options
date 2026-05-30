# Directional Alpha Engine

A second signal engine focused on **large upside moves** ("10X" / big-move buys) — the
complement to the CSP / Covered Call income engine. Where the income engine harvests
premium near support, the alpha engine looks for quality names positioned to run hard to
the upside (the kind of move AMD, Micron, and several space names made in 2025–2026).

It does **not** replace any existing page or pipeline — it is purely additive.

## Three Horizons

Big-move targets are defined as forward returns over a window (`ml/labels.py`,
`BIG_MOVE_SPECS`):

| Horizon | Label | Target | Window |
|---|---|---|---|
| Swing | `big_move_up_25pct_40d` | +25% | ~40 trading days |
| Trend | `big_move_up_40pct_60d` | +40% | ~60 trading days |
| Thematic | `big_move_up_60pct_120d` | +60% | ~120 trading days |

Magnitude regression targets (`peak_recovery_pct_*`, `close_return_pct_*`) are also
produced for future calibration. Labels are built from **raw OHLCV only** (leakage
prevention) — no derived features flow into label construction.

## Features

Two opt-in feature groups, enabled via `get_feature_columns(include_momentum=True)`:

- **Momentum (`MOMENTUM_FEATURE_COLS`)** — `return_63d/126d/252d`, `ema_200` + slope,
  `price_to_200ema_pct`, `ema_stack_score` (8 > 21 > 50 > 200 alignment), `pct_off_52w_high`,
  `pct_above_52w_low`, `breakout_20d`, `breakout_63d`, `volume_thrust_ratio`, `slope_accel`.
- **Relative strength (`RS_FEATURE_COLS`)** — `rs_63d/126d/252d` vs SPY (`add_relative_strength_features`).

### Ablation note (May 2026)

A MACD-histogram + multi-timeframe (weekly / monthly / quarterly) trend-alignment group was
prototyped and walk-forward validated against the big-move targets. It produced only
**noise-level AUC lift (+0.0003 to +0.0005)** on top of the momentum / EMA-stack features —
i.e. redundant — and was dropped to keep the nightly feature pipeline lean. A `NOTE` comment
in `ml/features.py` records the negative result. Re-evaluate if/when fundamental or analyst
partner data is added, since the interaction may differ.

## ML Model — `BreakoutPredictor`

`ml/breakout.py` mirrors `CSPSafetyPredictor`:

- Loads the per-horizon big-move XGBoost artifacts from `data/ml/models/`.
- Bridges a `FeatureSignal` (+ a short OHLCV tail for the handful of momentum features not
  already on the signal) to `predict_proba`.
- Returns per-horizon probabilities; **gracefully degrades to `None`** when no artifact
  exists (the engine then runs rules-only).

Train with `python scripts/train_alpha.py --save-model` (walk-forward evaluation +
production model persistence).

## AlphaScoreEngine

`strategy/alpha_engine.py` composites deterministic factors and ML probability into a single
**0–100 AlphaScore**:

- Factors: `momentum`, `relative_strength`, `trend_quality`, `breakout`, `volume_thrust`
  (each 0–1, surfaced individually in the UI factor breakdown).
- Combined with the best ML big-move probability and mapped to a `signal`
  (`strong_buy` / `buy` / `watch` / `avoid`) and a `horizon` tag (`swing` / `trend` /
  `thematic` / `none`).
- `AlphaSignal` also carries `market_cap` (live `shares × close`) and `institutional_pct`,
  enriched from `TickerMetaStore`.

## Batch, Persistence, API

- **Batch:** `workflow/alpha_batch.py` `run_alpha_batch()` runs nightly at **16:20 ET**
  (`alpha_batch_enabled`), after the OHLCV refresh. Common-stock only, build-net floor
  `alpha_min_market_cap_millions` (default **$250M** — intentionally wide so the page can
  explore down without a rebuild).
- **Store:** `market_data/alpha_store.py` `AlphaSignalStore` → `data/alpha_signals.parquet`.
- **Routes** (`api/routes/alpha.py`):
  - `GET /alpha/scan` — read-time `min_market_cap_millions` floor (defaults to config) +
    common-stock filter + meta enrichment. Also `signal`, `horizon`, `min_score`, `limit`.
  - `GET /alpha/signal/{ticker}` — single-ticker detail.
  - `POST /alpha/recompute` — kicks off the batch in the background.

## Frontend — Directional Alpha page

`frontend/src/pages/stocks/Alpha.tsx` (Stocks → Directional Alpha):

- Columns: Signal, Ticker, Alpha, Horizon, Move Prob, RS vs SPY (6m), Return (6m),
  Off 52w High, Price, Mkt Cap, Inst Own.
- **Sortable:** Alpha, Move Prob, RS vs SPY (6m), Return (6m), Off 52w High, Price, Mkt Cap,
  Inst Own (nulls sort last).
- **Filters:** Signal and Horizon are `multiselect` (empty = All; toggle any combination).
  Alpha and Inst Own have `min` threshold filters.
- **Min Mkt Cap** selector ($250M–$10B presets) persists to `localStorage`
  (`tyche_alpha_min_market_cap_m`, default $1B) and is sent to `GET /alpha/scan`.
- Expandable row: per-factor bars + per-horizon ML probabilities + return/EMA-stack metrics.

## Relationship to the income engine

| | Income engine (CSP / CC) | Directional Alpha |
|---|---|---|
| Goal | Monthly premium | Large capital gain |
| Entry | Near support (pullback / oversold) | Strength / breakout / momentum |
| IV Rank / VRP | Required (rich premium) | Irrelevant |
| Horizon | Days to ~2 weeks | 40–120 trading days |
| Output | CSP candidates, CC signals | AlphaScore + horizon + buy signal |

The two are designed to run side by side: the income engine tells you where to *sell
premium*, the alpha engine tells you where to *buy and hold for the move*.
