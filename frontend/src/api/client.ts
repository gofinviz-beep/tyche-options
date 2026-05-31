import { telemetry } from "@/lib/telemetry";

const BASE_URL = "/api/v1";

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const start = performance.now();
  let status = 0;

  try {
    const response = await fetch(`${BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json", ...options.headers },
      ...options,
    });

    status = response.status;
    const durationMs = performance.now() - start;

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      let message: string;
      if (typeof error.detail === "string") {
        message = error.detail;
      } else if (Array.isArray(error.detail)) {
        message = error.detail
          .map((e: { msg?: string; loc?: string[] }) =>
            e.msg ? `${(e.loc ?? []).slice(-1).join(".")}: ${e.msg}` : JSON.stringify(e),
          )
          .join("; ");
      } else {
        message = `Request failed: ${response.status}`;
      }
      telemetry.reportError(path, status, message, durationMs);
      throw new Error(message);
    }

    telemetry.reportTiming(path, durationMs, status);
    return response.json();
  } catch (err) {
    const durationMs = performance.now() - start;
    if (status === 0) {
      telemetry.reportError(path, 0, (err as Error).message, durationMs, {
        network_error: true,
      });
    }
    throw err;
  }
}

export const api = {
  account: {
    getSummary: () => request<import("@/types").AccountSummary>("/account/summary"),
    getBalances: () => request<import("@/types").AccountBalance>("/account/balances"),
    getPositions: () => request<import("@/types").Position[]>("/account/positions"),
  },

  scanner: {
    triggerScan: (symbols?: string, topN = 5, enableLlm?: boolean, targetExpiration?: string, availableCapital?: number) => {
      const params = new URLSearchParams({ top_n: String(topN) });
      if (symbols) params.set("symbols", symbols);
      if (enableLlm !== undefined) params.set("enable_llm", String(enableLlm));
      if (targetExpiration) params.set("target_expiration", targetExpiration);
      if (availableCapital !== undefined) params.set("available_capital", String(availableCapital));
      return request<import("@/types").ScanResult>(`/scanner/scan?${params}`, {
        method: "POST",
      });
    },
    getLatest: () => request<import("@/types").ScanResult | null>("/scanner/latest"),
    getHistory: (limit = 5) =>
      request<import("@/types").ScanHistoryEntry[]>(`/scanner/history?limit=${limit}`),
    getById: (scanId: string) =>
      request<import("@/types").ScanResult>(`/scanner/${scanId}`),
    explore: (symbols: string, capital?: number) => {
      const params = new URLSearchParams({ symbols });
      if (capital) params.set("available_capital", String(capital));
      return request<import("@/types").ExploreResult>(
        `/scanner/explore?${params}`,
        { method: "POST" },
      );
    },
  },

  watchlist: {
    get: () => request<import("@/types").WatchlistEntry[]>("/watchlist/"),
  },

  conviction: {
    getVersion: () =>
      request<{ last_computed_at: string | null; as_of_date: string | null }>(
        "/conviction/version",
      ),
    getStatus: () =>
      request<import("@/types").DataStoreStatus>("/conviction/status"),
    bootstrap: (days = 120) =>
      request<Record<string, unknown>>("/conviction/bootstrap", {
        method: "POST",
        body: JSON.stringify({ days }),
      }),
    updateDaily: () =>
      request<Record<string, unknown>>("/conviction/update", {
        method: "POST",
      }),
    scan: (symbols?: string) => {
      const params = new URLSearchParams();
      if (symbols) params.set("symbols", symbols);
      const qs = params.toString();
      return request<import("@/types").ConvictionScanResult>(
        `/conviction/scan${qs ? `?${qs}` : ""}`,
      );
    },
    getSignal: (ticker: string) =>
      request<import("@/types").ConvictionSignal>(
        `/conviction/signal/${ticker}`,
      ),
  },

  positionMonitor: {
    getPositions: () =>
      request<import("@/types").TrackedPositionsResult>("/monitor/positions"),
    track: (data: import("@/types").TrackPositionRequest) =>
      request<import("@/types").TrackPositionResponse>("/monitor/track", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    untrack: (optionSymbol: string) =>
      request<Record<string, string>>(
        `/monitor/track/${encodeURIComponent(optionSymbol)}`,
        { method: "DELETE" },
      ),
  },

  stocks: {
    getActivePullbacks: () =>
      request<import("@/types").ActivePullbacksResult>("/stocks/pullbacks/active"),
    getRecommendations: () =>
      request<import("@/types").StockRecommendationsResult>("/stocks/recommendations"),
    getConvictionHistory: (ticker: string, days = 30) =>
      request<import("@/types").ConvictionHistory>(
        `/stocks/conviction/history?ticker=${encodeURIComponent(ticker)}&days=${days}`,
      ),
    getTransitions: (days = 7, toStates?: string) => {
      const params = new URLSearchParams({ days: String(days) });
      if (toStates) params.set("to_states", toStates);
      return request<import("@/types").TransitionsList>(
        `/stocks/transitions?${params}`,
      );
    },
    refreshConviction: () =>
      request<import("@/types").ConvictionBatchStatus>("/stocks/conviction/refresh", {
        method: "POST",
      }),
    getCspFallbacks: () =>
      request<import("@/types").CSPFallbackAlert[]>("/stocks/csp-fallbacks"),
    getExpiredCsps: () =>
      request<import("@/types").ExpiredCSP[]>("/stocks/csp-expiries"),
    recordCspExpiry: (data: {
      ticker: string;
      strike: number;
      expiry_date: string;
      premium_collected: number;
    }) =>
      request<import("@/types").ExpiredCSP>("/stocks/csp-expiries", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    removeCspExpiry: (ticker: string) =>
      request<Record<string, string | number>>(
        `/stocks/csp-expiries/${encodeURIComponent(ticker)}`,
        { method: "DELETE" },
      ),
    getDeepDips: () =>
      request<import("@/types").DeepDipScanResult>("/stocks/deep-dips"),
    getConvictionSnapshots: (asOfDate?: string) => {
      const params = asOfDate ? `?as_of_date=${asOfDate}` : "";
      return request<import("@/types").ConvictionSnapshot[]>(
        `/stocks/conviction/snapshots${params}`,
      );
    },
    getTickerGates: (ticker: string) =>
      request<import("@/types").TickerGatesResult>(
        `/stocks/conviction/${encodeURIComponent(ticker)}/gates`,
      ),
    getBacktestProfiles: () =>
      request<import("@/types").BacktestProfile[]>("/stocks/backtest/profiles"),
    getBacktestProfile: (ticker: string) =>
      request<import("@/types").BacktestTickerDetail>(
        `/stocks/backtest/profile/${encodeURIComponent(ticker)}`,
      ),
    getPositions: (activeOnly = false) =>
      request<import("@/types").StockPosition[]>(
        `/stocks/positions${activeOnly ? "?active_only=true" : ""}`,
      ),
    getActivePositions: () =>
      request<import("@/types").StockPosition[]>("/stocks/positions/active"),
    createPosition: (data: {
      ticker: string;
      purchase_price: number;
      quantity: number;
      purchase_date: string;
      pullback_type: string;
    }) =>
      request<import("@/types").StockPosition>("/stocks/positions", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    exitPosition: (id: string, exitPrice: number, exitReason = "manual") =>
      request<Record<string, string>>(
        `/stocks/positions/${id}/exit?exit_price=${exitPrice}&exit_reason=${encodeURIComponent(exitReason)}`,
        { method: "POST" },
      ),
    deletePosition: (id: string) =>
      request<Record<string, string>>(`/stocks/positions/${id}`, {
        method: "DELETE",
      }),
    bulkImportPositions: (
      positions: { ticker: string; quantity: number; purchase_price: number; purchase_date?: string }[],
      skipDuplicates = true,
    ) =>
      request<{ created: number; skipped: number; errors: string[] }>(
        "/stocks/positions/bulk",
        {
          method: "POST",
          body: JSON.stringify({
            positions,
            skip_duplicates: skipDuplicates,
          }),
        },
      ),
    checkExits: () =>
      request<import("@/types").ExitCheckResult>(
        "/stocks/positions/check-exits",
        { method: "POST" },
      ),
    getRecentSignals: () =>
      request<import("@/types").ExitSignal[]>("/stocks/positions/signals"),
  },

  alpha: {
    scan: (params?: {
      signal?: string;
      horizon?: string;
      minScore?: number;
      minMarketCapMillions?: number;
      variant?: string;
      limit?: number;
    }) => {
      const q = new URLSearchParams();
      if (params?.signal) q.set("signal", params.signal);
      if (params?.horizon) q.set("horizon", params.horizon);
      if (params?.minScore) q.set("min_score", String(params.minScore));
      if (params?.minMarketCapMillions != null)
        q.set("min_market_cap_millions", String(params.minMarketCapMillions));
      if (params?.variant) q.set("variant", params.variant);
      if (params?.limit) q.set("limit", String(params.limit));
      const qs = q.toString();
      return request<import("@/types").AlphaScanResult>(
        `/alpha/scan${qs ? `?${qs}` : ""}`,
      );
    },
    getSignal: (ticker: string) =>
      request<import("@/types").AlphaSignal>(
        `/alpha/signal/${encodeURIComponent(ticker)}`,
      ),
    recompute: (maxTickers?: number) => {
      const qs = maxTickers ? `?max_tickers=${maxTickers}` : "";
      return request<import("@/types").AlphaBatchResult>(`/alpha/recompute${qs}`, {
        method: "POST",
      });
    },
  },

  system: {
    getConfig: () => request<import("@/types").SystemConfig>("/system/config"),
    updateConfig: (data: import("@/types").ConfigUpdateRequest) =>
      request<{ status: string; updated: Record<string, unknown> }>(
        "/system/config",
        { method: "PATCH", body: JSON.stringify(data) },
      ),
    getScheduler: () => request<Record<string, unknown>>("/system/scheduler"),
  },

  news: {
    getSignals: () =>
      request<import("@/types").NewsSignal[]>("/news/signals"),
    getSignal: (ticker: string) =>
      request<import("@/types").NewsSignal | null>(
        `/news/signals/${encodeURIComponent(ticker)}`,
      ),
    getArticles: (ticker: string, hours = 48) =>
      request<import("@/types").NewsArticle[]>(
        `/news/articles/${encodeURIComponent(ticker)}?hours=${hours}`,
      ),
    triggerIngest: () =>
      request<{ status: string; message: string }>("/news/ingest", {
        method: "POST",
      }),
  },

  coveredCalls: {
    analyze: (positions: import("@/types").CCPosition[], targetDte = 8) =>
      request<import("@/types").CCPortfolioAnalysis>("/covered-calls/analyze", {
        method: "POST",
        body: JSON.stringify({ positions, target_dte: targetDte }),
      }),
    analyzeTicker: (
      ticker: string,
      params: { shares?: number; cost_basis?: number; target_dte?: number } = {},
    ) => {
      const qs = new URLSearchParams();
      if (params.shares) qs.set("shares", String(params.shares));
      if (params.cost_basis) qs.set("cost_basis", String(params.cost_basis));
      if (params.target_dte) qs.set("target_dte", String(params.target_dte));
      const q = qs.toString();
      return request<import("@/types").CCDeepDive>(
        `/covered-calls/analyze/${encodeURIComponent(ticker)}${q ? `?${q}` : ""}`,
      );
    },
  },

  filings: {
    getSignals: () =>
      request<import("@/types").FilingSignal[]>("/filings/signals"),
    getSignal: (ticker: string) =>
      request<import("@/types").FilingSignal | null>(
        `/filings/signals/${encodeURIComponent(ticker)}`,
      ),
    get8KFilings: (ticker: string, days = 90) =>
      request<import("@/types").Filing8K[]>(
        `/filings/8k/${encodeURIComponent(ticker)}?days=${days}`,
      ),
    getInsiderTransactions: (ticker: string, days = 90) =>
      request<import("@/types").InsiderTransaction[]>(
        `/filings/insider/${encodeURIComponent(ticker)}?days=${days}`,
      ),
    triggerIngest: () =>
      request<{ status: string; message: string }>("/filings/ingest", {
        method: "POST",
      }),
  },
};
