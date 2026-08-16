"""Directional-Alpha PERSISTENCE layer.

The nightly alpha batch overwrites a single snapshot, so day-over-day
consistency is not stored anywhere. This script *reconstructs* the daily alpha
snapshot for the last N trading sessions by rebuilding the feature panel over a
short date window and re-scoring every (ticker, date) row through the exact
production engine + breakout predictor. It then computes a per-ticker
persistence / stability score to separate genuine, consistently-ranked names
from one-day wonders, and overlays each name's upcoming earnings date.

Output:
  /tmp/alpha_panel.parquet        cached windowed feature panel (reuse with --reuse)
  /tmp/alpha_persistence.json     ranked gems + per-name trend series + earnings

Run:  .venv/bin/python scripts/alpha_persistence.py --window-days 50 --variant sustained
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta

import pandas as pd

from tyche.config import get_settings
from tyche.ml.dataset import build_dataset
from tyche.ml.xgb_baseline import ALPHA_SUSTAINED_TARGETS, ALPHA_TARGETS
from tyche.strategy.alpha_engine import build_alpha_score_engine
from tyche.workflow.alpha_persistence import (
    compute_persistence,
    persist_response,
    response_from_scored,
)

PANEL_CACHE = "/tmp/alpha_panel.parquet"
OUT_JSON = "/tmp/alpha_persistence.json"
_SUS_TO_CANON = dict(zip(ALPHA_SUSTAINED_TARGETS, ALPHA_TARGETS))


def select_candidates(net: int, variant: str) -> list[str]:
    """Candidate universe = union of the current snapshot's top-`net` names by
    alpha_score across the peak and sustained variants. This is the wide net
    that plausibly contains every gem; persistence then ranks within it.
    """
    from tyche.market_data.alpha_store import AlphaSignalStore

    s = get_settings()
    picks: set[str] = set()
    for v in {"peak", variant}:
        try:
            sigs, as_of, _ = AlphaSignalStore(data_dir=s.data_dir, variant=v).read_latest()
            ranked = sorted(sigs, key=lambda r: r.get("alpha_score") or 0, reverse=True)
            top = [str(r.get("ticker", "")).upper() for r in ranked[:net]]
            picks.update(t for t in top if t)
            print(f"[candidates] {v}: snapshot as_of={as_of} n={len(sigs)} took top {len(top)}")
        except Exception as e:
            print(f"[candidates] {v} read failed: {e}")
    out = sorted(picks)
    print(f"[candidates] union universe = {len(out)} tickers")
    return out


def build_panel(candidates: list[str], lookback_days: int, min_cap: float, reuse: bool) -> pd.DataFrame:
    if reuse and os.path.exists(PANEL_CACHE):
        print(f"[panel] reusing cache {PANEL_CACHE}")
        return pd.read_parquet(PANEL_CACHE)
    end = date.today()
    # Long lookback so EMA-50 / 252d-return / RS features are valid; we slice to
    # the recent sessions AFTER scoring. (MIN_BARS=60, but 252d features need ~1y.)
    start = end - timedelta(days=lookback_days)
    print(f"[panel] build_dataset {start} -> {end} over {len(candidates)} candidates ...")
    panel = build_dataset(
        data_dir=get_settings().data_dir,
        start_date=start,
        end_date=end,
        min_market_cap=min_cap,
        tickers=candidates,
        include_neighbors=True,
        include_etf=True,
        include_correlation=True,
        include_market_context=True,
        include_momentum=True,
        include_demand=True,
        job_name="alpha-persistence",
    )
    if panel.empty:
        print("[panel] EMPTY — check candidates / lookback")
        return panel
    print(f"[panel] rows={len(panel)} tickers={panel['ticker'].nunique()} "
          f"dates={panel['date'].nunique()}")
    panel.to_parquet(PANEL_CACHE, index=False)
    return panel


def score_panel(panel: pd.DataFrame, variant: str) -> pd.DataFrame:
    """Re-score every (ticker, date) row -> daily alpha snapshot reconstruction."""
    from tyche.ml.breakout import BreakoutPredictor

    s = get_settings()
    targets = ALPHA_SUSTAINED_TARGETS if variant == "sustained" else ALPHA_TARGETS
    bp = BreakoutPredictor(data_dir=s.data_dir, targets=targets)
    predictor = bp if bp.is_available else None
    print(f"[score] variant={variant} ml_available={predictor is not None}")

    engine = build_alpha_score_engine(
        discovery_enabled=s.alpha_discovery_enabled,
        percentile_signals=s.alpha_percentile_signals_enabled,
        demand_adjusted_extension=s.alpha_demand_adjusted_extension_enabled,
        demand_mult_ceil_discovery=s.alpha_demand_mult_ceil_discovery,
    )

    panel = panel.reset_index(drop=True)
    probs = predictor.predict_proba_batch(panel) if predictor is not None else {}
    if variant == "sustained" and probs:
        probs = {_SUS_TO_CANON.get(k, k): v for k, v in probs.items()}

    signals = engine.score_from_features(panel, breakout_probs=probs)
    dates = pd.to_datetime(panel["date"]).dt.date.tolist()

    recs = []
    for i, sig in enumerate(signals):
        hp = {
            "swing": sig.breakout_prob_swing,
            "trend": sig.breakout_prob_trend,
            "thematic": sig.breakout_prob_thematic,
        }
        mp = hp.get(sig.horizon)
        if mp is None:
            vals = [v for v in hp.values() if v is not None]
            mp = max(vals) if vals else None
        recs.append({
            "date": dates[i],
            "ticker": sig.ticker,
            "alpha_score": sig.alpha_score,
            "signal": sig.signal,
            "horizon": sig.horizon,
            "move_prob": mp,
            "demand_net": (sig.demand.net if sig.demand else None),
            "market_cap": sig.market_cap,
        })
    df = pd.DataFrame(recs)
    df["rank"] = df.groupby("date")["alpha_score"].rank(ascending=False, method="min")
    return df.sort_values(["date", "rank"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-days", type=int, default=520,
                    help="OHLCV read window (must cover 252d features + warmup)")
    ap.add_argument("--sessions", type=int, default=30,
                    help="recent trading sessions used for persistence metrics")
    ap.add_argument("--min-cap", type=float, default=1e9)
    ap.add_argument("--variant", default="sustained", choices=["sustained", "peak"])
    ap.add_argument("--net", type=int, default=400, help="candidate universe size per variant")
    ap.add_argument("--top", type=int, default=100, help="final gems to emit")
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--from-history", action="store_true",
                    help="cheap path: read persisted daily snapshots instead of "
                         "rebuilding + re-scoring the feature panel")
    ap.add_argument("--persist", action="store_true",
                    help="also write signals/alpha/persistence_{variant}.json artifact")
    args = ap.parse_args()

    settings = get_settings()

    if args.from_history:
        resp = compute_persistence(
            settings, variant=args.variant, sessions=args.sessions, top=args.top
        )
        if resp is None:
            print("[abort] no alpha history yet — run the nightly alpha batch a few "
                  "times (or backfill) before using --from-history")
            return
    else:
        candidates = select_candidates(args.net, args.variant)
        if not candidates:
            print("[abort] no candidates from snapshot")
            return
        panel = build_panel(candidates, args.lookback_days, args.min_cap, args.reuse)
        if panel.empty:
            print("[abort] empty panel")
            return
        scored = score_panel(panel, args.variant)
        keep_dates = sorted(scored["date"].unique())[-args.sessions:]
        scored = scored[scored["date"].isin(keep_dates)].copy()
        resp = response_from_scored(
            scored, variant=args.variant, top=args.top, settings=settings
        )
        if resp is None:
            print("[abort] empty persistence table")
            return

    if args.persist:
        rel = persist_response(resp, settings)
        print(f"[persist] wrote artifact {rel}")

    out = resp.model_dump(mode="json")
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, indent=2, default=str)

    print(f"\n[done] sessions={resp.sessions} range={resp.date_range} "
          f"universe={resp.universe_size} gems={resp.total}")
    print(f"[done] wrote {OUT_JSON}\n")
    cols = ["ticker", "persistence", "mean_alpha", "last_alpha", "pct_buy",
            "pct_top100", "mean_rank", "std_rank", "signal_churn", "last_signal",
            "earnings_date", "days_to_earnings"]
    rows = [{c: g.get(c) for c in cols} for g in out["gems"]]
    print(pd.DataFrame(rows).head(30).to_string(index=False))


if __name__ == "__main__":
    main()
