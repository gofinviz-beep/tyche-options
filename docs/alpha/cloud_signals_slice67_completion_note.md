# Cloud-Computed Signals — Slices 6–7 Completion

**Status:** Slices 6–7 **complete** for the scoped pre-app-deploy path (stocks + options scanner/conviction).  
**Verified:** `tyche-data-prod`, 2026-06-27.

## Slice 6 — API cloud-mode enforcement

- `GET /scanner/latest` → `published/routes/options_scanner.json` (Parquet fallback).
- `GET /conviction/scan` → `options_conviction.json` or `signals/stocks/conviction.parquet`.
- `POST /scanner/scan`, `POST /scanner/explore`, `POST /covered-calls/analyze` → **409** when `TYCHE_DATA_BACKEND=gcs` and `TYCHE_ALLOW_INLINE_SCAN=false` (default).
- `publish_options_conviction()` builds route JSON from stocks conviction Parquet.

**Commit:** `96adaf4` (API + publisher).

## Slice 7 — Workflow integration

- All batch jobs deployed via `infra/gcp/deploy_jobs.sh`.
- `tyche-morning-pipeline` includes: flatfiles + alpha → optional demand gate → stocks batches → universe → chain-prep → scanner → publish → audit.
- Manual workflow: `tyche-options-morning-slice` (conviction → universe → chain-prep → scanner → publish).
- **E2E green:** workflow `46016e7d-b32d-429d-8bd7-7e461ac74299` **SUCCEEDED** 2026-06-27 (09:30–19:42 UTC, ~10h wall).

### Published artifacts (2026-06-27T19:39Z publish)

| Route | Status | Rows |
|-------|--------|------|
| `options_scanner.json` | ok | 10 CSP candidates |
| `options_conviction.json` | ok | 2075 screened (239 ranked in UI payload) |
| `stocks_conviction.json` | ok | 2075 snapshots |

Local backend in GCS mode loads Options Scanner pre-scanned data without inline Tradier/OHLCV scans.

## Out of scope (deferred)

| Item | Notes |
|------|--------|
| `tyche-options-snapshot-batch` | Optional post-open Tradier; **not** in morning workflow (flatfile chain-prep is canonical). |
| `signals/options/explore.parquet` | Placeholder publish; Explore POST blocked in GCS mode. |
| `signals/options/monitor.parquet` | Monitor is in-memory + live broker for tracked positions. |
| `signals/options/covered_calls.parquet` | CC analyze blocked in GCS mode until batch exists. |
| Separate `signals/options/conviction.parquet` | Options conviction published from `signals/stocks/conviction.parquet`. |

## Operational SLA (target vs observed)

**Product target:** prior-session computed signals **ready by ~7 AM PT** for laptop/Cloud Run app reads.

| Milestone | Target (PT) | Observed (2026-06-27) |
|-----------|-------------|------------------------|
| Massive flatfile available | ~2:00 AM | (vendor) |
| Scheduler fires morning workflow | 2:30 AM | 2:30 AM ✓ |
| Flatfile ingest complete | ~6:30 AM | ~6:30 AM ✓ (typical) |
| **Publish (UI-ready JSON)** | **~7:00 AM** | **~12:39 PM** ✗ |

**Root cause:** `tyche-run-demand-gate` runs **sequentially after** flatfiles+alpha and **blocks** all signal batches + publish until it finishes (~5h in this run; spec allows 4–8h). UI path does not need gate output — publish uses alpha-batch + stocks conviction, not gate-promoted models.

See **Morning pipeline optimization** in `infra/gcp/README.md` and spec §10.

## Next optimization (not implemented)

1. **Decouple demand gate from UI publish path** — run gate in parallel (evening or async) so conviction → scanner → publish can start when flatfiles complete (~6:30 AM PT).
2. **Evening stocks batches** — run `stocks-conviction` + `stocks-derived` after evening OHLCV ingest (6 PM PT) so morning only waits on flatfiles + options chain.
3. **`TYCHE_DEMAND_GATE_REUSE_DATASET=true`** — skip ~90 min dataset rebuild when `ml/alpha_dataset.parquet` exists.
4. **Frontend** — disable/relabel “Scan Now” in GCS mode (409 is correct API behavior).
