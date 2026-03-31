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

export interface GateResult {
  gate: string;
  passed: boolean;
  actual: string;
  threshold: string;
  reason: string;
}

export interface ConvictionSignal {
  ticker: string;
  trend_state: string;
  conviction_level: string;
  raw_conviction: string;
  csp_eligible: boolean;
  is_watchlist: boolean;
  last_close: number;
  ema_8: number;
  ema_21: number;
  ema_8_slope: number;
  ema_21_slope: number;
  price_to_8ema_pct: number;
  price_to_21ema_pct: number;
  volume_declining_on_pullback: boolean;
  avg_volume_20d: number;
  latest_volume: number;
  days_above_both_emas: number;
  prior_streak: number;
  as_of_date?: string;
  market_cap: number | null;
  institutional_pct: number | null;
  gate_results: GateResult[];
}

export interface PipelineStage {
  name: string;
  input: number;
  output: number;
  dropped: number;
  detail: string;
}

export interface AllocatedTrade {
  symbol: string;
  option_type: string;
  strike: number;
  expiration: string;
  dte: number;
  contracts: number;
  bid: number;
  total_premium: number;
  collateral: number;
  annualized_return_pct: number;
  conviction: string;
  extension_pct: number;
  strategy: string;
}

export interface AllocationSummary {
  total_premium: number;
  capital_utilization_pct: number;
  solver_status: string;
  trades?: number;
  total_collateral?: number;
  positions_used?: number;
}

export interface ScanResult {
  scan_id: string;
  scanned_at: string;
  symbols_scanned: number;
  pipeline_stages: PipelineStage[];
  conviction_signals: Record<string, ConvictionSignal>;
  csp_candidates: CSPCandidate[];
  cc_candidates: CSPCandidate[];
  llm_analyses: CSPAnalysis[];
  earnings_context: Record<string, unknown>;
  institutional_ownership: Record<string, number>;
  allocation: AllocationSummary | null;
  allocated_trades: AllocatedTrade[];
  intents_created: number;
  errors: string[];
}

export interface ScanHistoryEntry {
  scan_id: string;
  scanned_at: string;
  trigger: string;
  symbols_scanned: number;
  csp_candidate_count: number;
  cc_candidate_count: number;
  llm_analysis_count: number;
  intents_created: number;
  errors_count: number;
}

export interface OrderIntent {
  id: string;
  created_at: string;
  updated_at: string;
  status: string;
  symbol: string;
  option_symbol?: string;
  side: string;
  strategy: string;
  strike?: number;
  expiration?: string;
  quantity: number;
  limit_price?: number;
  estimated_premium: number;
  collateral_required: number;
  annualized_return_pct: number;
  conviction_level: string;
  trend_state: string;
  thesis?: string;
  risks?: string;
  invalidation?: string;
  risk_passed: boolean;
  risk_summary?: string;
  approved_at?: string;
  rejected_at?: string;
  user_note?: string;
  executed_at?: string;
  actual_fill_price?: number;
  actual_quantity?: number;
  actual_premium?: number;
  broker_confirmation?: string;
  scan_id?: string;
  wheel_cycle_id?: string;
}

export interface OrderIntentList {
  intents: OrderIntent[];
  total: number;
  pending: number;
  approved: number;
  executed: number;
}

export interface CreateIntentRequest {
  symbol: string;
  strike: number;
  expiration: string;
  quantity: number;
  limit_price?: number;
  strategy?: string;
  side?: string;
  conviction_level?: string;
  trend_state?: string;
  thesis?: string;
}

export interface DataStoreStatus {
  exists: boolean;
  total_rows: number;
  ticker_count: number;
  earliest_date?: string;
  latest_date?: string;
  parquet_path: string;
}

export interface TrendSummary {
  strong_uptrend: number;
  uptrend: number;
  pullback_to_8ema: number;
  pullback_to_21ema: number;
  consolidation: number;
  downtrend: number;
  insufficient_data: number;
}

export interface ConvictionScanResult {
  scan_id: string;
  scanned_at: string;
  total_screened: number;
  eligible_count: number;
  uptrend_eligible: number;
  pullback_eligible: number;
  pullback_count: number;
  trend_summary: TrendSummary | null;
  signals: ConvictionSignal[];
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
  available_capital: number;
  risk_limits: Record<string, number>;
  wheel_params: Record<string, number>;
  universe_filters: Record<string, number>;
  options_scan: Record<string, number>;
  workflow_schedule: Record<string, string | number>;
  watchlist: string[];
}

export interface ConfigUpdateRequest {
  watchlist?: string[];
  available_capital?: number;
  max_risk_per_trade_pct?: number;
  max_account_exposure_pct?: number;
  max_concentration_per_ticker_pct?: number;
  max_open_positions?: number;
  max_new_trades_per_day?: number;
  max_contracts_per_position?: number;
  csp_target_dte_min?: number;
  csp_target_dte_max?: number;
  cc_target_dte_min?: number;
  cc_target_dte_max?: number;
  min_annualized_return_pct?: number;
  min_market_cap_millions?: number;
  min_institutional_pct?: number;
  min_avg_volume?: number;
  min_stock_price?: number;
  max_expiration_dates?: number;
  expiration_mode?: string;
  strike_range_pct?: number;
  llm_concurrency?: number;
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

// --- Position Monitor ---

export interface TrackPositionRequest {
  symbol: string;
  option_symbol: string;
  position_type?: string;
  strike: number;
  expiration: string;
  entry_price: number;
  contracts: number;
  underlying_at_entry: number;
}

export interface TrackPositionResponse {
  status: string;
  option_symbol: string;
  message: string;
}

export interface SuggestedAction {
  action: string;
  reason: string;
  details?: Record<string, unknown>;
}

export interface TrackedPositionAlert {
  severity: string;
  alert_type: string;
  symbol?: string;
  message: string;
  suggested_actions?: SuggestedAction[];
  timestamp?: string;
}

export interface TrackedPositionStatus {
  symbol: string;
  option_symbol: string;
  position_type: string;
  strike: number;
  expiration: string;
  entry_price: number;
  contracts: number;
  underlying_price: number;
  option_bid: number;
  option_ask: number;
  option_mid: number;
  delta: number;
  theta: number;
  pnl_per_contract: number;
  total_pnl: number;
  distance_to_strike_pct: number;
  dte: number;
  trend: {
    direction: string;
    velocity_per_min: number;
    price_change_pct: number;
    samples: number;
    window_minutes: number;
  };
  alerts: TrackedPositionAlert[];
}

export interface TrackedPositionsResult {
  tracked_count: number;
  positions: TrackedPositionStatus[];
  alerts: TrackedPositionAlert[];
}

// --- Pullback Alerts & Stock Buy Recommendations ---

export interface HistoricalBounceStats {
  pullback_type: string;
  event_count: number;
  median_peak_gain_pct: number;
  mean_peak_gain_pct: number;
  p25_peak_gain_pct: number;
  p75_peak_gain_pct: number;
  median_exit_gain_pct: number;
  win_rate_5pct: number;
  win_rate_10pct: number;
  median_days_to_peak: number;
  median_days_to_exit: number;
  avg_max_drawdown_pct: number;
  suggested_exit_pct: number;
}

export interface BacktestProfile {
  ticker: string;
  pullback_type: string;
  event_count: number;
  median_peak_gain_pct: number;
  mean_peak_gain_pct: number;
  p25_peak_gain_pct: number;
  p75_peak_gain_pct: number;
  median_exit_gain_pct: number;
  win_rate_5pct: number;
  win_rate_10pct: number;
  median_days_to_peak: number;
  median_days_to_exit: number;
  avg_max_drawdown_pct: number;
  last_computed: string | null;
}

export interface BacktestTickerDetail {
  ticker: string;
  profiles: BacktestProfile[];
  recent_events: BacktestEvent[];
}

export interface BacktestEvent {
  ticker: string;
  pullback_type: string;
  entry_date: string;
  entry_price: number;
  peak_date: string;
  peak_price: number;
  peak_gain_pct: number;
  exit_date: string;
  exit_price: number;
  exit_gain_pct: number;
  days_to_peak: number;
  days_to_exit: number;
  max_drawdown_pct: number;
  volume_declining_at_entry: number;
}

export interface PullbackAlert {
  ticker: string;
  alert_type: "pullback_8ema" | "pullback_21ema";
  severity: "info" | "high";
  trend_state: string;
  conviction_level: string;
  raw_conviction: string;
  last_close: number;
  ema_8: number;
  ema_21: number;
  ema_8_slope: number;
  ema_21_slope: number;
  volume_declining: boolean;
  institutional_pct: number | null;
  institutional_label: string;
  suggested_action: string;
  position_size_hint: "standard" | "large";
  stop_loss_level: number;
  detected_at: string;
  market_cap: number | null;
  market_cap_label: string;
  exchange: string;
  name: string;
  days_above_both_emas: number;
  avg_volume_20d: number;
  price_to_8ema_pct: number;
  price_to_21ema_pct: number;
  historical_bounce: HistoricalBounceStats | null;
}

export interface StockBuyRecommendation {
  ticker: string;
  entry_type: "pullback_8ema" | "pullback_21ema";
  entry_price: number;
  target_ema_value: number;
  stop_loss: number;
  conviction: string;
  institutional_pct: number | null;
  institutional_label: string;
  volume_confirmation: boolean;
  position_size_hint: "standard" | "large";
  days_above_emas: number;
  ema_8_slope: number;
  ema_21_slope: number;
  related_csp_strike: number | null;
  has_active_csp: boolean;
  recommendation: string;
  risk_reward_note: string;
  created_at: string;
}

export interface CSPFallbackAlert {
  ticker: string;
  expired_strike: number;
  expiry_date: string;
  premium_collected: number;
  pullback_alert: PullbackAlert;
  message: string;
}

export interface ExpiredCSP {
  ticker: string;
  expired_strike: number;
  expiry_date: string;
  premium_collected: number;
  recorded_at: string;
}

export interface PullbackScanResult {
  scan_id: string;
  scanned_at: string;
  pullback_alerts: PullbackAlert[];
  stock_recommendations: StockBuyRecommendation[];
  csp_fallback_alerts: CSPFallbackAlert[];
  total_signals_analyzed: number;
}

// --- Conviction Snapshots & Transitions ---

export interface ConvictionSnapshot {
  ticker: string;
  as_of_date: string | null;
  trend_state: string;
  conviction_level: string;
  raw_conviction: string;
  csp_eligible: boolean;
  last_close: number;
  ema_8: number;
  ema_21: number;
  ema_8_slope: number;
  ema_21_slope: number;
  price_to_8ema_pct: number;
  price_to_21ema_pct: number;
  volume_declining: boolean;
  days_above_both_emas: number;
  avg_volume_20d: number;
  latest_volume: number;
  computed_at: string | null;
}

export interface ConvictionTransition {
  id: string;
  ticker: string;
  from_state: string;
  to_state: string;
  transition_date: string | null;
  last_close: number;
  ema_8: number;
  ema_21: number;
  ema_8_slope: number;
  ema_21_slope: number;
  conviction_level: string;
  raw_conviction: string;
  detected_at: string | null;
}

export interface ActivePullbacksResult {
  watchlist: PullbackAlert[];
  universe: PullbackAlert[];
  transitions_today: ConvictionTransition[];
  as_of_date: string;
}

export interface ConvictionBatchStatus {
  as_of_date: string;
  total_tickers_in_store: number;
  tickers_after_market_cap_filter: number;
  tickers_after_price_volume_filter: number;
  signals_computed: number;
  snapshots_upserted: number;
  transitions_detected: number;
  new_pullback_transitions: number;
  duration_ms: number;
  errors: string[];
}

export interface ConvictionHistory {
  ticker: string;
  snapshots: ConvictionSnapshot[];
  transitions: ConvictionTransition[];
}

export interface TransitionsList {
  transitions: ConvictionTransition[];
  from_date: string;
  to_date: string;
}

export interface StockRecommendationsResult {
  recommendations: StockBuyRecommendation[];
  as_of_date: string;
}

export interface TickerGatesResult {
  ticker: string;
  gate_results: GateResult[];
  error?: string;
}

export interface StockPosition {
  id: string;
  ticker: string;
  quantity: number;
  purchase_date: string | null;
  purchase_price: number;
  pullback_type: string;
  target_exit_pct: number | null;
  target_exit_price: number | null;
  stop_loss_price: number | null;
  current_price: number | null;
  current_gain_pct: number | null;
  status: string;
  exit_date: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ExitSignal {
  id: string;
  position_id: string;
  ticker: string;
  signal_type: string;
  trigger_price: number;
  current_price: number;
  gain_pct: number;
  triggered_at: string | null;
}

export interface ExitCheckResult {
  positions_checked: number;
  prices_updated: number;
  profit_targets_hit: number;
  stop_losses_hit: number;
  errors: number;
  signals: ExitSignal[];
}

export interface ExploreCandidate {
  symbol: string;
  option_symbol: string;
  strike: number;
  expiration: string;
  dte: number;
  bid: number;
  ask: number;
  mid: number;
  volume: number;
  open_interest: number;
  implied_volatility: number;
  delta: number;
  theta: number;
  underlying_price: number;
  premium_per_contract: number;
  collateral: number;
  max_contracts: number;
  total_premium: number;
  annualized_return_pct: number;
  score: number;
}

export interface ExploreResult {
  symbols_requested: number;
  symbols_with_options: number;
  expiration: string | null;
  total_contracts: number;
  available_capital: number;
  duration_ms: number;
  broker_cache: Record<string, number>;
  errors: string[];
  candidates: ExploreCandidate[];
}
