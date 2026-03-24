export interface AccountBalance {
  cash: number;
  buying_power: number;
  net_liquidation_value: number;
  market_value: number;
  total_equity: number;
  open_pl: number;
  close_pl: number;
  pending_cash: number;
  captured_at: string;
}

export interface Position {
  id: string;
  symbol: string;
  quantity: number;
  cost_basis: number;
  average_cost: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_pl_pct: number;
  option_symbol?: string;
  option_type?: string;
  strike?: number;
  expiration?: string;
  strategy: string;
  contracts: number;
  wheel_cycle_id?: string;
}

export interface AccountSummary {
  balance: AccountBalance;
  positions: Position[];
  position_count: number;
  total_unrealized_pl: number;
  cash_available_for_csp: number;
}

export interface CSPCandidate {
  symbol: string;
  option_symbol: string;
  strike: number;
  expiration: string;
  dte: number;
  bid: number;
  ask: number;
  premium_per_contract: number;
  collateral_required: number;
  annualized_return_pct: number;
  score: number;
  delta: number;
  theta: number;
  implied_volatility: number;
  volume: number;
  open_interest: number;
  earnings_within_dte: boolean;
  earnings_date?: string;
}

export interface ScanResult {
  scan_id: string;
  scanned_at: string;
  symbols_scanned: number;
  csp_candidates: CSPCandidate[];
  cc_candidates: CSPCandidate[];
  llm_analyses: CSPAnalysis[];
  earnings_context: Record<string, unknown>;
  errors: string[];
}

export interface CSPAnalysis {
  ticker: string;
  assignment_comfort: "high" | "medium" | "low";
  assignment_comfort_reasoning: string;
  thesis: string;
  recommended_strike: number;
  recommended_expiration: string;
  target_premium: number;
  annualized_return_pct: number;
  earnings_proximity?: string;
  earnings_risk_assessment?: string;
  invalidation: string;
  confidence: "low" | "medium" | "high";
  risks: string[];
  would_you_hold_if_assigned: string;
  suggested_contracts: number;
  collateral_required: number;
  allocation_mode: "concentrated" | "diversified";
}

export interface OpenOrder {
  id: string;
  broker_order_id: string;
  symbol: string;
  option_symbol?: string;
  side: string;
  order_type: string;
  quantity: number;
  limit_price?: number;
  status: string;
  intent: string;
  strategy: string;
  duration: string;
  captured_at: string;
}

export interface OrderMonitorAlert {
  order_id: string;
  symbol: string;
  limit_price: number;
  underlying_price: number;
  option_bid?: number;
  option_ask?: number;
  volume_at_strike?: number;
  oi_at_strike?: number;
  distance_to_fill_pct?: number;
  attention?: string;
}

export interface OrderMonitorResult {
  monitored_at: string;
  orders_checked: number;
  alerts: OrderMonitorAlert[];
  analyses: unknown[];
  errors: string[];
}

export interface WatchlistEntry {
  symbol: string;
  last?: number;
  bid?: number;
  ask?: number;
  volume?: number;
  change_pct?: number;
  next_earnings?: string;
  earnings_time?: string;
}

export interface SystemConfig {
  sandbox_mode: boolean;
  preview_only: boolean;
  broker_configured: boolean;
  llm_configured: boolean;
  earnings_api_configured: boolean;
  risk_limits: Record<string, number>;
  wheel_params: Record<string, number>;
  workflow_schedule: Record<string, string | number>;
  watchlist: string[];
}

export interface OrderPreviewRequest {
  symbol: string;
  option_symbol?: string;
  side: string;
  quantity: number;
  order_type?: string;
  limit_price?: number;
  duration?: string;
  intent?: string;
}

export interface OrderPreviewResponse {
  estimated_cost: number;
  estimated_commission: number;
  estimated_fees: number;
  estimated_premium: number;
  collateral_required: number;
  risk_results: RiskRuleResult[];
  all_rules_passed: boolean;
  warnings: string[];
}

export interface RiskRuleResult {
  rule_name: string;
  passed: boolean;
  reason: string;
  details?: Record<string, unknown>;
}
