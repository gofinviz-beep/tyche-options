# Intent Risk Pipeline

**Source:** `backend/src/tyche/workflow/intent_builder.py`

## Overview

The intent risk pipeline is the deterministic safety layer between probabilistic LLM recommendations and the trade intents shown to the user. It ensures every number displayed on the Intents page is broker-verified, every recommendation passes a risk gate, and conviction levels honestly reflect the weakest signal.

## Design Principle

> **LLM = qualitative advisor. Broker = numerical source of truth.**

The LLM (Gemini) produces thesis, risks, assignment comfort, and confidence assessments. All numerical fields — strike, premium, collateral, annualized return, expiration — are sourced from the broker's options chain data via `ScoredCandidate`. When the LLM disagrees with the broker on a number, the broker wins and a `llm_broker_divergence` warning is logged.

## Data Flow

```
ScoredCandidate (broker data) ──┐
                                ├──► intent_builder.py ──► OrderIntent (DB)
CSPAnalysis (LLM output) ──────┤
                                │
ConvictionSignal (EMA engine) ──┘
```

## Numerical Field Resolution

Every numerical field uses `_resolve_numeric()` which follows this logic:

1. If broker value exists and is non-zero → use broker value
2. If LLM value diverges >5% from broker → log warning, still use broker
3. If no broker value available → fall back to LLM value

| Field | Broker Source (`ScoredCandidate`) | LLM Source (`CSPAnalysis`) |
|---|---|---|
| Strike | `candidate.strike` | `analysis.recommended_strike` |
| Premium (per share) | `candidate.mid` | `analysis.target_premium` |
| Collateral | `candidate.collateral_required` | `analysis.collateral_required` |
| Annualized Return % | `candidate.annualized_return_pct` | `analysis.annualized_return_pct` |
| Expiration | `candidate.expiration` | `analysis.recommended_expiration` |

### Premium Unit Sanity Check

LLMs sometimes confuse per-share and per-contract premium. If `premium > strike`, it's impossible for a put option and is auto-corrected by dividing by 100. A `premium_unit_correction` warning is logged.

## Combined Conviction Level

The conviction level shown to the user is the **minimum** of two independent assessments:

| Source | What It Measures |
|---|---|
| **EMA Conviction** (`ConvictionSignal.conviction_level`) | Deterministic trend state from 8/21 EMA analysis |
| **LLM Confidence** (`CSPAnalysis.confidence`) | AI's qualitative assessment of trade quality |

Ranking: high=3, medium=2, low=1, none=0. The lower rank wins.

**Example:** If EMA says "high" (strong uptrend) but the LLM says "low" (strike is near ATM, overextended), the user sees **LOW** conviction — not a misleadingly optimistic "high."

## Risk Gate (`evaluate_risk`)

Every intent runs through five deterministic checks. **All must pass** for `risk_passed=True`:

| # | Check | Fail Condition | Rationale |
|---|---|---|---|
| 1 | LLM confidence | `confidence == "low"` | If the AI isn't confident, neither should you be |
| 2 | Assignment comfort | `assignment_comfort == "low"` | You wouldn't want to hold this stock if assigned |
| 3 | CSP eligibility | `signal.csp_eligible == False` | Trend is broken (downtrend/consolidation) |
| 4 | ITM check | `strike >= underlying_price` | Put is in-the-money, assignment near-certain |
| 5 | EMA extension | `price_to_8ema_pct > 15%` | Blow-off top risk; >10% generates a warning |

The risk summary stored on the intent contains each check result as a tagged string:
- `"FAIL: ..."` — check failed (red in UI)
- `"WARN: ..."` — check passed but borderline (amber in UI)
- Plain text — check passed (gray in UI)

## Intent Sorting

Intents are sorted in the API response by:
1. `risk_passed` — passed intents first
2. Conviction level — high → medium → low → none
3. Created date — newest first

## Stale Intent Management

- `POST /intents/bulk-expire?max_age_hours=48` — expires pending/approved intents older than the threshold
- `DELETE /intents/expired` — permanently removes expired intents from the database
- UI "Expire Stale" button triggers bulk-expire with 48-hour default

## LLM-Only Fields

These fields are taken directly from the LLM with no broker override (no numerical alternative exists):

- `thesis` — trade rationale narrative
- `risks` — list of risk factors
- `invalidation` — what breaks the thesis
- `confidence` — qualitative self-assessment (used in risk gate + conviction)
- `assignment_comfort` — would you hold if assigned (used in risk gate)
- `suggested_contracts` — position sizing suggestion
