---
name: multibagger-discovery
description: >-
  Multi-bagger discovery engine checkpoint context for tyche-options. Use when
  implementing P2+, auditing P1, ingesting demand/options data, fixing ticker
  universe gaps (ADRC/ARM), or reading alpha discovery flags and artifacts.
---

# Multi-Bagger Discovery (Tyche)

## Authority

| Doc | Role |
|-----|------|
| `docs/multibagger_discovery_engine_v8_cursor_composer_spec.md` | **Current** implementation spec (P2+ next) |
| `docs/alpha/p1_completion_note.md` | P1 accepted with caveats — flags, artifacts, conservative invariance |
| `docs/alpha/estimate_snapshot_findings.md` | P0.3 — Finnhub has no historical as-of; local snapshots only |

v7 spec is superseded; do not start new work from v7.

## Phase status

- **P0 / P1:** Done and accepted (discovery flags default **off** for scoring).
- **P2:** Next — discovery labels + model namespace (v8 §P2).
- **Not done by design:** estimate-ramp unit fix, same-period revision ML features (P1.5-C).

## Discovery flags (`config.py`)

Master: `alpha_discovery_enabled=false`. Sub-flags (percentile, DAE) only apply when master is on.

Training defaults **on** independently: `alpha_class_weighting_enabled`, `alpha_purged_walk_forward_enabled`.

## Conservative Alpha

Live scoring / alpha batch unchanged when `alpha_discovery_enabled=false`. Locked by `tests/fixtures/alpha_conservative_fixture.json`.

## Ticker universe gotchas

1. **`filter_equity_only()`** — only `type == "CS"`.
2. **`EQUITY_TYPE_OVERRIDES`** in `market_data/data_store.py` — ADR names forced to CS on every `write_meta()` and meta refresh (currently `ARM`).
3. **Missing `ticker_meta` row** — OHLCV alone insufficient; seed meta before bulk demand/options.
4. **Per-ticker options backfill** — `ingest_options_flatfiles.py --tickers X --days-back N --force` (global `completed_dates` skips dates otherwise).

## Key artifacts

- Models: `backend/data/ml/models/`
- Alpha snapshots: `backend/data/alpha_signals.parquet`, `alpha_signals_sustained.parquet`
- Estimate snapshots: `backend/data/estimate_snapshots/{TICKER}.parquet` (append by `snapshot_date`)
- Demand gate: `backend/data/ml/alpha_results/demand_gate_verdict.json`

## GCP cloud ingest

Batch demand/options/alpha runs in Cloud Run when `TYCHE_DATA_BACKEND=gcs`.
Use skill `gcp-cloud-ops` for deploy, manifests, workflow issues, and Cloud
Logging queries (`job_phase` / `job_progress`). **Evening:** `ingest-demand-data`
(estimates/fundamentals). **Morning:** optional `run-demand-gate` (~4–8h) after
flatfiles+alpha — not required for `publish-signals`. `ingest-demand-data` writes
Benzinga guidance → `catalyst_signals/`; manifest tracks
`guidance_tickers_fetched` vs `guidance_catalysts_written`.

## Execution rules (from v8 contract)

- One task = one commit-sized diff.
- No conservative Alpha changes with discovery off.
- Preserve train/serve parity; no all-null training features.
- Add tests for behavior-changing tasks.
