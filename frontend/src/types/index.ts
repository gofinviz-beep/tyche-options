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
  conviction_score: number;
  csp_safety_prob: number | null;
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
  ema_50: number;
  ema_50_slope: number;
  rsi_14: number;
  iv_rank: number | null;
  iv_percentile: number | null;
  atm_iv: number | null;
  vrp: number | null;
  market_cap: number | null;
  institutional_pct: number | null;
  sector: string | null;
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
  available_capital?: number;
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
  errors_count: number;
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
  options_scan: Record<string, number | string>;
  conviction_engine: Record<string, number>;
  pullback_csp: Record<string, number | boolean>;
  workflow_schedule: Record<string, string | number>;
  options_snapshot: Record<string, number | boolean | string>;
  notifications: Record<string, boolean | string>;
  llm: Record<string, string>;
  scan_persistence: Record<string, number>;
  news_pipeline: Record<string, number | boolean>;
  edgar_pipeline: Record<string, number | boolean>;
  watchlist: string[];
}

export interface ConfigUpdateRequest {
  [key: string]: string | number | boolean | string[] | undefined;
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
  underlying_at_entry: number;
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
  ema_50: number;
  ema_50_slope: number;
  rsi_14: number;
  iv_rank: number | null;
  iv_percentile: number | null;
  atm_iv: number | null;
  vrp: number | null;
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
  sector: string | null;
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
  ema_50_slope: number;
  rsi_14: number;
  iv_rank: number | null;
  iv_percentile: number | null;
  atm_iv: number | null;
  vrp: number | null;
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
  conviction_score: number;
  csp_safety_prob: number | null;
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
  ema_50: number;
  ema_50_slope: number;
  rsi_14: number;
  iv_rank: number | null;
  iv_percentile: number | null;
  atm_iv: number | null;
  vrp: number | null;
  computed_at: string | null;
  market_cap: number | null;
  institutional_pct: number | null;
  sector: string | null;
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

// --- News ---

export interface NewsSignal {
  ticker: string;
  news_impact_score: number;
  negative_count_24h: number;
  positive_count_24h: number;
  total_count_24h: number;
  dominant_event_type: string | null;
  last_negative_at: string | null;
  last_positive_at: string | null;
  has_risk: boolean;
  updated_at: string | null;
}

export interface NewsArticle {
  article_id: string;
  source: string;
  title: string;
  published_at: string;
  url: string;
  author: string | null;
  summary: string | null;
  event_type: string | null;
  sentiment: string | null;
  impact_score: number | null;
  relevance: string | null;
}

export interface NewsIngestResult {
  polygon_fetched: number;
  finnhub_fetched: number;
  total_persisted: number;
  tickers_updated: number;
  articles_classified: number;
  signals_rebuilt: number;
  errors: string[];
}

// --- Filings (EDGAR) ---

export interface FilingSignal {
  ticker: string;
  last_8k_at: string | null;
  last_8k_sentiment: string | null;
  last_8k_impact: number | null;
  eightk_count_30d: number;
  insider_net_shares_30d: number;
  insider_buy_count_30d: number;
  insider_sell_count_30d: number;
  insider_cluster_sell: boolean;
  last_insider_tx_at: string | null;
  has_risk: boolean;
  updated_at: string | null;
}

export interface EdgarIngestResult {
  tickers_resolved: number;
  tickers_failed_cik: number;
  eightk_fetched: number;
  eightk_persisted: number;
  form4_fetched: number;
  insider_tx_persisted: number;
  errors: string[];
  duration_ms: number;
}

export interface Filing8K {
  accession_no: string;
  form_type: string;
  filed_at: string | null;
  description: string | null;
  filing_url: string | null;
  items_reported: string | null;
  content_summary: string | null;
  event_type: string | null;
  sentiment: string | null;
  impact_score: number | null;
}

export interface InsiderTransaction {
  accession_no: string;
  filed_at: string | null;
  period_of_report: string | null;
  insider_name: string;
  insider_title: string | null;
  is_officer: boolean;
  is_director: boolean;
  is_ten_pct_owner: boolean;
  transaction_type: string;
  shares: number;
  price_per_share: number;
  total_value: number;
  shares_owned_after: number;
  acquisition_or_disposition: string;
}

// ── Deep Dip (Oversold Recovery) ─────────────────────────────────────

export interface DipClassification {
  catalyst: string;
  risk_level: string;
  reasons: string[];
  actionable: boolean;
  news_impact_score: number | null;
  negative_news_count: number;
  insider_cluster_sell: boolean;
  last_8k_impact: number | null;
}

export interface MarketContext {
  concurrent_dips: number;
  total_universe: number;
  market_dip_breadth: number;
  spy_return_5d: number | null;
  spy_drawdown_from_high: number | null;
  spy_rsi_14: number | null;
  is_broad_selloff: boolean;
}

export interface RecoverySignal {
  actionable: boolean;
  recovery_20d_est: string;
  recovery_40d_est: string;
  meets_all_thresholds: boolean;
  threshold_checks: string[];
  suggested_cc_dte: string;
  peak_recovery_est: string;
}

export interface DeepDipAlert {
  ticker: string;
  alert_type: "oversold_21ema" | "oversold_50ema";
  severity: "info" | "high";
  trend_state: string;
  conviction_level: string;
  last_close: number;
  ema_8: number;
  ema_21: number;
  ema_50: number;
  ema_8_slope: number;
  ema_21_slope: number;
  ema_50_slope: number;
  rsi_14: number;
  prior_streak: number;
  dip_pct: number;
  price_to_21ema_pct: number;
  price_to_50ema_pct: number;
  iv_rank: number | null;
  vrp: number | null;
  conviction_score: number;
  volume_declining: boolean;
  institutional_pct: number | null;
  suggested_action: string;
  position_size_hint: "standard" | "large";
  stop_loss_level: number;
  market_cap: number | null;
  market_cap_label: string;
  sector: string | null;
  name: string;
  dip_classification: DipClassification | null;
  recovery_signal: RecoverySignal | null;
  detected_at: string;
}

export interface DeepDipScanResult {
  alerts: DeepDipAlert[];
  total_analyzed: number;
  total_oversold: number;
  total_actionable: number;
  market_context: MarketContext | null;
  as_of_date: string;
}

// --- Covered Call Recommender ---

export interface CCPosition {
  ticker: string;
  shares: number;
  cost_basis: number;
}

export interface CCSignal {
  ticker: string;
  signal: "GO" | "WAIT" | "CAUTION";
  signal_reason: string;
  last_close: number;
  ema_8: number;
  ema_21: number;
  ema_50: number;
  ema_21_slope: number;
  extension_pct_8: number;
  extension_pct_21: number;
  rsi_14: number;
  iv_rank: number | null;
  vrp: number | null;
  rv_20d: number | null;
  suggested_strike: number;
  suggested_otm_pct: number;
  suggested_expiry_dte: number;
  suggested_premium_est: number | null;
  optimal_entry_day: string;
  assignment_prob_1w: number;
  assignment_prob_2w: number;
  estimated_next_earnings: string | null;
  earnings_in_window: boolean;
  price_source: "ohlcv_close" | "live_tradier" | string;
  live_price: number | null;
  prev_close: number | null;
}

export interface CCDeepDive {
  signal: CCSignal;
  total_episodes: number;
  episode_table: Record<string, unknown>[];
  days_to_8ema: Record<string, number>;
  days_to_21ema: Record<string, number>;
  days_to_50ema: Record<string, number>;
  drawdown_at_8ema: Record<string, number>;
  drawdown_at_21ema: Record<string, number>;
  forward_returns: Record<string, unknown>[];
  dow_analysis: Record<string, unknown>[];
  rally_peak_day_distribution: Record<string, number>;
  call_candidates: Record<string, unknown>[] | null;
  pnl_scenarios: Record<string, unknown>;
  recommended_action: {
    action: string;
    instruction: string;
    ticker: string;
    contracts: number;
    strike: number;
    otm_pct: number;
    expiration_date: string | null;
    expiration_label: string | null;
    actual_dte: number;
    entry_timing: string;
    premium_est_per_share: number | null;
    total_premium_est: number | null;
    net_premium_est: number | null;
    assignment_prob: number;
    pullback_prob_by_expiry: number;
    safety_reasons: string[];
    warnings: string[];
    reason?: string;
    premium_source?: "live_tradier" | "historical_estimate" | string;
    live_bid?: number;
    live_ask?: number;
    live_mid?: number;
    live_iv?: number;
    live_volume?: number;
    live_oi?: number;
    live_delta?: number;
    live_theta?: number;
    option_symbol?: string;
    price_source?: "live_tradier" | "ohlcv_close" | string;
    live_price?: number;
    prev_close?: number;
  };
}

export interface CCPortfolioAnalysis {
  analyses: CCDeepDive[];
  portfolio_summary: {
    total_premium_est: number;
    positions_go: number;
    positions_wait: number;
    positions_caution: number;
    total_positions: number;
  };
}

// ── Directional Alpha ───────────────────────────────────────────────────

export interface AlphaFactorScores {
  momentum: number;
  relative_strength: number;
  trend_quality: number;
  breakout: number;
  volume_thrust: number;
}

export interface AlphaDemandDimensions {
  fund: number | null;
  est: number | null;
  catalyst: number | null;
  policy: number | null;
  squeeze: number | null;
  net: number | null;
}

export interface AlphaSignal {
  ticker: string;
  alpha_score: number;
  signal: "strong_buy" | "buy" | "watch" | "avoid";
  horizon: "swing" | "trend" | "thematic" | "none";
  factors: AlphaFactorScores;
  breakout_prob_swing: number | null;
  breakout_prob_trend: number | null;
  breakout_prob_thematic: number | null;
  last_close: number;
  return_63d: number | null;
  return_126d: number | null;
  return_252d: number | null;
  rs_126d: number | null;
  pct_off_52w_high: number | null;
  ema_stack_score: number;
  volume_thrust_ratio: number | null;
  as_of_date: string | null;
  regime: "revenue" | "narrative";
  demand: AlphaDemandDimensions | null;
  demand_multiplier: number | null;
  overextension_score: number | null;
  overextension_penalty: number | null;
  market_cap: number | null;
  institutional_pct: number | null;
  sector: string | null;
  is_watchlist: boolean;
}

export interface AlphaScanResult {
  scanned_at: string;
  as_of_date: string | null;
  computed_at: string | null;
  ml_available: boolean;
  variant: string;
  total: number;
  strong_buy_count: number;
  buy_count: number;
  signals: AlphaSignal[];
}

export interface AlphaBatchResult {
  status: string;
  signals: number;
  buy_signals: number;
  ml_available: boolean;
  as_of_date: string | null;
  elapsed_s: number | null;
}
