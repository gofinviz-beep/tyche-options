# Live Scan Workflow

**Source:** `backend/scripts/live_scan.py`

## Purpose

The live scan is a standalone CLI script that runs the full conviction-to-allocation pipeline against real-time market data. It identifies today's best CSP and CC opportunities and produces an optimal portfolio allocation using the MILP optimizer.

## Pipeline Steps

```mermaid
flowchart TD
    Start["Load TickerMetaStore\n+ OHLCVStore"] --> Filter["Universe Filter\nmarket cap >= $5B\nvalid exchange"]
    Filter -->|"~1100 tickers"| LoadOHLCV["Load OHLCV data\nfor qualified tickers"]
    LoadOHLCV --> Conviction["ConvictionEngine\n8/21 EMA analysis"]
    Conviction -->|"~10-20 CSP-eligible\n(uptrend + pullback)"| Quotes["Tradier: Live Quotes\nfor eligible tickers"]
    Quotes --> OptionsCSP["Tradier: Options Chains\nDTE 3-14\nPullback: 5% below EMA\nUptrend: OTM range"]
    OptionsCSP -->|"CSP candidates"| Allocator["PortfolioAllocator\nMILP Optimizer"]
    Start --> Positions["Tradier: Broker Positions\n(equity with >= 100 shares)"]
    Positions --> OptionsCC["Tradier: Options Chains\nfor held positions"]
    OptionsCC -->|"CC candidates"| Allocator
    Allocator --> Output["Optimal Portfolio\n+ Capital Utilization"]
```

## Step-by-Step

### 1. Load and Filter Universe

Load `TickerMetaStore` for market caps and exchanges. Filter to tickers with:
- Market cap >= $5 Billion
- Exchange in `{XNYS, XNAS, XNMS, XASE, ARCX, BATS}`

Typical result: ~1,100 qualified tickers from ~12,000+ in the store.

### 2. Load OHLCV + Run Conviction Engine

Load historical daily bars from `OHLCVStore` for all qualified tickers. Run `ConvictionEngine.analyze_batch()` which:
- Computes 8/21 EMA for each ticker
- Classifies trend state
- Checks CSP eligibility via two paths:
  - **Uptrend path:** extension <= 3% + 5-10 day streak above both EMAs
  - **Pullback path:** prior streak >= 5 days + rising 21-EMA slope (stock pulling back to EMA support)

Typical result: ~10-20 CSP-eligible tickers (mix of uptrend and pullback entries).

### 3. Fetch Live Quotes (Tradier)

Call `TradierClient.get_quotes()` for all eligible tickers. Display last price, bid, ask, and volume.

### 4. Scan CSP Candidates

For each eligible ticker with a live price >= $15:

1. Fetch available option expirations from Tradier
2. Filter to expirations within DTE range (default: 3-14 days)
3. **Limit to nearest N expirations** (`max_expiration_dates`, default: 2) to cap API calls
4. Fetch the options chain for each valid expiration
5. **Filter strikes by EMA range**:
   - **Uptrend tickers:** `strike >= 8-EMA * (1 - strike_range_pct/100)`. For example, if the 8-EMA is $100 and `strike_range_pct=15`, only strikes >= $85 are scanned.
   - **Pullback tickers:** strikes bounded between `support_EMA * (1 - pullback_strike_offset_pct/100)` and `support_EMA`. For example, if the 21-EMA is $100 and offset is 5%, only strikes between $95 and $100 are scanned.
6. Find the best put strike: `strike <= price * (1 - OTM_PCT)` with `bid > 0`, pick the highest strike below the OTM threshold
7. Build a `ScoredCandidate` with premium, collateral, and annualized return

### 5. Scan CC Candidates (Broker Positions)

1. Fetch current positions from Tradier
2. Filter to equity positions with >= 100 shares
3. For each position with a live price >= $15:
   - Fetch options chain for the nearest valid expiration
   - Find the best call: `strike > last_price` AND `strike >= cost_basis`, pick by highest bid
   - Build a `ScoredCandidate`

### 6. MILP Optimization

Pass all CSP and CC candidates to `PortfolioAllocator.optimize()` with:
- Available capital (default: $100,000)
- Conviction signals from step 2
- Held shares from step 5

The optimizer returns the optimal trade allocation respecting all constraints.

## Constants

| Constant | Value | Description |
|---|---|---|
| `AVAILABLE_CAPITAL` | $100,000 | Cash available for CSP collateral |
| `MAX_POSITIONS` | 8 | Maximum distinct positions |
| `MAX_CONTRACTS` | 40 | Maximum contracts per position |
| `MAX_CONCENTRATION_PCT` | 25.0% | Maximum per-symbol collateral concentration |
| `MIN_MARKET_CAP` | $5 Billion | Minimum market capitalization |
| `MIN_PRICE` | $15 | Minimum stock price |
| `MIN_VOLUME` | 500,000 | Minimum average daily volume |
| `DTE_MIN` | 3 | Minimum days to expiration |
| `DTE_MAX` | 14 | Maximum days to expiration |
| `OTM_PCT` | 5% | Out-of-the-money target for strike selection |
| `MAX_EXPIRATION_DATES` | 2 | Max number of nearest expiration dates to scan per ticker |
| `STRIKE_RANGE_PCT` | 15% | Only consider strikes within this % below 8-EMA (uptrend path) |
| `PULLBACK_STRIKE_OFFSET_PCT` | 5% | Strikes within this % below support EMA (pullback path) |
| `LLM_CONCURRENCY` | 5 | Max parallel LLM (Gemini) calls during analysis |

## Running the Live Scan

```bash
cd backend
source .venv/bin/activate
python scripts/live_scan.py
```

Required environment variables:
- `TYCHE_TRADIER_API_TOKEN` — Tradier API token (production, not sandbox)
- `TYCHE_TRADIER_ACCOUNT_ID` — Tradier account ID
- `TYCHE_POLYGON_API_KEY` — Polygon.io API key (for bootstrap, not needed if data exists)

The script expects `data/ohlcv_daily.parquet` and `data/ticker_meta.parquet` to exist (run bootstrap first).

## Output Format

The script outputs four sections:

1. **Conviction-eligible tickers** — Table showing symbol, price, EMAs, extension %, days above, market cap
2. **Live quotes** — Real-time bid/ask/last from Tradier
3. **Options scanning** — Count of CSP and CC candidates found
4. **Optimal portfolio** — MILP-optimized allocation with contract counts, premiums, collateral, and annualized returns

## Morning Scan (API)

The same pipeline runs via the API endpoint `POST /scanner/scan`, orchestrated by `workflow/morning_scan.py`. The API version additionally:
- Loads account balances from the broker (uses actual buying power when no `available_capital` override)
- Applies institutional ownership filters
- Fetches earnings dates
- Runs **per-ticker parallel LLM analysis** (Gemini) with `llm_concurrency` semaphore — avoids token overflow for large universes
- **Persists results** to distributed SQLite (scan runs, candidates, LLM analyses) — results survive backend restarts
- Enforces `scan_retention_count` (default 5), cleaning up older scans after each run
- Returns results as JSON via the REST API

### Scanner API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/scanner/scan` | Trigger a new scan. Query params: `tickers` (optional — omit for full universe), `top_n` (default 50), `enable_llm`, `target_expiration`, `available_capital` (optional per-scan deploy capital override; defaults to Settings). |
| `GET` | `/scanner/latest` | Returns the most recent scan result, or `null` (200 OK) if none exists. |
| `GET` | `/scanner/history?limit=5` | Returns summary metadata for the last N scans. |
| `GET` | `/scanner/{scan_id}` | Returns full details for a specific scan by ID. |

The `GET /scanner/latest` endpoint returns `200 OK` with `null` when no scan has been persisted (e.g., after a fresh backend restart). It does **not** return a 404.
