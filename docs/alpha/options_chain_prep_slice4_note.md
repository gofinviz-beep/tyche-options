# Options Chain Prep — Slice 4 (revised)

**Status:** Flatfile-sourced prep in morning pipeline; Tradier is optional post-open.

## Why not Tradier at 2:30 AM?

The morning pipeline runs before market open. Tradier live chains at that hour are stale or empty — **no value for CSP scanning**. Massive/Polygon OPRA flatfiles (prior trading day) are the correct overnight input and already run via `tyche-ingest-options-flatfiles` in parallel with alpha.

## Two data sources, two roles

| Source | When | What it has | Role |
|--------|------|-------------|------|
| **Massive flatfiles** → `options_history/` | Morning ~2:30 AM | Prior session **traded** contracts: close, volume | **Slice 4 prep** + IV/VRP (derived/) |
| **Tradier live** → `options_chains/` | Post-open only | Full chain: bid/ask, OI, greeks | Optional live refresh |

Flatfiles **do not** have bid/ask spread, open interest, or full strike grids — only contracts that traded. Slice 5 scanner must treat `source=flatfile` rows differently (close as premium proxy, relax OI gate).

## Slice 4 job: `tyche-options-chain-prep-batch`

**Not** a Tradier fetch. Reads **500 candidates only** from flatfile history:

```text
ingest-options-flatfiles  →  options_history/{TICKER}.parquet
candidate-universe-batch  →  options_candidates.parquet
        ↓
options-chain-prep-batch
        ↓
signals/options/options_chain_contracts.parquet   scanner input (contract rows)
signals/options/options_chain_snapshot.parquet  per-ticker status summary
reports/options_chain_prep/manifest.json
```

Runs **after** flatfiles + candidate-universe, **before** publish (and before Slice 5 scanner batch).

## Optional: `tyche-options-snapshot-batch` (Tradier)

Kept as a **manual / future post-open** job — **not** in the morning workflow. Use when live bid/ask/OI matter (e.g. after 7 AM PT / market open). Writes to `options_chains/` + overwrites summary with `source=tradier`.

## Slice 5 implications

Scanner batch (`tyche-options-scanner-batch`) should:
1. Read **`csp_scan_tickers.parquet`** (not the full 500-candidate list)
2. Read `signals/stocks/conviction.parquet` for strike targeting / scoring context
3. Read `options_chain_contracts.parquet` (not call Tradier in morning)
4. When `source=flatfile`: use `close` as premium, `min_oi=0`, label results `"as of prior close"`

Optional Tradier refresh (`tyche-options-snapshot-batch`) also reads **`csp_scan_tickers.parquet`**, not `options_candidates`.

## Deploy / verify

```bash
gcloud run jobs execute tyche-options-chain-prep-batch --wait
gsutil ls gs://tyche-data-prod/signals/options/
gsutil cat gs://tyche-data-prod/reports/options_chain_prep/manifest.json
```
