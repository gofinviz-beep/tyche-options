# Multi-Bagger Discovery Engine v8 — Complete Cursor Composer Implementation Spec

Audience: Cursor Composer 2.5 Fast inside `tyche-options-main`.

This V8 file is the standalone implementation spec. It supersedes V6 and V7. It preserves V7's P0.3/P1 estimate-snapshot changes and restores the complete P2-P8 plan from V6.

Core principle:

> Do not penalize extension. Penalize price that has outrun validated demand, measured within the right peer, size, coverage, and evidence tier.

---

## 0. Cursor agent contract

1. Execute phases in order unless the user explicitly redirects.
2. One task equals one commit-sized diff.
3. Conservative Directional Alpha behavior must remain unchanged unless discovery flags are enabled.
4. Behavior changes must be behind config flags or isolated in discovery paths.
5. Do not refactor unrelated files.
6. If an anchor file/symbol does not match, stop and report.
7. Preserve train/serve feature parity.
8. Do not add training features that cannot be produced at inference.
9. Unverified LLM/news/13F claims may create `Validate Now`; they may not raise score.
10. Add or update tests for every behavior-changing task.

Stop if a task would alter conservative Alpha with discovery flags off, add all-null features to training, overwrite existing models, or compare different fiscal periods as same-period revisions.

---

## 1. Current accepted baseline

### P0 complete

- P0.1 funnel audit complete.
- P0.2 missed-winner audit complete.
- P0.3 Finnhub EPS/revenue estimate as-of probe complete.

P0.3 finding:

- Finnhub `/stock/eps-estimate` and `/stock/revenue-estimate` return one current consensus row per fiscal period.
- They do not provide historical point-in-time consensus snapshots.
- Params such as `from`, `to`, `asOf`, `asOfDate`, and `date` did not change response shape.
- `snapshot_date` is local ingest metadata, not vendor historical as-of.
- Estimate revision velocity requires local snapshot accumulation going forward.

### P1 complete and accepted with caveats

Implemented:

- P1.1 discovery config flags.
- P1.2 per-target `scale_pos_weight`.
- P1.3 `__isna` missingness indicators and train/serve parity.
- P1.4 percentile discovery signals and `build_alpha_score_engine()`.
- P1.5-A/B `EstimateSnapshotStore`, daily ingest wiring, snapshot cadence audit.
- P1.7 guidance `tanh(log1p)` tail-preserving transform.
- P1.8 gated demand-adjusted extension.
- P1.9 purged + embargoed walk-forward splits.

Deferred by design:

- old estimate-ramp unit fix;
- same-period estimate revision features in ML;
- active-forward revision features;
- ramp calibration.

Validation interpretation:

- ARM metadata/options backfill fixed (2026-06-07): `EQUITY_TYPE_OVERRIDES` forces `CS` in `write_meta()` / `refresh_ticker_meta()`; options via `ingest_options_flatfiles.py --tickers ARM --force`.
- AVGO may be treated as a valid weak/caution case if fresh weak outlook supports that.
- Estimate snapshot infrastructure is accepted even though 90d revisions are still null.

---

## 2. Phase order

```text
P0 Diagnostics                                  DONE
P1 Mechanical unblock, gated                    DONE
P2 Discovery labels and model namespace         NEXT
P3 Discovery feature families
P4 Evidence ledger and EDGAR/Form 4 bridge
P5 D-SMART and D-RISK
P6 Dynamic theme graph and event-driven rescore
P7 Discovery engine, API, and UI
P8 P&L backtest and acceptance gate
```

Before P2, record:

```text
P0/P1 accepted. ARM backfill + `EQUITY_TYPE_OVERRIDES` complete. AVGO accepted as possible weak-demand caution case. Revised P1.5 remains estimate snapshot infrastructure; no ramp fix yet. P1 completion note: `docs/alpha/p1_completion_note.md`.
```

---

# Phase P2 — Discovery labels and model namespace

## P2.1 Add multi-bagger and path-aware labels

File: `backend/src/tyche/ml/labels.py`

Add without changing current `BIG_MOVE_SPECS`:

```python
MULTIBAGGER_SPECS = [
    (252, 100.0),
    (504, 200.0),
    (756, 400.0),
]
```

Emit:

```text
big_move_sustained_100pct_252d
big_move_sustained_200pct_504d
big_move_sustained_400pct_756d
max_forward_return_252d
max_forward_return_504d
max_forward_return_756d
hit_target_before_stop_100pct_252d_35dd
hit_target_before_stop_200pct_504d_35dd
time_to_100pct_252d
max_drawdown_before_hit_100pct_252d
```

Rules:

- Do not add these to conservative target lists.
- Use split-adjusted price series.
- Avoid lookahead leakage.
- Path-aware labels must distinguish investable paths from one-day spikes.

Acceptance:

- Synthetic tests for hit before stop, stop before hit, no hit, late hit, and max drawdown before hit.

Commit: `labels: add discovery multi-bagger and path-aware labels`

## P2.2 Add discovery model artifact namespace

Files likely touched:

```text
backend/src/tyche/ml/xgb_baseline.py
backend/src/tyche/ml/model_store.py
backend/src/tyche/ml/breakout.py
backend/scripts/train_alpha.py
backend/scripts/run_demand_gate.py
```

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
backend/data/ml/models/discovery/{target}.json
backend/data/ml/models/discovery/{target}_meta.json
```

Rules:

- Do not overwrite conservative model artifacts.
- Metadata must include target, feature columns, label horizon, positive rate, training window, class weighting, purged-WF flag, embargo days, and trained_at.

Acceptance:

- Discovery artifacts save/load under `models/discovery/`.
- Conservative model loading still works.
- Namespace isolation test passes.

Commit: `ml: add discovery model namespace`

## P2.3 Add payoff-weighted discovery blend

File: `backend/src/tyche/strategy/discovery_scoring.py` or `strategy/discovery_engine.py`

Initial payoff:

```text
payoff = 0.25*P(+25%) + 0.60*P(+60%) + 1.00*P(+100%) + 2.00*P(+200%) + 4.00*P(+400%)
```

Rules:

- It is a score component, not the entire decision.
- Missing discovery models degrade gracefully.
- Conservative Alpha score remains visible as reference.

Commit: `discovery: add payoff-weighted model blend`

---

# Phase P3 — Discovery feature families

Add:

```python
def discovery_feature_columns() -> list[str]:
    ...
```

Do not mutate conservative `demand_feature_columns()`.

## P3.1 D-EST snapshot-derived features

Files:

```text
backend/src/tyche/ml/features.py
backend/src/tyche/market_data/estimate_snapshot_store.py
backend/scripts/audit_estimate_snapshots.py
```

Available now:

- current EPS/revenue estimates by fiscal period;
- analyst count;
- recommendation score/trend;
- price target upside;
- EPS surprise history;
- forward growth if derivable.

Forward-maturing features, only when local snapshots exist:

```text
e_eps_revision_7d_same_period
e_eps_revision_14d_same_period
e_eps_revision_30d_same_period
e_eps_revision_90d_same_period
e_rev_revision_7d_same_period
e_rev_revision_14d_same_period
e_rev_revision_30d_same_period
e_rev_revision_90d_same_period
e_eps_revision_abs_7d_same_period
e_eps_revision_abs_14d_same_period
e_eps_revision_abs_30d_same_period
e_eps_revision_abs_90d_same_period
e_eps_revision_num_changes_30d
e_rev_revision_num_changes_30d
e_eps_revision_days_since_change
e_rev_revision_days_since_change
e_eps_analyst_count_delta_30d
e_rev_analyst_count_delta_30d
e_eps_dispersion_change_30d
e_rev_dispersion_change_30d
```

Rules:

- Compare same ticker + same metric + same fiscal period.
- Never compare today's front quarter with the prior front quarter and call it same-period.
- EPS percent revisions can explode around zero; use absolute deltas when prior EPS is near zero/negative.
- Keep revision velocity out of training until non-null coverage is meaningful.

Commit: `features: add D-EST snapshot-based discovery features`

## P3.2 D-CAT split and tail-preserving catalyst features

Files:

```text
backend/src/tyche/market_data/benzinga.py
backend/src/tyche/market_data/catalyst_store.py
backend/src/tyche/ml/features.py
```

Add:

```text
cat_guide_vs_consensus_pct
cat_guide_vs_consensus_raw
cat_yoy_implied_growth_pct
cat_high_magnitude_event_score
cat_positive_event_count_30d
cat_positive_event_count_90d
cat_negative_event_count_30d
cat_negative_event_count_90d
cat_event_magnitude_max_180d
cat_event_source_quality_max_180d
```

Keep legacy catalyst fields.

Commit: `features: split catalyst magnitude and counts for discovery`

## P3.3 D-FLOW v1 from options history

Files:

```text
backend/src/tyche/market_data/options_history_store.py
backend/src/tyche/ml/features.py
backend/src/tyche/ml/dataset.py
```

Features:

```text
flow_call_volume_z_20d
flow_put_volume_z_20d
flow_call_put_volume_ratio
flow_call_dollar_volume_z_20d
flow_options_vs_stock_volume
flow_near_expiry_call_ratio
flow_repeat_call_activity_5d
flow_call_transactions_z_20d
```

Rules:

- Use option `close * volume * 100` as rough premium proxy.
- Aggregate by underlying/date first.
- Merge backward as-of.
- No-options names are NaN/neutral, not bad.

Commit: `features: add D-FLOW volume features from options history`

## P3.4 D-FLOW v2 from chain snapshots

Files:

```text
backend/src/tyche/workflow/options_snapshot.py
backend/src/tyche/market_data/data_store.py
backend/src/tyche/ml/features.py
```

Prerequisite: discovery options snapshots must include calls and puts.

Features:

```text
flow_oi_change_persistence_5d
flow_call_put_premium_ratio
flow_25_delta_call_skew
flow_term_structure_shift
flow_delta_bucket_call_demand
flow_iv_up_call_volume_up
```

Commit: `features: add D-FLOW chain snapshot features`

## P3.5 Peer-tier normalization

File: `backend/src/tyche/ml/peer_tiers.py`

Tiers:

- market-cap bucket;
- sector/industry;
- revenue/narrative regime;
- coverage tier;
- options liquidity tier.

Features:

```text
tier_id
price_mom_tier_z
demand_mom_tier_z
flow_tier_z
estimate_revision_tier_z
cat_magnitude_tier_z
```

Commit: `ml: add peer-tier normalization for discovery`

---

# Phase P4 — Evidence ledger and EDGAR/Form 4 bridge

## P4.1 EvidenceEventStore

File: `backend/src/tyche/market_data/evidence_store.py`

Schema:

```python
{
    "evidence_id": str,
    "tickers": list[str],
    "theme_ids": list[str],
    "event_type": str,
    "source": str,
    "source_quality": str,
    "event_date": date,
    "ingest_date": datetime,
    "effective_date": date | None,
    "claim_text": str,
    "numeric_facts": dict,
    "validation_status": str,
    "decay_half_life_days": float,
    "confidence": float,
    "linked_feature_names": list[str],
    "ref_id": str,
}
```

Store: `backend/data/evidence_events/{TICKER}.parquet`

Commit: `evidence: add EvidenceEventStore`

## P4.2 LLM/news claim extraction writes unverified evidence

Files:

```text
backend/src/tyche/analysis/news_classifier.py
backend/src/tyche/analysis/thesis_extractor.py
```

Extract theme, roles, claims, numeric facts, dates, validation tasks, and source quality.

Rule: LLM output may create `Validate Now`; it may not raise score.

Commit: `evidence: write unverified thesis events from news extraction`

## P4.3 Deterministic validation workflow

File: `backend/src/tyche/workflow/evidence_validation.py`

Validate against Finnhub, Benzinga/Massive, EDGAR, Form 4, options stores, and OHLCV reaction.

Statuses: `verified`, `contradicted`, `stale`, `unverified`.

Commit: `evidence: add deterministic validation workflow`

## P4.4 EDGAR 8-K bridge

Files:

```text
backend/src/tyche/workflow/edgar_pipeline.py
backend/src/tyche/market_data/filing_store.py
backend/src/tyche/market_data/evidence_store.py
backend/src/tyche/ml/features.py
```

Features:

```text
cat_validated_primary_score_90d
f_backlog_yoy
f_rpo_yoy
f_book_to_bill
risk_shelf_registration
risk_atm_program
risk_secondary_offering
risk_convertible_issuance
```

Commit: `evidence: bridge EDGAR events into discovery features`

## P4.5 Form 4 cluster buy features

Files:

```text
backend/src/tyche/market_data/filing_signals.py
backend/src/tyche/ml/features.py
```

Features:

```text
insider_cluster_buy_30d
insider_net_buy_value_30d
insider_buy_count_30d
insider_sell_pressure_90d
insider_role_weighted_buy_value_30d
```

Rules: use transaction code `P` and acquisition `A`; ignore grants/awards as buy evidence.

Commit: `features: add Form 4 insider cluster buy features`

---

# Phase P5 — D-SMART and D-RISK

## P5.1 D-SMART ownership flow

Features:

```text
sm_initiations_90d
sm_adds_90d
sm_exits_90d
sm_net_flow_z
sm_marquee_exit
sm_staleness_days
sm_price_move_since_effective_date
sm_crowding_score
```

Rules:

- 13F is structural context, not same-day buy signal.
- Famous-manager ownership does not directly create buy score.

Commit: `features: add D-SMART structural ownership flow`

## P5.2 D-RISK hard disqualifiers

Features/routing:

```text
risk_shelf_registration -> De-risk or Disqualified
risk_atm_program -> De-risk
risk_secondary_offering -> De-risk
risk_convertible_issuance -> De-risk
risk_share_count_acceleration -> risk penalty
risk_liquidity_trap -> Disqualified if position size exceeds liquidity capacity
risk_customer_concentration -> reduced size / wait
risk_hype_no_estimate_confirmation -> Validate/Wait
risk_policy_reversal -> De-risk
```

Commit: `risk: add discovery disqualifier features and routing`

---

# Phase P6 — Dynamic theme graph and event-driven rescore

## P6.1 Dynamic theme/cohort store

Files:

```text
backend/src/tyche/market_data/theme_cohort_store.py
backend/src/tyche/market_data/supply_chain_graph.py
backend/src/tyche/ml/features.py
```

Edge components:

```text
revenue_corr + news_co_mention + shared_customer + product_similarity + estimate_revision_corr + flow_corr
```

Features:

```text
graph_theme_signal
graph_theme_breadth
graph_unreported_peer_lift
graph_customer_capex_lag
graph_crowding_score
theme_momentum_30d
theme_evidence_velocity_30d
```

Commit: `features: add dynamic theme cohort graph`

## P6.2 Event-driven rescore

Files:

```text
backend/src/tyche/workflow/alpha_batch.py
backend/src/tyche/workflow/discovery_rescore.py
```

Triggers:

- verified guidance;
- EDGAR 8-K;
- Form 4 cluster event;
- options flow spike;
- theme peer event;
- major estimate revision;
- evidence validation state change.

Commit: `discovery: add event-driven affected-ticker rescore`

---

# Phase P7 — Discovery engine, API, and UI

## P7.1 DiscoverySignal dataclass and store

Files:

```text
backend/src/tyche/strategy/discovery_engine.py
backend/src/tyche/market_data/discovery_signal_store.py
```

Dataclass fields include ticker, discovery_score, conservative_alpha_score, percentile, state, entry_mode, risk_mode, thesis, themes, evidence_momentum, demand/price tier momentum, DAE, validation summary, risk flags, top evidence ids, model probs, expected upside, and drawdown risk.

State machine:

```text
Ignore
Monitor
Validate Now
Wave Watchlist
Buy Candidate
Pursue Despite Extension
Wait for Entry
Second-Chance Entry
De-risk
Disqualified
```

Commit: `discovery: add signal dataclass and parquet store`

## P7.2 Discovery API

Additive endpoints:

```text
GET /alpha/scan?mode=conservative|discovery
GET /alpha/discovery/signal/{ticker}
GET /alpha/evidence/{ticker}
GET /alpha/theme/{id}
GET /alpha/diagnostics/funnel
GET /alpha/diagnostics/missed-winners
POST /alpha/discovery/recompute
```

Default conservative `/alpha/scan` behavior must remain unchanged.

Commit: `api: add discovery alpha endpoints`

## P7.3 Discovery frontend cockpit

Add to Alpha page or a new stocks page:

- Conservative / Discovery toggle.
- Discovery funnel.
- State chips.
- Evidence timeline.
- Demand-adjusted extension readout.
- Theme/cohort panel.
- Risk flags.
- Diagnostics links.

Commit: `frontend: add discovery cockpit for alpha signals`

---

# Phase P8 — P&L backtest and acceptance gate

## P8.1 Discovery portfolio backtest

Files:

```text
backend/src/tyche/backtest/discovery_portfolio.py
backend/scripts/backtest_discovery.py
```

Backtests:

- top 25, 50, 100 weekly baskets;
- 40/60/120/252 day holds;
- equal-weight and volatility-weight;
- state-routed entry;
- slippage and liquidity;
- benchmarks: SPY, QQQ, sector ETF, momentum baseline, conservative alpha.

Report:

```text
CAGR
max drawdown
Sharpe / Sortino
hit rate
average winner / loser
skew
turnover
slippage
future +100% and +200% capture rate
missed-winner reasons
```

Acceptance gate:

- Do not promote discovery by AUC alone.
- Require top-k portfolio improvement and survivable drawdown.

Commit: `backtest: add discovery top-k portfolio acceptance gate`

---

# Regression tests

Required:

1. Conservative invariance with discovery flags off.
2. Demand-adjusted extension treats positive/negative demand differently.
3. Estimate snapshot store preserves prior snapshot dates.
4. Estimate revision velocity stays out of training until coverage is meaningful.
5. Rare class weighting for binary labels.
6. Missingness parity at training and inference.
7. Percentile signals classify top 1/5/15%.
8. Purged split respects embargo.
9. Multi-bagger labels are path-aware and leakage-safe.
10. Discovery namespace does not overwrite conservative artifacts.
11. Unverified evidence creates `Validate Now` but does not raise score.
12. Form 4 grants are not insider buys.
13. Dilution risk routes to De-risk/Disqualified.
14. Discovery API leaves default conservative scan unchanged.
15. Backtest emits top-k returns and drawdown.

---

# Cursor packets

## Packet C — P2

```text
Implement Phase P2 only from multibagger_discovery_engine_v8_cursor_composer_spec.md.
Add multi-bagger/path-aware labels, discovery model artifact namespace, and payoff-weighted discovery blend.
Do not overwrite conservative model artifacts.
Add tests for label correctness and namespace isolation.
```

## Packet D — P3

```text
Implement Phase P3 only.
Add discovery_feature_columns(), D-EST snapshot-derived features, D-CAT split, D-FLOW v1, D-FLOW v2 only if call+put snapshots exist, and peer-tier normalization.
Do not mutate demand_feature_columns().
Do not add estimate revision velocity to training until non-null historical coverage is meaningful.
```

## Packet E — P4

```text
Implement Phase P4 only.
Add EvidenceEventStore, unverified evidence capture, deterministic validation, EDGAR bridge, and Form 4 cluster buy features.
Unverified evidence may change state but must not raise score.
```

## Packet F — P5/P6

```text
Implement P5 and P6 after P4.
Add D-SMART, D-RISK, dynamic theme graph, and event-driven rescore.
Keep conservative Alpha unchanged.
```

## Packet G — P7/P8

```text
Implement P7 and P8 only after P2-P6 are merged.
Add DiscoverySignal store, discovery API, frontend cockpit, and portfolio backtest.
Existing conservative /alpha/scan behavior must remain unchanged.
```

---

# Final stance

Directional Alpha remains the conservative precision engine.

Discovery becomes the parallel recall-first engine that surfaces asymmetric candidates, validates evidence, routes entry/risk state, and proves itself with top-k portfolio backtests.

Target funnel:

```text
Discovery: 50-100 names
Triage: 15-25 names
Action: 3-10 names
```
