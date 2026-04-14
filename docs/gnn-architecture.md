# GNN + News Architecture for Tyche Options

## Context

**Tyche** is an options trading platform built on a "buy strength on dips" thesis. It identifies stocks in confirmed uptrends that pull back to their moving averages, then sells cash-secured puts (CSPs) at those levels. The current system uses EMA crossovers (8/21/50), RSI, IV Rank, and Volatility Risk Premium (VRP) to score and rank candidates. Backtested win rate: **76.8%** for 5% OTM CSPs on $4B+ market cap stocks.

This document proposes adding a **Graph Neural Network (GNN)** and **news intelligence pipeline** to Tyche — not to replace the proven conviction system, but to add a macro intelligence layer that helps with trade decisions the current system cannot make.

An initial proposal for a GNN system was reviewed and refined through multiple rounds of feedback. This is the result: a three-tier integration architecture where GNN outputs start as advisory-only intelligence, with a clear promotion path as the model earns trust through a structured feedback loop. The v1 scope is deliberately narrow — three outputs only — with explicit kill criteria if the model fails to prove its value.

---

## The Problem: What the Current System Misses

On April 7, 2025, XOM appeared as a strong CSP candidate — EMA alignment, healthy prior streak, good IV Rank. Ten $160 puts were sold, expiring April 10. On April 8, news broke about XOM's Qatar pipeline operational hit. By Friday morning XOM was at $152 — deep in the money. Assignment followed.

The current system had no way to know:

- That a material negative news event was imminent
- That the energy sector was under broader pressure
- That CVX, SLB, and OXY (correlated names) were also weakening
- Whether to roll the puts or accept assignment
- Post-assignment: what covered call strategy to run

**The EMA/RSI/VRP system is excellent at identifying technical setups. It is blind to news events, cross-asset contagion, and sector-level dynamics.**

---

## Two Capabilities, Better Together

### Capability 1: News Intelligence (Standalone, No GNN Required)

A news article saying "XOM faces Qatar pipeline operational issues" should flag XOM immediately. This is a direct per-ticker signal that doesn't need graph learning.

```
Articles --> Entity Extraction --> Event Classification --> Per-ticker news signal
                                                               |
                                    Displayed on Intelligence dashboard (Tier 1)
                                    Warning badge on conviction/scanner (Tier 2)
                                    news_factor in CSP scoring formula (Tier 3)
```

**Data source options (cost-ordered):**

- **Polygon News API** — news endpoint included in existing market data plans. Returns articles with ticker tags. Lowest friction.
- **Finnhub** — free tier (60 calls/min), company news endpoint.
- **Alpha Vantage News** — free tier, includes sentiment scores.
- **Benzinga** — paid ($100-300/month), highest quality financial entity tagging.

**Processing pipeline:**

1. **Ingest** — scheduled fetch (every 30 min during market hours, hourly off-hours)
2. **Entity extraction** — LLM-based (Gemini, already integrated in Tyche) extraction of tickers, event type, sentiment direction
3. **Event classification** — categorize as: earnings, regulatory/FDA, executive change, legal, product launch, macro/tariff, analyst rating, M&A, operational issue, secondary offering
4. **Scoring** — `news_impact_score` (-1 to +1), `news_recency_weight` (exponential decay, half-life ~6h intraday, ~2d for event impact)
5. **Persist** — per-ticker Parquet or SQLite table

### Capability 2: GNN Cross-Asset Propagation

The GNN adds what per-ticker news filtering cannot: **contagion**.

- An article about NVDA → article node → GNN message passing → AMD, AVGO, SMCI nodes receive propagated risk signal
- A tariff announcement → theme node "semiconductors" → all constituent stocks get risk-adjusted
- XOM Qatar news → XOM node, but also: CVX, SLB, OXY via energy sector edges; companies with Qatar exposure via supply-chain edges

**The GNN catches risk to stocks that aren't mentioned in the article.** This is the value proposition that per-ticker filtering cannot deliver.

---

## v1 Scope: Three Outputs Only

The GNN could theoretically produce dozens of signals — direction predictions, pullback cascades, IV direction, wheel advice, private entity narratives. But an initial model should not try to do everything. v1 is deliberately narrow:

**v1 outputs (build and validate):**

- **`news_contagion_risk`** (0-1) — graph-propagated news risk. "XOM has direct negative news; CVX has no direct news but 0.85 correlation — elevated risk." This is the GNN's most natural value-add over per-ticker filtering.
- **`sector_trend`** ("bullish" / "neutral" / "bearish") + **`sector_relative_strength`** — where does this stock sit relative to its sector? Is the sector itself strong or weak? This is the macro context the current system lacks entirely.
- **`csp_safety_prob`** (0-1) — probability that a 5% OTM CSP at this pullback level expires worthless. This is the most directly actionable signal for the current trading strategy, and computable from existing options history data.

**v1 does NOT include:**

- Per-stock 5d/10d/20d direction predictions (keep as baseline/internal metric, not a shipped output)
- Pullback cascade alerts (defer unless a simple correlation baseline proves these are useful first)
- IV direction predictions
- Post-assignment wheel advisor
- Private entity nodes (SpaceX, OpenAI, etc.)
- Temporal graph networks
- Real-time intraday inference

These are all valid future work, but v1 must prove that the graph adds signal on the three core outputs before expanding scope. The prediction journal tracks everything internally regardless — if 5d direction accuracy turns out to be strong, it can be promoted in v2.

---

## Core Architecture Decision: Three Tiers of Integration

The current conviction system (8/21/50 EMA, RSI, IV Rank, VRP) has proven backtested win rates. An immature GNN model should not modify those scores until it has earned trust through observed accuracy. Instead, integration follows three tiers:

### Tier 1: Macro Intelligence Dashboard (Day 1 — advisory only)

GNN outputs are **displayed but never touch scoring or gates**. This is paper-trading the model itself.

**What the user sees:**

- **Sector Heat Map:** "Technology: bullish (72% 5d up). Energy: bearish (38% 5d up). Healthcare: neutral."
- **Stock-Level Flags:** "XOM: negative news contagion from Qatar pipeline report. Elevated sector risk."
- **Pullback Cascade Alerts:** "NVDA pulled back to 21-EMA yesterday. Historically, AMD follows within 3 days (68% probability)."
- **News Intelligence Feed:** "3 negative articles about Energy sector in last 24h. 1 positive catalyst for AAPL (product launch)."

**What it does NOT do:**

- Does not change conviction scores
- Does not gate CSP eligibility
- Does not modify allocator weights
- Does not alter scanner rankings

**Where it lives in the UI:**

- New "Intelligence" module (alongside existing Options, Stocks, Research sections)
- Macro dashboard with sector cards, news feed, cascade alerts
- Per-ticker sidebar: when clicking a ticker on the Options Conviction or Scanner page, a panel shows "GNN Intelligence" alongside the proven EMA/IV conviction

### Tier 2: Warning Badges + Soft Signals (After 3-6 months of observed accuracy)

Once the model demonstrates reliable accuracy (measured by the prediction journal — see below), specific high-confidence signals **graduate** to visible warnings on existing pages:

- **News risk badge** — a caution icon next to tickers with recent negative news or graph-propagated risk. Still advisory — the user decides whether to skip the trade.
- **Sector strength indicator** — "Sector: Weak" shown alongside existing conviction columns. Informational, not a gate.
- **"GNN disagrees" flag** — when GNN prediction contradicts conviction level (e.g., conviction = high but GNN says 35% 5d up probability). Draws attention without overriding.

**Still does NOT change conviction scores, gates, or allocator weights.**

### Tier 3: Scoring Integration (After 6-12 months, with backtest validation)

Only after walk-forward backtests demonstrate measurable improvement do specific signals enter the scoring pipeline:

- `gnn_factor` added to CSP scoring formula (e.g., 0.7x for bearish GNN, 1.0 neutral, 1.15x for strong bullish alignment)
- `news_contagion_gate` added to CSP eligibility policy — blocks CSPs when graph-propagated negative news exceeds threshold
- `gnn_confidence` as a component in the conviction score (small weight initially)

**Promotion criteria — trade-quality metrics, not ML vanity metrics:**

Raw classification accuracy (e.g., "55% 5d direction accuracy") can be misleading. What matters is whether the signal improves actual trade outcomes. Promotion requires demonstrating ALL of:

- **Delta in CSP expiry-worthless rate:** Walk-forward backtest shows GNN-augmented conviction improves CSP win rate by >= 2 percentage points vs baseline (e.g., 76.8% → 78.8%+)
- **Reduction in severe adverse assignments:** Fewer cases where assigned stock drops > 10% below strike within 30 days
- **Change in max drawdown of deployed positions:** GNN-flagged caution should correlate with reduced worst-case outcomes
- **Precision on negative-risk flags:** When the model flags `news_contagion_risk > 0.5`, the stock actually underperforms at least 70% of the time (high precision matters more than recall — a few missed flags are acceptable, false alarms that block good trades are costly)
- **Calibration of confidence by regime:** Separate accuracy measurements for risk_on, neutral, and risk_off. If the model is only useful in risk_on markets, that's fine — but it must be explicitly documented so Tier 2/3 promotion is regime-conditional
- **Consistent across at least 2 distinct market conditions:** Must span at least one risk_on period and one correction/risk_off period in the observation window

---

## The XOM Trade: How Each Tier Would Have Helped

**Tier 1 (advisory — available from day 1):**
Open the Intelligence dashboard before placing the XOM trade. See: "Energy sector: neutral-bearish (news volume elevated)" and "XOM: 2 articles about Qatar pipeline operations in last 12h, sentiment: negative." XOM still appears as CSP-eligible on the Scanner (conviction system unchanged), but now there's macro context. Choose a wider strike offset (7% OTM instead of 5%) or skip XOM entirely.

**Tier 2 (badges — after validation):**
On the Scanner page, XOM shows a yellow caution badge: "Recent negative news detected." The conviction score and ranking are unchanged, but the visual flag catches attention.

**Tier 3 (scoring — after extended validation):**
XOM's CSP score is penalized by `news_factor = 0.3` and `gnn_factor = 0.75`. It drops from rank #3 to rank #12 in the scanner results. The allocator assigns fewer contracts or skips it entirely.

---

## What "Macro Level" Means Concretely

The GNN's primary output is a **market intelligence layer**, not a per-ticker scoring modifier:

### Sector-Level Signals

- **Sector momentum:** "Technology: 72% of constituents above 8-EMA. Energy: only 41%."
- **Sector IV regime:** "Healthcare IV Rank averaging 65 (rich premiums). Financials averaging 22 (thin premiums)."
- **Sector rotation:** "Money flowing from Energy to Technology based on relative strength + volume patterns."
- **Sector news pressure:** "3 negative articles in Energy sector last 24h vs 1 week average of 0.5."

### Stock-in-Sector Signals

- **Relative strength within sector:** "AMD is the weakest semiconductor by relative strength. If sector pulls back, AMD likely leads the decline."
- **News contagion exposure:** "XOM flagged directly. CVX has no direct news but 0.85 correlation with XOM — elevated risk."
- **Pullback cascade prediction:** "NVDA pulled back to 8-EMA. Historical pattern: AMD follows 68% of the time within 3 days."
- **Outlier detection:** "AAPL is holding above 21-EMA while the rest of XLK is pulling back. Relative strength or delayed reaction?"

### Cross-Asset Themes

- "AI theme: NVDA, AMD, AVGO, SMCI, MSFT, GOOGL — all elevated IV. Theme-wide concern."
- "Qatar operations theme: XOM (direct), SLB (service provider), QatarEnergy-linked names."
- "Tariff exposure theme: stocks with China revenue > 20% showing correlated weakness."

---

## Graph Schema

### Node Types

- **Stock** — each ticker in the universe ($4B+ market cap, common stock)
- **ETF** — sector and thematic ETFs (SPY, QQQ, SMH, SOXX, XLK, XLF, etc.)
- **Sector** — GICS sectors (Technology, Energy, Healthcare, etc.)
- **Theme** — cross-sector themes (AI, EV, space, semiconductors, etc.)
- **Article** — news articles with extracted entities and event classification
- **Market State** — single super-node representing regime (risk_on / neutral / risk_off)

### Edge Types

- stock → stock: rolling correlation, supply-chain, ecosystem links
- stock → ETF: constituent-of, with weight
- stock → sector: member-of
- stock → theme: exposed-to
- article → stock: mentions (weighted by relevance — primary subject vs passing mention)
- article → theme: about
- market_state → stock: global conditioning edge (regime features broadcast to all stocks)

### Node Features

**Stock nodes:** multi-window returns, EMAs (8/21/50), slopes, RSI(14), volume signals, streak, IV Rank, IV Percentile, ATM IV, VRP, RV 20d, conviction score, trend state, days above EMAs, institutional ownership, market cap

**ETF nodes:** returns, IV, flow proxies, concentration metrics

**Article nodes:** LLM/FinBERT embedding (768d), event type one-hot, recency decay, source quality, entity confidence, news_impact_score

**Market State node:** SPY/QQQ EMA alignment, VIX level, realized vol, regime classification

### Edge Features

- Rolling correlation strength (stock-stock)
- ETF constituent weight (stock-ETF)
- Co-mention frequency (stock-stock, derived from news)
- IV contagion strength (stock-stock, computed from options history)
- Time since last major event (article-stock)

---

## Labels: Strategy-Specific, Not Generic

Generic 5d/10d/20d up/down classification is useful for the macro dashboard but the real value comes from strategy-specific labels:

### Options Labels

- **"Would a 5% OTM CSP at this EMA pullback have expired worthless?"** — the core CSP question
- **"Does this pullback recover above support EMA within DTE?"** — CSP safety
- **"What is the forward max drawdown within DTE?"** — strike offset calibration
- **"Should I roll or accept assignment?"** — post-trade decision support
- **"Is IV likely to expand or contract over the next 14 days?"** — covered call timing

### Stock Labels

- **"Does the stock make a new high within 20 days of this pullback?"** — pullback buying thesis
- **"Does the pullback deepen from 8-EMA to 21-EMA?"** — wait-or-buy decision

### Generic (macro dashboard)

- 5d/10d/20d forward return classification (up/down/flat)
- Sector-level direction

---

## Options Flow as Graph Edge Signals

Tyche has 2 years of full options chain history (puts + calls, all strikes). This is richer data than most GNN-for-stocks projects have access to:

- **IV surface contagion** — when NVDA's IV spikes, how do AMD/AVGO IV respond? Natural graph edge weight.
- **Put/call ratio divergence** — whole sector sees elevated put buying except one name? Signal for the outlier.
- **VRP regime by sector** — some sectors systematically misprice options. Theme/ETF node feature.
- **Unusual options activity co-occurrence** — stocks with correlated unusual flow likely share an information channel.

---

## Post-Assignment: GNN for the Wheel

The GNN should help **after assignment**, not just before the trade.

When assigned on XOM at $160 and the stock is at $152:

- **Covered call strike selection:** GNN's `trend_prob_10d` helps decide: aggressive ($155 strike, higher premium, risk of being called away at a loss) vs conservative ($162 strike, lower premium, better chance of profit if assigned)
- **Hold vs sell decision:** If GNN shows "Energy sector recovering, XOM graph signals improving" → hold + sell calls. If "Energy sector deteriorating further" → consider cutting the loss.
- **Roll timing:** "XOM's IV Rank is 72 (elevated) — good time to sell covered calls. Wait 2-3 days for IV to peak after the news cycle."

This is naturally a Tier 1 (advisory) output displayed on the Stocks Dashboard alongside assigned positions.

---

## GNN Output Contract

### v1 Outputs (shipped to dashboard)

```
GNNSignal_v1 per ticker per day:
  # Core v1 outputs — the three signals that matter
  news_contagion_risk: float      # 0-1, graph-propagated news risk
  sector_trend: str               # "bullish" / "neutral" / "bearish"
  sector_relative_strength: float # this stock vs its sector peers
  csp_safety_prob: float | None   # P(5% OTM CSP expires worthless), pullback tickers only

  # Explainability (always present)
  top_contributing_nodes: list[tuple[str, float]]  # (node_id, contribution_weight)
  key_news_articles: list[str]    # article IDs driving the signal
  confidence: float               # calibrated model confidence
```

### Internal Metrics (logged to prediction journal, not shipped to UI)

These are computed and tracked in the prediction journal for future promotion decisions, but not displayed on the dashboard in v1:

```
GNNSignal_internal per ticker per day:
  trend_prob_5d: float            # used to evaluate model quality
  trend_prob_10d: float
  trend_prob_20d: float
  sector_news_pressure: float
  iv_direction_5d: float | None
  pullback_recovery_prob: float | None
  pullback_cascade_risk: float | None
```

### v2+ Outputs (promoted after validation)

Signals that demonstrate accuracy through the feedback loop graduate to shipped outputs:

- `pullback_cascade_risk` → if simple correlation baseline proves these are useful
- `iv_direction_5d` → if the model shows reliable IV regime prediction
- `trend_prob_5d/10d/20d` → if direction accuracy proves useful for trade decisions
- Post-assignment wheel signals → only after covered call strategy is built out

---

## Data Leakage and Event-Timing Risk

This is the **single biggest implementation hazard** in the project. Because Tyche mixes OHLCV, options, news, and graph propagation, the system can look brilliant in backtests if even a tiny bit of future information leaks through. This section documents the specific leakage vectors and their mitigations.

### Leakage Vector 1: Article Timestamps

**Risk:** A news article published at 3:55 PM on April 8 should not influence the graph snapshot used for the April 8 morning prediction. If articles are indexed by date without time filtering, a "Day N" prediction could use "Day N evening" news.

**Mitigation:** All news features use a strict cutoff: only articles published before the prediction timestamp are included. The prediction record logs the exact cutoff time. Walk-forward backtests enforce the same cutoff using article `published_at` timestamps, not `date`.

### Leakage Vector 2: End-of-Day Features in "Morning" Predictions

**Risk:** OHLCV close prices for Day N are not known until 4:00 PM. If the model uses Day N closes to predict Day N forward returns, it has future information.

**Mitigation:** All features use **Day N-1 close** (the most recent fully settled bar). The `as_of_date` convention already used by `ConvictionFeatureEngine` enforces this. EMAs, RSI, and all derived features are computed from bars up to and including the prior close.

### Leakage Vector 3: Options Snapshot Timing

**Risk:** Tyche's `OptionsChainStore` snapshots are taken at 4:10 PM ET. IV Rank and VRP derived from these snapshots reflect end-of-day values. Using today's IV data to predict today's outcome is leakage.

**Mitigation:** Options-derived features (IV Rank, VRP, ATM IV) use the **prior day's snapshot**. In backtesting, the `DerivedMetricsStore` is queried with `as_of_date = prediction_date - 1 trading day`.

### Leakage Vector 4: Correlation Window Overlap

**Risk:** Rolling correlation (e.g., 60-day stock-to-stock correlation) could include future data if the window is not strictly backward-looking from the prediction date.

**Mitigation:** All edge features use `[prediction_date - window, prediction_date - 1]` windows. The correlation matrix is rebuilt daily as part of graph construction, never using same-day data.

### Leakage Vector 5: Label Construction

**Risk:** Labels like "did this CSP expire worthless?" require forward-looking data. If the label construction accidentally uses any feature from the label period, the model learns to cheat.

**Mitigation:** Labels are computed in a completely separate pipeline from features. The label builder reads only raw OHLCV and options data, never derived features. Walk-forward splits enforce a strict temporal boundary: train on `[t0, t_train]`, predict at `t_train + 1`, evaluate against `[t_train + 1, t_train + 1 + horizon]`.

### Leakage Vector 6: Graph Structure Leakage

**Risk:** If the graph edges themselves encode future information (e.g., "these two stocks were co-mentioned in an article that hasn't been published yet"), the graph structure leaks.

**Mitigation:** The daily graph snapshot is built using only information available as of the prior close. Article nodes are only added after their publication timestamp. Co-mention edges use only articles up to the cutoff.

### Validation Protocol

Every walk-forward backtest run must report:

- The exact timestamp cutoff used for features, news, and options data
- A spot-check of 10 random predictions verifying no future data in features
- A comparison of "cheating" performance (features include same-day data) vs "clean" performance (features use prior-day only). If the gap is large, leakage is present somewhere.

---

## Prediction Journal + Feedback Loop

This is **core infrastructure, not a nice-to-have.** Built alongside the GNN from day 1. Without it, Tier 1 is just a dashboard of unvalidated guesses.

### What Gets Recorded

Every GNN inference run produces predictions that are **immutable once written**. No retroactive editing.

**Prediction Record:**

```
PredictionRecord:
  prediction_id: uuid
  ticker: str
  prediction_date: date         # when the prediction was made
  prediction_time: datetime     # timestamp of inference run
  model_version: str            # "v0.1.0", "v0.2.0" — tracks model evolution

  # Direction classifications
  trend_5d: str                 # "up" / "down" / "flat"
  trend_5d_prob: float          # 0.72 = 72% confidence in "up"
  trend_10d: str
  trend_10d_prob: float
  trend_20d: str
  trend_20d_prob: float

  # Sector context at prediction time
  sector: str
  sector_trend: str
  regime: str                   # risk_on / neutral / risk_off

  # What drove this prediction (explainability snapshot)
  top_signals: list[dict]       # [{node: "NVDA", type: "pullback", weight: 0.35}, ...]
  key_news_ids: list[str]       # article IDs active at prediction time
  news_contagion_risk: float
  confidence: float

  # Conviction system state at the same moment (for comparison)
  conviction_score: float       # what the proven EMA/IV system said
  conviction_level: str         # "high" / "medium" / "low"
  trend_state: str              # "PULLBACK_TO_8EMA", etc.
```

**Storage:** SQLite table `gnn_predictions` indexed on `(ticker, prediction_date, horizon)`.

### What Gets Validated

A scheduled job runs **daily after market close** and evaluates all predictions that have reached their maturity date:

**Outcome Record:**

```
OutcomeRecord:
  prediction_id: uuid           # links back to the prediction
  ticker: str
  prediction_date: date
  horizon: int                  # 5, 10, or 20
  maturity_date: date           # prediction_date + horizon trading days

  # What actually happened
  price_at_prediction: float
  price_at_maturity: float
  actual_return_pct: float
  actual_direction: str         # "up" / "down" / "flat"
  max_drawdown_pct: float       # worst intra-period decline
  max_gain_pct: float           # best intra-period gain

  # Verdict
  predicted_direction: str
  predicted_prob: float
  hit: bool                     # did predicted direction match actual?
  confidence_calibrated: bool   # was the probability well-calibrated?

  # What changed between prediction and outcome
  news_events_during: list[str] # articles published during the horizon window
  regime_changed: bool          # did regime shift during the window?
  sector_moved: str             # "aligned" / "diverged"
  conviction_was_right: bool    # did the proven conviction system agree and was it right?
```

### The "Why Was It Wrong?" Analysis

When `hit = false`, the system records what the model missed. This is the most valuable part.

**Example: GNN predicted XOM "up" in 5 days but it dropped 5%**

```
Failure Analysis:
  prediction_id: abc-123
  ticker: XOM
  predicted: up (68% confidence)
  actual: down (-5.2%)

  Signals at prediction time:
    - XOM trend_state: PULLBACK_TO_8EMA (bullish setup)
    - Energy sector: neutral (52% bullish)
    - No negative news at prediction time
    - Correlation cluster: CVX +0.85, SLB +0.72

  What happened during the 5d window:
    - Day +1: Article "XOM faces Qatar pipeline shutdown" (news_impact: -0.8)
    - Day +2: CVX also dropped 3% (correlation cascade confirmed)
    - Day +3: Energy sector shifted to bearish (38% bullish)
    - Regime: stayed risk_on (macro didn't predict this)

  Root cause classification: NEWS_SURPRISE
  Notes: Model had no pre-existing news signal for Qatar risk.
         News pipeline would have caught this on Day +1.
         Model's sector/correlation signals were correct (CVX followed).
         The model's technical setup was sound — it was an exogenous event.
```

**Root cause categories:**

- `NEWS_SURPRISE` — material news event the model couldn't have known
- `SECTOR_DIVERGENCE` — sector moved differently than predicted
- `REGIME_SHIFT` — macro regime changed during the window
- `IDIOSYNCRATIC` — stock moved against sector (company-specific)
- `OVERFITTING` — model was confidently wrong on a common pattern (systematic issue)
- `LOW_CONFIDENCE_MISS` — model was already uncertain (< 55% prob), expected some misses
- `CORRECT_DIRECTION_WRONG_MAGNITUDE` — direction right but flat classified as up (threshold issue)

### Feedback Dashboard

**Rolling Accuracy Panel:**

```
Model v0.2.0 — Last 90 days (updated daily)

5-day predictions:   58.3% accurate  (baseline: 52.1%)  [+6.2pp vs random]
10-day predictions:  54.7% accurate  (baseline: 51.8%)  [+2.9pp vs random]
20-day predictions:  56.1% accurate  (baseline: 50.5%)  [+5.6pp vs random]

Sector calls:        63.2% accurate  (Energy: 71%, Tech: 58%, Healthcare: 55%)
News flag accuracy:  78.4% (when model flags negative news risk, stock actually drops)

Accuracy by regime:
  risk_on:   61.2%
  neutral:   55.8%
  risk_off:  49.3%  <-- model struggles in volatile markets
```

**Calibration Plot:**
When model says 70% up, does the stock actually go up ~70% of the time? If model says 70% up but stock only goes up 50% of the time, model is overconfident — discount predictions.

**Failure Pattern Analysis:**
- "Model consistently wrong on biotech stocks within 5 days of FDA decision dates"
- "Model overestimates pullback recovery during risk_off regime"
- "Sector calls are strong for Technology and Energy, weak for Healthcare"
- "News surprise is the #1 root cause of misses (42%) — news pipeline would address 60% of these"

**Per-Ticker Prediction Timeline:**

```
XOM — Prediction History

Apr 7:  5d=UP(68%)   → MISS  (actual: -5.2%)  [NEWS_SURPRISE: Qatar pipeline]
Apr 7:  10d=DOWN(61%) → HIT   (actual: -3.1%)
Apr 7:  20d=UP(55%)   → pending (matures Apr 30)
Apr 14: 5d=DOWN(72%)  → HIT   (actual: -1.8%)
Apr 14: 10d=UP(58%)   → pending (matures May 1)
```

### What the Feedback Loop Enables

**Observation period (months 1-3):**
- Accumulate predictions and outcomes without acting on them
- Identify which signals are reliable vs noise
- Discover systematic failure patterns
- Compare GNN accuracy vs the existing conviction system ("was conviction_level=high right more often than GNN trend_5d=up?")

**Pattern discovery (months 2-4):**
- "GNN is 71% accurate when confidence > 65% AND regime is risk_on AND no news flags"
- "GNN adds no value over baseline when IV Rank > 80 (event-driven regime)"
- "GNN sector calls are the strongest signal — 63% accuracy, should be Tier 2 candidate"
- "5d predictions during earnings weeks are worse than random — exclude from Tier 2"

**Targeted improvement (months 3-6):**
- Retrain with more emphasis on failure patterns
- Add features the model was missing (if NEWS_SURPRISE is 42% of misses, the news pipeline will address the biggest failure mode)
- Version the model: v0.2.0 → v0.3.0, track whether accuracy improves on the same failure patterns
- **Never auto-retrain.** Every retraining is manual, motivated by observed failure analysis.

**Promotion decisions (month 3+):**
- "Sector calls are 63% accurate with 78% news flag accuracy — promote sector trend to Tier 2 warning badges"
- "5d individual stock predictions are only 54% — keep in Tier 1, not ready for badges"
- "CSP safety predictions during risk_on + no earnings: 67% accurate — candidate for Tier 2"

### Why Not Auto-Retrain

Traditional ML ops would auto-retrain on a schedule (weekly, monthly). For a trading system, this is dangerous because:

- A model that overfits to the last month's regime will fail when regime changes
- Auto-retraining can silently degrade a model that was working
- You need to understand *why* accuracy changed before adjusting the model

Instead, the feedback loop enables **deliberate, evidence-based improvement:**

1. Observe predictions and outcomes for 90+ days
2. Identify failure patterns with root cause analysis
3. Hypothesize a fix (new feature, different threshold, regime-conditional model)
4. Train a candidate model v(N+1)
5. Run both v(N) and v(N+1) in parallel (both produce predictions, both get validated)
6. Promote v(N+1) only if it improves accuracy on the failure patterns WITHOUT degrading accuracy on the patterns v(N) was already getting right
7. Archive v(N) predictions for historical comparison

This is the **scientific method applied to model development**, not a cron job.

---

## Model Architecture

### Phase 1 Baseline (Pre-GNN)

Before building the GNN, establish baselines that the graph model must beat:

- **XGBoost on per-stock tabular features** — EMAs, RSI, slopes, streaks, IV metrics
- **XGBoost with neighbor-aggregated features** — add average sector momentum, ETF trend, peer IV
- **Simple sequence model** per stock

These baselines tell you whether the graph structure actually adds signal.

### GNN Model

- **Architecture:** Heterogeneous GraphSAGE or Heterogeneous GAT, 2-3 layers
- **Separate output heads** for 5d / 10d / 20d direction + CSP safety + IV direction
- **Regime conditioning:** market-state super-node connected to all stock nodes broadcasts regime features
- **Framework:** PyTorch + PyTorch Geometric (explicit heterogeneous graph support, batching, sampling)
- **Training:** Offline daily graph snapshots, walk-forward train/test splits (no future data leakage)
- **Inference:** Daily batch scoring after market close, CPU-only (no GPU needed for inference)

### Future: Temporal Graph Networks

Once the static daily graph proves value, upgrade to purpose-built temporal architectures:

- **TGN (Temporal Graph Networks)** — memory modules that learn temporal patterns
- **TGAT (Temporal Graph Attention)** — time-aware attention over graph neighbors
- **EvolveGCN** — GNN with evolving weight matrices

These handle event-driven edge dynamics more principally than hand-rolled time-decay functions.

---

## Existing Infrastructure Leveraged

Tyche already has substantial data infrastructure the GNN consumes (not rebuilds):

- **OHLCV store** — per-ticker Parquet files, daily bars from Polygon
- **Options history** — 2 years of full chain data (puts + calls, all strikes) via Massive S3 flat files
- **Derived IV metrics** — IV Rank, IV Percentile, ATM IV, VRP, RV 20d per ticker
- **Feature engineering** — 8/21/50 EMAs, slopes, RSI(14), streaks, volume signals, conviction scores
- **Walk-forward backtest framework** — rolling train/test windows
- **Regime detector** — SPY/QQQ-based risk_on / neutral / risk_off
- **Earnings + economic calendar** — FOMC, CPI, jobs report dates
- **Gemini LLM client** — retry, fallback, structured output parsing (reusable for news entity extraction)
- **Ticker metadata** — market cap, exchange, type, institutional ownership

### Prerequisite: Sector/Industry Data — ✅ COMPLETE

Sector/industry classification is now populated in `ticker_meta.parquet`:

- ✅ Polygon's ticker reference API provides SIC codes and sector classification — implemented via `sic_sectors.py` (SIC-to-GICS mapping) + `get_batch_ticker_details_concurrent()` + `_backfill_sic_data()`. Run `python scripts/ingest_data.py --sector` to backfill. Sector is displayed as a filterable column on all conviction/dashboard pages.
- ✅ ETF constituent lists (SPY, QQQ, DIA, XLK, XLF, XLE, XLV, SMH, SOXX, XLI) — static curated lists in `etf_constituents.py` + yfinance weights via `ETFConstituentStore`. Ingest: `ingest_data.py --etf`. Automated quarterly.
- ✅ ETF weight data from yfinance `funds_data.top_holdings` — merged with static lists during `build_etf_data()`. Quarterly refresh scheduled.

---

## Kill Criteria

The project should be stopped or significantly descoped if any of these conditions are met:

**Kill the GNN, keep news-only:**
- If the GNN does not beat XGBoost + neighbor-aggregated features on `csp_safety_prob` after 3 walk-forward windows spanning at least 2 distinct market regimes → the graph structure is not adding signal. Keep the news pipeline (standalone value), keep the tabular model, but stop investing in graph infrastructure.

**Kill graph complexity, keep it simple:**
- If news-only models (Capability 1, no GNN) explain most of the gain in negative-risk flag precision → keep the graph minimal (sector membership edges only). Don't invest in correlation edges, IV contagion, or co-mention features. The news pipeline alone may be sufficient.

**Block Tier 2/3 promotion for specific regimes:**
- If risk_off regime performance remains poor (worse than baseline) after regime-conditioning the model → do not promote any GNN signal to Tier 2/3 for use during risk_off periods. The model may only be trustworthy during calm markets, and that's acceptable — but it must be explicitly gated.

**Kill the whole project:**
- If after 6 months of observation, no v1 signal (news_contagion_risk, sector_trend, csp_safety_prob) demonstrates measurable improvement on trade-quality metrics (CSP expiry rate, adverse assignment reduction, drawdown improvement) → the model is not useful for this strategy. Archive the research and redirect effort elsewhere.

These criteria prevent the project from becoming an indefinite research exercise. The prediction journal provides the data to make these decisions objectively.

---

## Candidate Retrain Pipeline

No auto-retraining — but a **scheduled candidate generation pipeline** removes the manual overhead of rebuilding the training process each time:

**Weekly (automated):**
- Rebuild the daily graph snapshots with latest data
- Retrain a candidate model v(N+1) on the expanded dataset
- Run walk-forward evaluation on the candidate
- Log candidate metrics to the prediction journal alongside v(N)

**Monthly (manual review):**
- Compare v(N+1) accuracy against v(N) on the same time period
- Check whether v(N+1) improves on known failure patterns without degrading strengths
- Decision: promote v(N+1), discard it, or run both in parallel for another month

**What is NOT automated:**
- Model promotion (switching which version produces shipped signals)
- Tier promotion (moving signals from Tier 1 to Tier 2/3)
- Architecture changes (adding new node types, edge types, or output heads)
- Feature engineering changes (adding or removing node/edge features)

This gives you a fresh candidate every week without the risk of silent degradation from blind auto-promotion.

---

## Cost Estimate

| Item | Estimated Cost | Notes |
|------|---------------|-------|
| GPU training | $50-100/run | ~$200-400/month for weekly retraining |
| News data | $0-300/month | Polygon news may be included; Finnhub free; Benzinga paid |
| Storage | Minimal | Parquet-based, fits on local disk |
| Inference | CPU-only | Daily batch scoring, no GPU needed |
| **Total** | **$200-700/month** | Incremental above existing infrastructure costs |

---

## Build Sequence

### Pre-work (Week 0): Data Foundation — ✅ COMPLETE

- ✅ Ingest sector/industry classification from Polygon into ticker metadata — SIC code mapping via `sic_sectors.py`, backfill via `ingest_data.py --sector`, sector column on all conviction/dashboard pages with filterable dropdown
- ✅ Ingest ETF constituent lists (SPY, QQQ, SMH, SOXX, XLK, XLF, etc.) — static curated lists in `etf_constituents.py` + yfinance weights via `ETFConstituentStore`. 10 ETFs, ~400 unique tickers. Ingest: `ingest_data.py --etf`
- ✅ Build stock-to-stock rolling correlation matrix from existing OHLCV — `CorrelationStore` with 60d rolling pairwise correlations (top-20 peers) + SPY/QQQ betas. Strict backward-looking windows (leakage prevention). Ingest: `ingest_data.py --correlations`
- ✅ Add ML package to the backend codebase — `xgboost>=2.1` + `scikit-learn>=1.5` as optional `[ml]` dependency group
- ✅ ETF + correlation features integrated into ML pipeline — 7 ETF features (`etf_membership_count`, `in_spy`, `in_qqq`, `in_dia`, `spy_weight`, `qqq_weight`, `max_etf_weight`) + 5 correlation features (`spy_beta_60d`, `qqq_beta_60d`, `top_peer_corr_mean/max/min`). Auto-included in `build_dataset()` and `get_feature_columns()`
- ✅ **All pre-work data pipelines fully automated via APScheduler.** ETF refresh: quarterly (Mar/Jun/Sep/Dec 1st, 03:00 ET). Correlation refresh: monthly (28th, 22:00 ET). Conviction batch: daily (16:08 ET, after OHLCV refresh). Bridge Tradier IV: daily (16:45 ET, after options snapshot). Weekly ticker meta refresh (Sundays 02:00 ET). Quarterly sector/institutional refresh (Mar/Jun/Sep/Dec 1st, 03:30 ET). ML retrain now includes ETF + correlation features. See `docs/data-operations.md` for full runbook.

### Phase 1 (Weeks 1-3): News Pipeline + EDGAR Pipeline + Intelligence Dashboard — ✅ COMPLETE

**News pipeline (standalone) — ✅ BUILT:**
- Polygon + Finnhub article ingestion on a schedule (every 30 min, configurable)
- LLM-based entity extraction + event classification via Gemini Flash (`NewsClassifier`)
- Per-ticker `news_impact_score` (48h recency-weighted, 12h decay half-life)
- Persisted to per-ticker Parquet (`data/news_articles/{TICKER}.parquet`) + SQLite signals (`news.db`)
- Displayed on Intelligence dashboard (Tier 1) with per-ticker drill-down
- `NewsRiskBadge` on Options Conviction, Options Scanner, Stocks Conviction, and Stocks Dashboard pages
- Background task execution via `asyncio.create_task` with `asyncio.Lock` concurrency guard

**EDGAR pipeline (standalone, not in original plan — added as supplemental) — ✅ BUILT:**
- SEC EDGAR 8-K filing ingestion + Gemini Flash classification (event type, sentiment, impact)
- Form 4 insider transaction XML parsing with cluster-sell detection (3+ insiders in 7 days)
- Filing signals persisted to `news.db` (`filing_signals` table)
- Raw data in per-ticker Parquet (`data/filings_8k/`, `data/insider_transactions/`)
- Filing risk badge on conviction/scanner pages (red `FileWarning` icon)
- Scheduled every 120 min (configurable)

**Intelligence UI (Tier 1 dashboard) — ✅ BUILT:**
- Four pages: Dashboard, News, SEC Filings, Insider Activity
- Dashboard shows aggregate stats (tickers with risk, negative articles, cluster sells) + manual ingest buttons
- Per-ticker expandable rows with raw articles, 8-K filings, and insider transactions
- Filtering by risk level, insider buy/sell type, and text search

**Tabular baselines — ✅ BUILT:**
- Vectorised feature extraction in `tyche/ml/features.py` — EMAs (8/21/50), slopes, RSI, streaks, volume, trailing returns, volatility, IV metrics, sector encoding, all matching `ConvictionFeatureEngine` formulas
- Sector-aggregated neighbor features in `add_neighbor_features()` — sector avg RSI, EMA slopes, breadth (% above 8/21 EMA), avg IV Rank, VRP, returns
- Strategy-specific labels in `tyche/ml/labels.py` — CSP profitability (5% OTM, 5d/14d DTE), pullback recovery (5d/10d), forward returns, max drawdown, direction classification (up/down/flat)
- Walk-forward XGBoost evaluation in `tyche/ml/xgb_baseline.py` — strict temporal splits, per-window metrics (accuracy, AUC, precision, recall, F1), feature importance tracking, comparison reporting
- Dataset assembly in `tyche/ml/dataset.py` — loads from OHLCVStore, DerivedMetricsStore, TickerMetaStore; filters by market cap; outputs ML-ready Parquet
- CLI script `scripts/train_baselines.py` — build dataset, train all model variants, produce comparison reports
- 43 unit tests covering features, labels, and walk-forward evaluation

**XGBoost CSP safety integration — ✅ BUILT:**
- `CSPSafetyPredictor` in `tyche/ml/inference.py` — singleton that loads a trained XGBoost model and produces P(CSP expires worthless) for each ticker during conviction scans
- Model persistence via `tyche/ml/model_store.py` — XGBoost native JSON + metadata sidecar (feature columns, training stats, timestamp) under `data/ml/models/`
- Production model training via `train_production_model()` in `xgb_baseline.py` — trains on full dataset (walk-forward is for eval only), called by `train_baselines.py --save-model`
- `csp_safety_prob: float | None` field threaded through all 5 persistence layers: `FeatureSignal` → `ConvictionSignal` → Parquet signal store → SQLite `ConvictionSnapshot` → API response schemas
- Frontend "CSP Safety" column on Options Conviction and Stocks Conviction pages — percentage bar with green (≥75%), yellow (50-75%), red (<50%) color coding
- Scanner scoring multiplier: `ml_factor = 0.5 + 0.5 * csp_safety_prob` when model available, 1.0 when absent
- Monthly retrain scheduler (`schedule_ml_retrain`) with `CronTrigger` on 1st of month, 2 AM ET; config knobs: `ml_retrain_enabled`, `ml_retrain_day_of_month`, `ml_retrain_time`
- Manual retrain via `POST /api/v1/system/ml/retrain`; model info via `GET /api/v1/system/ml/model-info`
- Graceful degradation: when no model artifact exists, `csp_safety_prob = None` everywhere; deterministic conviction pipeline unchanged
- 22 new unit tests for model_store, inference, and conviction plumbing

**Baseline results (3.39M rows, 8,192 tickers, 2015-12-18 → 2026-04-13, 39 walk-forward windows):**
- `csp_win_5d`: 88.0% accuracy, 0.915 AUC (single features) — **strong, deployed as Tier 1 signal**
- `csp_win_14d`: 76.2% accuracy, 0.827 AUC
- `pullback_recovery_5d`: 87.3% accuracy, 0.947 AUC
- Neighbor-aggregated features (sector averages) did NOT improve CSP safety: 87.6% accuracy, 0.910 AUC (Δ acc = -0.4pp, Δ AUC = -0.004). Sector averages are too coarse to add signal beyond what per-stock features already capture.
- Direction models (5d/10d) are weak (~40-43% accuracy) — not actionable

**Top 15 feature importance (single model, averaged across 39 windows):**

| Rank | Feature | Importance |
|------|---------|-----------|
| 1 | `price_to_21ema_pct` | 0.5127 |
| 2 | `rsi_14` | 0.0941 |
| 3 | `trend_state_ord` | 0.0458 |
| 4 | `price_to_50ema_pct` | 0.0334 |
| 5 | `days_above_both_emas` | 0.0270 |
| 6 | `price_to_8ema_pct` | 0.0249 |
| 7 | `volatility_20d` | 0.0230 |
| 8 | `return_10d` | 0.0149 |
| 9 | `return_1d` | 0.0134 |
| 10 | `ema_21_slope` | 0.0129 |
| 11 | `return_20d` | 0.0106 |
| 12 | `spy_beta_60d` | 0.0095 |
| 13 | `volume_ratio` | 0.0093 |
| 14 | `qqq_beta_60d` | 0.0092 |
| 15 | `sector_encoded` | 0.0089 |

**Relational feature analysis (ETF + correlation, 12 features):**
- 8.1% share of total feature importance in the single model (6.2% in the neighbor model where sector features compete)
- SPY/QQQ betas are the most valuable relational features (ranks 12, 14) — high-beta stocks have greater CSP assignment risk
- ETF membership count (0.0078) and binary membership flags (`in_qqq` 0.0069, `in_spy` 0.0065) provide signal — index constituents show more institutional mean-reversion behavior
- Peer correlation features (mean/max/min of top-20 correlated peers) add moderate signal (0.0062–0.0075)
- ETF weight features (`spy_weight`, `qqq_weight`, `max_etf_weight`) are weakest (0.0039–0.0061) — weight granularity doesn't matter as much as binary membership
- **Bug fix (April 2026):** SPY/QQQ were initially excluded from beta computation because `filter_equity_only()` removed ETFs. Fixed by auto-injecting benchmark tickers into `compute_rolling_correlations()`.

**Key insight:** `price_to_21ema_pct` alone carries 51% of model importance — how far the stock is from its 21-EMA dominates all other signals for predicting 5-day CSP safety. This aligns with the strategy philosophy: the 21-EMA pullback is the primary edge. Relational features (8.1%) contribute real signal but cannot overcome the inherent ceiling of static cross-sectional features applied across 10 years of history. Time-varying graph edges (Phase 2 GNN) are the natural next step to unlock temporal relationship dynamics.

**Phase 1 status: COMPLETE.** All deliverables built and deployed: news/EDGAR intelligence pipelines, tabular XGBoost baselines, CSP safety Tier 1 scoring signal with monthly recalibration, ETF + correlation relational features, and fully automated data operations (see `docs/data-operations.md`). The non-GNN relational baseline has reached its ceiling at ~88% accuracy, establishing the bar that Phase 2 GNN must beat.

### Phase 2 (Weeks 4-6): Static Heterogeneous Graph + Prediction Journal

**GNN model (v1 scope — three outputs only):**
- Graph schema: stock, ETF, sector, article, market-state nodes (NO theme or private-entity nodes in v1)
- Node features from existing data stores + news embeddings
- Edge features: correlation, ETF weight, IV contagion, co-mention frequency
- Hetero GraphSAGE, 2-3 layers
- Output heads: `news_contagion_risk`, `sector_trend` / `sector_relative_strength`, `csp_safety_prob`
- Internal tracking heads: `trend_prob_5d/10d/20d` (logged, not shipped)
- Regime conditioning via market-state super-node
- Walk-forward backtest with **full leakage audit** (see Data Leakage section)

**Prediction journal (built alongside the model, not after):**
- Prediction and outcome tables in SQLite
- Daily prediction logging from the first day of inference
- Daily outcome validation job (evaluates matured predictions after market close)
- Root cause classification for misses
- GNN v1 outputs + prediction history displayed on Intelligence dashboard (Tier 1 only)
- Internal metrics logged but not displayed

### Phase 3 (Weeks 7-8): Feedback Dashboard + Accuracy Tracking

**Feedback UI:**
- Rolling trade-quality metrics: CSP expiry-worthless rate delta, adverse assignment reduction, drawdown change
- Negative-risk flag precision (when model flags risk, was it right?)
- Calibration plot by regime (predicted probability vs actual frequency, split by risk_on / neutral / risk_off)
- Failure pattern analysis (root cause breakdown)
- Per-ticker prediction timeline
- Model version comparison (v(N) vs v(N+1) candidate side-by-side)

**Kill criteria evaluation:**
- Does GNN beat XGBoost + neighbor features on `csp_safety_prob`?
- Does news-only explain most of the gain? If so, simplify the graph.
- **No scoring changes yet — this phase is about understanding the model, not deploying it**

### Phase 4 (Months 3-6): Observation Period + Tier 2 Promotion

This is NOT a build phase — it's a **validation period**:
- Continue daily predictions and outcome tracking
- Accumulate 90+ days of predictions across at least 2 distinct market conditions
- Run monthly trade-quality reviews (CSP expiry rate delta, adverse assignments, drawdown)
- Evaluate kill criteria: does GNN beat baselines? Does news-only explain the gain?
- When specific v1 signals meet trade-quality promotion criteria, promote to Tier 2 — but **regime-conditional** (e.g., sector_trend promoted for risk_on only if risk_off accuracy is below threshold)
- Continue monitoring accuracy post-promotion to catch degradation
- Weekly candidate retrain pipeline running automatically; monthly manual review of candidates

### Phase 5 (Months 6-12+): Tier 3 Scoring + Temporal Graphs

- Promote battle-tested signals into conviction scoring (Tier 3) only with backtest validation
- Temporal Graph Networks for event-driven edge dynamics
- Model v2: retrained with new features addressing observed failure patterns
- Private entity nodes (SpaceX, OpenAI, etc.) as narrative nodes
- Real-time news ingestion during market hours
- Post-assignment wheel advisor (covered call strike suggestions)

---

## Tier Promotion Timeline

```
Week 0-8:   BUILD    — Data foundation, news pipeline, GNN, prediction journal, feedback dashboard
Month 1-3:  OBSERVE  — Daily predictions logged, outcomes validated, accuracy measured
Month 3-6:  TIER 2   — Promote strongest signals to warning badges (sector calls, news flags)
Month 6-12: TIER 3   — Promote validated signals into scoring (requires backtest proof)
Month 12+:  EVOLVE   — Retrain on failure patterns, temporal graphs, real-time news
```

---

## Summary of Key Decisions

1. **v1 does three things, not ten.** Outputs limited to `news_contagion_risk`, `sector_trend` + `sector_relative_strength`, and `csp_safety_prob`. Everything else is internal tracking or deferred to v2+. The model must prove value on narrow scope before expanding.

2. **GNN starts as macro intelligence, not scoring.** Tier 1 (advisory dashboard) from day 1. Tier 2 (warning badges) after 3+ months of observed accuracy. Tier 3 (scoring integration) after 6+ months with backtest proof. The proven conviction system is untouched until the GNN earns trust.

3. **Trade-quality metrics, not ML vanity metrics.** Promotion is judged by delta in CSP expiry-worthless rate, reduction in adverse assignments, max drawdown improvement, and negative-risk flag precision — not by generic classification accuracy.

4. **The feedback loop is core infrastructure, not a nice-to-have.** Every prediction is logged with explainability context. Every matured prediction is validated against actuals. Every miss is root-cause classified. This is how the model earns trust — or reveals that it shouldn't be trusted.

5. **Explicit kill criteria.** If GNN doesn't beat XGBoost + neighbor aggregates on CSP safety after 3 walk-forward windows, stop the graph work. If news-only explains most of the gain, keep the graph simple. If risk_off performance stays poor, don't promote to Tier 2/3 for those regimes.

6. **Data leakage is the #1 implementation risk.** Article timestamps, end-of-day features, correlation windows, and options snapshot timing each create leakage vectors. Every walk-forward run must include a leakage audit comparing "cheating" vs "clean" performance.

7. **No auto-retraining, but automated candidate generation.** Weekly candidate retrain pipeline produces a fresh v(N+1) model. Monthly manual review compares against v(N). Promotion is always a deliberate human decision.

8. **3 months minimum observation** spanning at least 2 distinct market conditions before any signal is promoted to Tier 2. 6+ months before Tier 3. Promotion may be regime-conditional (e.g., sector_trend promoted for risk_on markets only).

9. **News pipeline is standalone AND feeds the GNN.** Direct per-ticker news filtering delivers immediate value (the XOM scenario). The GNN adds cross-asset contagion (risk to stocks not mentioned in the article). Build the news pipeline first.

10. **Don't rebuild what exists.** The GNN reads from existing OHLCV, options history, IV metrics, and feature engineering. It adds a layer on top.

11. **The feedback loop answers "why was it wrong?" — not just "was it wrong?"** Root cause categories (NEWS_SURPRISE, SECTOR_DIVERGENCE, REGIME_SHIFT, etc.) drive targeted model improvement rather than blind retraining.
