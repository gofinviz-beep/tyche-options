const BASE_URL = "/api/v1";

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Request failed: ${response.status}`);
  }

  return response.json();
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
    getLatest: () => request<import("@/types").ScanResult>("/scanner/latest"),
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

  system: {
    getConfig: () => request<import("@/types").SystemConfig>("/system/config"),
    getScheduler: () => request<Record<string, unknown>>("/system/scheduler"),
  },
};
