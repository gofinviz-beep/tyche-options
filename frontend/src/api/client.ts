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
    triggerScan: (symbols?: string, topN = 5) => {
      const params = new URLSearchParams({ top_n: String(topN) });
      if (symbols) params.set("symbols", symbols);
      return request<import("@/types").ScanResult>(`/scanner/scan?${params}`, {
        method: "POST",
      });
    },
    getLatest: () => request<import("@/types").ScanResult | null>("/scanner/latest"),
    getHistory: (limit = 5) =>
      request<import("@/types").ScanHistoryEntry[]>(`/scanner/history?limit=${limit}`),
    getById: (scanId: string) =>
      request<import("@/types").ScanResult>(`/scanner/${scanId}`),
  },

  orders: {
    getOpen: () => request<import("@/types").OpenOrder[]>("/orders/open"),
    preview: (data: import("@/types").OrderPreviewRequest) =>
      request<import("@/types").OrderPreviewResponse>("/orders/preview", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    execute: (data: Record<string, unknown>) =>
      request<Record<string, unknown>>("/orders/execute", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    cancel: (orderId: string) =>
      request<Record<string, string>>(`/orders/${orderId}`, {
        method: "DELETE",
      }),
    monitor: () =>
      request<import("@/types").OrderMonitorResult>("/orders/monitor"),
  },

  watchlist: {
    get: () => request<import("@/types").WatchlistEntry[]>("/watchlist/"),
  },

  conviction: {
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

  intents: {
    list: (status?: string) => {
      const params = new URLSearchParams();
      if (status) params.set("status", status);
      const qs = params.toString();
      return request<import("@/types").OrderIntentList>(
        `/intents${qs ? `?${qs}` : ""}`,
      );
    },
    create: (data: import("@/types").CreateIntentRequest) =>
      request<import("@/types").OrderIntent>("/intents", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    get: (id: string) =>
      request<import("@/types").OrderIntent>(`/intents/${id}`),
    approve: (id: string, note?: string) =>
      request<import("@/types").OrderIntent>(`/intents/${id}/approve`, {
        method: "POST",
        body: JSON.stringify({ user_note: note }),
      }),
    reject: (id: string, reason?: string) =>
      request<import("@/types").OrderIntent>(`/intents/${id}/reject`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      }),
    recordExecution: (
      id: string,
      data: {
        fill_price: number;
        quantity: number;
        premium_received?: number;
        broker_confirmation?: string;
      },
    ) =>
      request<import("@/types").OrderIntent>(`/intents/${id}/execute`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    bulkExpire: (maxAgeHours = 48) =>
      request<{ expired: number; cutoff: string }>(
        `/intents/bulk-expire?max_age_hours=${maxAgeHours}`,
        { method: "POST" },
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
    checkExits: () =>
      request<import("@/types").ExitCheckResult>(
        "/stocks/positions/check-exits",
        { method: "POST" },
      ),
    getRecentSignals: () =>
      request<import("@/types").ExitSignal[]>("/stocks/positions/signals"),
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
};
