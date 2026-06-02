# Directional Alpha Engine (Demand Conviction v2)

A second signal engine focused on **large upside moves** ("10X" / big-move buys) — the
complement to the CSP / Covered Call income engine. Where the income engine harvests
premium near support, the alpha engine looks for quality names positioned to run hard to
the upside (the kind of move AMD, Micron, and several space names made in 2025–2026).

It does **not** replace any existing page or pipeline — it is purely additive.

> **v1 → v2.** The original engine ranked on price **momentum + ML breakout probability**.
> That structurally favored names that had *already run* (momentum is, by definition, a
> backward read). v2 keeps momentum as a **timing** input but leads with **demand evidence** —
> fundamentals, analyst estimates, corporate guidance vs. consensus, policy tailwinds, and
> supply-chain demand cascades — plus an **anti-chase** penalty so already-extended names are
> demoted in favor of earlier-stage demand.

---

## 1. The Demand Conviction thesis

Big upside moves are *led* by a step-change in demand, which shows up in data **before** the
price fully reflects it:

- a hyperscaler raises capex guidance → upstream semis/optical/power suppliers see demand;
- a company **guides above consensus** (beat-and-raise) → estimates get revised up;
- a contract / design win or policy tailwind (CHIPS Act, AI capex, defense appropriations)
  structurally lifts a sector;
- short interest + days-to-cover set up a squeeze.

The engine reads these as six **demand dimensions**, fuses them per *regime*, and uses momentum
only to time the entry — not to pick the name.

### The six demand dimensions

| Dim | Name | What it measures | Source |
|---|---|---|---|
| **D-FUND** | Fundamentals | Revenue growth + acceleration, margin trend, EPS growth, FCF positivity | Finnhub Fundamental-1 (standardized statements) |
| **D-EST** | Estimates | EPS/revenue revisions (90d), recommendation score + trend, surprise history, price-target upside | Finnhub Estimates-1 |
| **D-CAT** | Catalysts | Demand catalysts (contract/design wins, capex guidance) + **guide-vs-consensus** verdicts; recency-weighted | News/8-K classifier + Massive **Benzinga Corporate Guidance** |
| **D-POL** | Policy | Structural multi-quarter tailwinds (AI-capex supercycle, CHIPS Act, defense/space, IRA) | Curated `PolicyEventCalendar` |
| **D-GRAPH** | Supply chain | Upstream-customer demand cascade (hyperscaler capex → suppliers), edge-weighted | Curated `SupplyChainGraph` |
| **D-TECH** | Squeeze | Short-squeeze pressure from days-to-cover / short-interest ratio | Polygon short interest |

---

## 2. Data sources, stores, ingestion

All demand stores are per-ticker Parquet under `backend/data/` (see `data-layout.mdc`):

| Store | Path | Source |
|---|---|---|
| `FundamentalsStore` | `data/fundamentals/{TICKER}.parquet` | Finnhub Fundamental-1 (Polygon fallback) |
| `EstimatesStore` | `data/estimates/{TICKER}.parquet` | Finnhub Estimates-1 |
| `ShortInterestStore` | `data/short_interest/{TICKER}.parquet` | Polygon short interest |
| `CatalystSignalStore` | `data/catalyst_signals/{TICKER}.parquet` | News/8-K classifier + Benzinga guidance |

Ingestion is orchestrated by `workflow/demand_data.py` (`ingest_demand_data`) and run via
`scripts/ingest_demand_data.py`. Each source runs as an **independent, rate-limited parallel
pipeline** (Finnhub 300 rpm; Polygon; Benzinga via Massive). Wired into the nightly schedule
behind config flags in the `# --- Demand data ---` block of `config.py`.

### Fundamentals ingestion (Finnhub standardized + fallbacks)

D-FUND quality depends on using the **right Finnhub endpoints**, not just
`financials-reported?freq=quarterly`:

1. **Primary:** `/stock/financials` (standardized IC/BS/CF) via
   `FinnhubClient.get_standardized_financials()` — merges income statement, balance sheet,
   and cash flow; scales millions → absolute; accepts `preliminary=true`.
2. **Fallback 1:** as-reported `financials-reported?freq=quarterly`.
3. **Fallback 2:** as-reported `financials-reported?freq=annual` (catches Q4 / 10-K-only
   updates that never appear on the quarterly feed — e.g. Jan-FYE names like PL).

**Dual-class shares.** Company-level fundamentals and estimates are identical across share
classes, but Finnhub often publishes under the voting / primary SEC symbol (GOOGL not GOOG).
`market_data/dual_class.py` maps each class to a canonical symbol; `demand_data.py` tries
`finnhub_symbol_candidates()` (canonical first), caches by fetch symbol, and **writes rows
under the universe ticker** (GOOG gets GOOGL data).

**`filing_date` for standardized rows.** When Finnhub omits a filed date, the pipeline
defaults to `period_end` (conservative for point-in-time `merge_asof`). Jan-FYE tickers can
look **STALE** in the audit (>120d since period end) even when the period is current — check
`fund_latest_period`, not just filing age.

**Coverage audit (run after full re-ingest):**

```bash
cd backend && python scripts/audit_demand_coverage.py
# outputs: data/ml/demand_audit_report.csv, demand_audit_summary.json
```

Post–June 2026 re-ingest baseline (~$250M+ universe): fund OK ~2,879 | STALE ~295 |
MISSING ~37; estimates OK ~3,212; ingest gaps 0. Remaining gaps are mostly **source limits**
(SPACs, no analyst coverage), not pipeline bugs.

### Benzinga guidance → demand catalysts (`market_data/benzinga.py`)

Corporate guidance is the strongest forward demand read. `derive_guidance_catalysts()` turns
the full guidance history into signed catalyst impacts, in priority order:

1. **Guide vs. consensus** (preferred): compare the guided figure to the Finnhub analyst
   consensus for the *same fiscal period*. A beat-and-raise is a strong positive; a guide-down
   is negative; a reiteration is neutral (`None`).
2. **Same-period revision** (fallback): company raised/cut its own prior guide
   (`_REVISION_FULL_PCT = 0.10` saturates impact).
3. **Year-over-year** (fallback): guided figure vs. the year-ago actual
   (`_YOY_FULL_PCT = 0.30`).

**Fiscal-calendar alignment.** Vendors disagree on period labels: Benzinga uses the company's
true fiscal labels (e.g. NVDA/WMT FY ends in Jan), while Finnhub keys consensus by calendar
quarter-end. To match them correctly:

- `_infer_fye_month()` (in `demand_data.py`) derives each company's fiscal-year-end month
  (1–12) by a modal vote over `FundamentalsStore` `period_end` dates.
- `fiscal_quarter_end(fiscal_year, fiscal_period, fye_month)` (in `benzinga.py`) computes the
  **true calendar end date** of a Benzinga fiscal quarter.
- `_match_consensus()` aligns that date to the **nearest** Finnhub consensus period within
  `_CONSENSUS_MAX_GAP_DAYS = 46`.
- If no FYE can be inferred, the consensus comparator is **skipped** (falls back to
  revision/YoY) — never a wrong match.

---

## 3. Features

Feature columns are assembled by `ml/features.py` and selected via `get_feature_columns(...)`
(flags: `include_momentum`, `include_demand`, `include_neighbors`, `include_etf`,
`include_correlation`, `include_market_context`). The full demand feature set is **97 columns**
(`demand_feature_columns()`).

**Momentum / RS (timing):** `return_63d/126d/252d`, `ema_200` + slope, `price_to_200ema_pct`,
`ema_stack_score` (8>21>50>200), `pct_off_52w_high`, `pct_above_52w_low`, `breakout_20d/63d`,
`volume_thrust_ratio`, `slope_accel`, `rs_63d/126d/252d` vs SPY, plus an `overextension_score`.

**Demand groups** (augmented onto the feature frame, each degrades to NaN/0 when its store is
absent):

- `FUNDAMENTAL_FEATURE_COLS` — `add_fundamental_features()` (`merge_asof` on filing date).
- `ESTIMATE_FEATURE_COLS` — `add_estimate_features()`.
- `SHORT_INTEREST_FEATURE_COLS` — `add_short_interest_features()`.
- `CATALYST_FEATURE_COLS` — `add_catalyst_features()` (recency-weighted, half-life 30d, 180d
  lookback; blends news/8-K catalysts with the `PolicyEventCalendar` tailwind).
- `GRAPH_FEATURE_COLS` — `add_graph_features()` (same-date customer demand cascade).

### ⚠️ Vectorization (point-in-time augmentation must stay O(rows))

`build_latest_features()` (nightly batch, 1 row/ticker) and `build_dataset()` (full training
panel, ~3.4M rows) share the same augmentation functions. The demand augmenters are
**fully vectorized** — any reintroduction of a per-row/per-date Python loop will make a full
`build_dataset` hang (it did, for 30–60 min, before the May 2026 fix):

- `add_estimate_features` — per-ticker `merge_asof` (was a per-date `_asof_value` scan).
- `add_catalyst_features` — per-ticker numpy `D×E` age-matrix broadcast + a vectorized
  `_policy_score_vec` (was `catalyst_store.aggregate()` **per row** — millions of Parquet
  re-reads). Validated bit-exact against `CatalystSignalStore.aggregate()`.
- `add_graph_features` — supplier-only numpy alignment (was `iterrows()` over the whole panel).

### Ablation note (May 2026)

A MACD-histogram + multi-timeframe trend-alignment group produced only **noise-level AUC lift
(+0.0003–0.0005)** on the big-move targets and was dropped. A `NOTE` comment in
`ml/features.py` records the negative result.

---

## 4. Labels — peak vs. sustained big moves

Two label families per horizon (`ml/labels.py`, `BIG_MOVE_SPECS = [(40,25),(60,40),(120,60)]`),
built from **raw OHLCV only** (no leakage):

| Family | Label | Fires when… | Bias |
|---|---|---|---|
| **Peak** | `big_move_up_{25,40,60}pct_{40,60,120}d` | price touches the target at **any** point in the window (intra-window close max) | rewards flash spikes that retrace |
| **Sustained** | `big_move_sustained_{25,40,60}pct_{40,60,120}d` | price is **still up by the target at the END** of the horizon (forward close) | realistic multi-week buy target |

Magnitude regressions (`peak_recovery_pct_*`, `close_return_pct_*`) are also produced for
calibration.

---

## 5. ML models — `BreakoutPredictor`

`ml/breakout.py` loads the per-horizon XGBoost artifacts from `data/ml/models/` and returns
per-horizon P(big move). It is variant-agnostic — instantiate with the target list you want:

- **Peak** → `ALPHA_TARGETS` (`big_move_up_*`).
- **Sustained** → `ALPHA_SUSTAINED_TARGETS` (`big_move_sustained_*`).

Gracefully degrades to `None` (rules-only) when no artifact exists.

### The demand gate (`scripts/run_demand_gate.py`)

A single non-destructive pass that decides whether to promote the demand-feature models:

1. Build the dataset once (demand features + sustained labels), cache to
   `data/ml/alpha_dataset.parquet`.
2. Walk-forward ablation per horizon: **momentum-only vs. full demand feature set** on the
   *sustained* targets.
3. **Promote** (train + persist) the demand-feature production model only where demand adds
   ≥ `--min-lift` AUC. Writes the `big_move_sustained_*` artifacts — which are **net-new** and
   do **not** overwrite the peak `big_move_up_*` models. Verdict → `data/ml/alpha_results/demand_gate_verdict.json`.

**Verdict (May 2026, 3.4M rows, walk-forward):** demand beat momentum on all three horizons —
precision +5.6 / +6.6 / +6.2 pp (swing / trend / thematic), AUC up to 0.905 on thematic. All
three sustained models promoted.

**Retrain after fundamentals fix (June 2026):** After the standardized + dual-class re-ingest,
re-run the full ML alignment pass:

```bash
cd backend
python scripts/run_demand_gate.py              # sustained models (97 features)
python scripts/train_alpha.py --feature-set momentum   # peak models (62 features)
python -c "from tyche.workflow.alpha_batch import run_alpha_batch; run_alpha_batch(variants=['peak','sustained'])"
```

Restart the backend (or `deps.reset_all()`) so the API reloads new model artifacts.

---

## 6. AlphaScoreEngine — fusion

`strategy/alpha_engine.py` composites everything into a **0–100 AlphaScore**:

1. **Composite = 0.55 × ML blend + 0.45 × factor blend.** ML blend rewards the best horizon
   probability (`0.6·max + 0.4·mean`); factor blend is the weighted technical sub-scores
   (momentum, relative_strength, trend_quality, breakout, volume_thrust). Falls back to
   factors-only when ML is unavailable.
2. **Anti-chase penalty.** `composite *= 1 − (1 − 0.55)·overextension` — a maximally
   parabolic/overbought name keeps only 55% of its raw composite.
3. **Regime router + demand multiplier.** Each name routes to a sub-model:
   - **Revenue** (recent fundamentals + non-null revenue growth): fundamentals + estimates
     drive; catalyst/policy/graph confirm.
   - **Narrative** (sparse fundamentals / pre-revenue): catalysts/policy/graph/squeeze/early-RS
     drive.
   The regime-weighted net demand evidence (−1..1) maps to a multiplier
   `1 + 0.30·net`, clamped to `[0.70, 1.30]` (exactly 1.0 with no demand data → v1-identical).
4. Maps to a `signal` (`strong_buy` / `buy` / `watch` / `avoid`) and a `horizon`
   (`swing` / `trend` / `thematic` / `none`).

`AlphaSignal` carries the full breakdown (`DemandDimensions`, factors, per-horizon probs,
`overextension_*`, `demand_multiplier`, `market_cap`, `institutional_pct`).

---

## 7. Peak vs. Sustained variants + page toggle

Both model variants are scored from the **same feature frame** (scoring is milliseconds;
only the feature build is expensive), and written to **separate snapshots**, so the page can
switch instantly:

- `data/alpha_signals.parquet` — **peak** (legacy filename, unchanged).
- `data/alpha_signals_sustained.parquet` — **sustained**.

Config flag `alpha_sustained_enabled` (default **true**) gates producing the second snapshot.
The Directional Alpha page **defaults to Sustained** (higher precision) with a Peak/Sustained
toggle at the top; it is **compare-only** — the canonical engine, the on-demand
`/alpha/signal/{ticker}`, and downstream consumers are unchanged. The toggle just selects which
snapshot `GET /alpha/scan` reads. If the requested snapshot is missing it falls back to peak and
reports the served `variant`.

> Sustained is far more selective than peak (e.g. ~4 vs ~19 strong buys at a $1B floor) — it
> only flags names the model expects to be **still up at the horizon end**, not merely spiking.

---

## 8. Batch, persistence, API, scheduling

- **Batch:** `workflow/alpha_batch.py` `run_alpha_batch(..., variants=["peak","sustained"])`
  builds features once and scores each variant. Sustained probabilities are remapped onto the
  engine's canonical horizon keys for transparent scoring. Common-stock only, build-net floor
  `alpha_min_market_cap_millions` (default **$250M** — intentionally wide so the page can
  explore down without a rebuild).
- **Scheduling:** runs chained after the nightly flatfile ingest
  (`alpha_batch_after_flatfile`), else the standalone 4:20 PM ET weekday cron. Produces both
  variants when `alpha_sustained_enabled`.
- **Store:** `market_data/alpha_store.py` `AlphaSignalStore(variant=...)`.
- **Routes** (`api/routes/alpha.py`):
  - `GET /alpha/scan?variant=sustained|peak` — read-time `min_market_cap_millions` floor +
    common-stock filter + meta enrichment; also `signal`, `horizon`, `min_score`, `limit`
    (default returns top **`limit`** by score; the page requests 500).
  - `GET /alpha/signal/{ticker}` — single-ticker on-demand detail (bypasses the top-`limit`
    slice; the way to inspect any specific name regardless of rank).
  - `POST /alpha/recompute` — kicks off the batch (both variants when enabled) in the background.

> **Why a specific ticker may not appear on the page:** the scan returns only the top `limit`
> names by Alpha score. A demoted mega-cap (e.g. NVDA, which scores ~29/100 "avoid" and ranks
> ~1,300th — already-run names are intentionally demoted) won't be in the payload. Use
> `GET /alpha/signal/{ticker}` to inspect it directly.

## 9. Frontend — Directional Alpha page

`frontend/src/pages/stocks/Alpha.tsx` (Stocks → Directional Alpha):

- **Peak/Sustained toggle** (top-right, defaults to Sustained, persists to `localStorage`
  `tyche_alpha_model_variant`) + a "Move Target" pill showing the active model + an amber notice
  if it fell back to peak.
- Columns: Signal, Ticker, Alpha, Horizon, Regime, Demand (net), Move Prob, Exp. Move,
  RS vs SPY (6m), Return (6m), Off 52w High, Price, Mkt Cap, Inst Own.
- Signal / Horizon / Regime are `multiselect` filters; Alpha and Inst Own have `min` thresholds.
- **Min Mkt Cap** selector ($250M–$10B, default $1B, persists to
  `tyche_alpha_min_market_cap_m`) → `GET /alpha/scan`.
- Expandable row: **Demand Conviction** breakdown (per-dimension bars, regime, demand ×,
  anti-chase), factor bars, per-horizon ML probabilities, return/EMA-stack metrics.

---

## 10. Relationship to the income engine

| | Income engine (CSP / CC) | Directional Alpha |
|---|---|---|
| Goal | Monthly premium | Large capital gain |
| Entry | Near support (pullback / oversold) | Demand step-change; momentum times it |
| IV Rank / VRP | Required (rich premium) | Irrelevant |
| Horizon | Days to ~2 weeks | 40–120 trading days |
| Output | CSP candidates, CC signals | AlphaScore + horizon + buy signal + demand breakdown |

The two are designed to run side by side: the income engine tells you where to *sell premium*,
the alpha engine tells you where to *buy and hold for the move*.
