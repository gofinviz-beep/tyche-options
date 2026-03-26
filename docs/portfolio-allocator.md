# Portfolio Allocator — MILP Optimizer

**Source:** `backend/src/tyche/strategy/allocator.py`

## Purpose

Given a set of scored CSP and CC option candidates, available capital, and risk constraints, the portfolio allocator solves a Mixed Integer Linear Program (MILP) to find the combination of trades that maximizes risk-adjusted premium income. It uses the HiGHS solver via `scipy.optimize.milp` with a greedy fallback.

## Mathematical Formulation

### Decision Variables

For each candidate `i` in `{0, ..., n-1}`:

- `x_i` — number of contracts to sell (integer >= 0)
- `y_i` — binary indicator (1 if `x_i > 0`, else 0)

### Objective Function

Maximize total risk-adjusted premium:

```
maximize  sum(x_i * premium_i * risk_weight_i)   for i = 0..n-1
```

Since `scipy.optimize.milp` minimizes, the implementation negates the objective:

```
c_obj[i] = -(premium_per_contract_i * risk_weight_i)
```

### Constraints

**1. Capital Budget (CSPs only)**

Total collateral for CSP positions must not exceed available capital:

```
sum(x_i * strike_i * 100)  <=  available_capital     for all CSP candidates i
```

Covered calls do not require cash collateral (shares are already held).

**2. Concentration Limit (per symbol)**

No single symbol's collateral may exceed `max_concentration_pct` of total capital:

```
sum(x_i * collateral_i)  <=  (max_concentration_pct / 100) * available_capital
    for all candidates i with the same symbol
```

Default: 25% maximum per symbol.

**3. Maximum Positions**

Total number of distinct positions (where `x_i > 0`) must not exceed the limit:

```
sum(y_i)  <=  max_positions
```

Default: 8 positions.

**4. Linking Constraint**

Connects the continuous variable `x_i` to the binary indicator `y_i`:

```
x_i  <=  M_i * y_i     for all i
```

Where `M_i` is the upper bound for candidate `i` (either max contracts or capital-constrained).

**5. Maximum Contracts per Position**

```
x_i  <=  max_contracts_per_position     for all i
```

Default: 40 contracts.

**6. Covered Call Share Limit**

For CC candidates, contracts are bounded by shares held:

```
x_i  <=  held_shares[symbol] // 100     for CC candidates i
```

## Risk Weight Computation

Each candidate receives a composite risk weight that multiplies its premium in the objective function. Higher weight = more favorable to the optimizer.

```
risk_weight = conviction_weight * extension_proximity * liquidity_factor
```

### Conviction Weight

Based on the conviction engine's assessment:

| Conviction Level | Weight |
|---|---|
| high | 1.0 |
| medium | 0.7 |
| low | 0.3 |
| none | 0.1 |

### Extension Proximity

Stocks closer to the 8-EMA (less extended) receive higher weight:

```
extension_proximity = 1.0 - (abs(extension_pct) / max_extension_pct) * 0.3
clamped to [0.5, 1.0]
```

At 0% extension: weight = 1.0. At 3% extension: weight = 0.7.

### Liquidity Factor

Based on open interest relative to a threshold (1000 for CSPs, 500 for CCs):

```
liquidity_factor = min(1.0, open_interest / threshold)
```

Candidates with low open interest are penalized.

## Greedy Fallback

If the MILP solver fails or returns a non-optimal solution, the allocator falls back to a greedy strategy:

1. Sort candidates by `premium * risk_weight` descending
2. Iterate through candidates in order
3. For each candidate, allocate the maximum contracts that fit within:
   - Remaining capital
   - Concentration limit for the symbol
   - Max contracts per position
   - Shares held (for CCs)
4. Stop when `max_positions` is reached

The greedy fallback is deterministic and always produces a valid allocation.

## Output: AllocationResult

```python
@dataclass
class AllocationResult:
    trades: list[AllocatedTrade]      # Selected trades with contract counts
    total_premium: float              # Sum of all premiums
    total_collateral: float           # Sum of all collateral requirements
    capital_utilization_pct: float    # total_collateral / available_capital * 100
    positions_used: int               # Number of distinct positions
    solver_status: str                # "optimal", "greedy_fallback", or error
    available_capital: float          # Input capital
```

Each `AllocatedTrade` includes:
- Symbol, option type (put/call), strike, expiration, DTE
- Contract count, bid, premium per contract, total premium
- Collateral required, annualized return percentage
- Conviction level, extension percentage, strategy type

## Integration Points

The allocator is used in three places:

1. **Morning scan** (`workflow/morning_scan.py`): After CSP and CC candidates are identified, the allocator produces the optimal portfolio. Results are included in the `MorningScanResult`.

2. **Live scan script** (`scripts/live_scan.py`): Standalone CLI tool that runs the full pipeline and displays the optimal allocation.

3. **Capital-aware backtest** (`scripts/backtest_ema.py`): The `run_capital_simulation()` function uses a sub-allocator per day to simulate realistic portfolio-level performance.

## Configuration

| Setting | Env Var | Default |
|---|---|---|
| Max positions | `TYCHE_MAX_OPEN_POSITIONS` | 8 |
| Max contracts per position | `TYCHE_MAX_CONTRACTS_PER_POSITION` | 40 |
| Max concentration per ticker | `TYCHE_MAX_CONCENTRATION_PER_TICKER_PCT` | 25.0% |
| Max extension | `TYCHE_MAX_EXTENSION_PCT` | 3.0% |
