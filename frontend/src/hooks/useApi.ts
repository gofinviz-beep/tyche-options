import { useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export function useAccountSummary() {
  return useQuery({
    queryKey: ["account", "summary"],
    queryFn: api.account.getSummary,
    refetchInterval: 30_000,
  });
}

export function usePositions() {
  return useQuery({
    queryKey: ["account", "positions"],
    queryFn: api.account.getPositions,
    refetchInterval: 30_000,
  });
}

export function useOpenOrders() {
  return useQuery({
    queryKey: ["orders", "open"],
    queryFn: api.orders.getOpen,
    refetchInterval: 15_000,
  });
}

export function useLatestScan() {
  return useQuery({
    queryKey: ["scanner", "latest"],
    queryFn: api.scanner.getLatest,
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}

export function useTriggerScan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      symbols,
      topN,
      enableLlm,
    }: {
      symbols?: string;
      topN?: number;
      enableLlm?: boolean;
    }) => api.scanner.triggerScan(symbols, topN, enableLlm),
    onSuccess: (data) => {
      queryClient.setQueryData(["scanner", "latest"], data);
      queryClient.invalidateQueries({ queryKey: ["scanner"] });
    },
  });
}

export function useExploreOptions() {
  return useMutation({
    mutationFn: ({ symbols, capital }: { symbols: string; capital?: number }) =>
      api.scanner.explore(symbols, capital),
  });
}

export function useScanHistory(limit = 5) {
  return useQuery({
    queryKey: ["scanner", "history", limit],
    queryFn: () => api.scanner.getHistory(limit),
  });
}

export function useScanById(scanId: string | null) {
  return useQuery({
    queryKey: ["scanner", "detail", scanId],
    queryFn: () => api.scanner.getById(scanId!),
    enabled: !!scanId,
  });
}

export function useOrderMonitor() {
  return useQuery({
    queryKey: ["orders", "monitor"],
    queryFn: api.orders.monitor,
    refetchInterval: 60_000,
  });
}

export function useWatchlist() {
  return useQuery({
    queryKey: ["watchlist"],
    queryFn: api.watchlist.get,
    refetchInterval: 30_000,
  });
}

export function useSystemConfig() {
  return useQuery({
    queryKey: ["system", "config"],
    queryFn: api.system.getConfig,
  });
}

export function useUpdateConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: import("@/types").ConfigUpdateRequest) =>
      api.system.updateConfig(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["system", "config"] });
    },
  });
}

export function useCancelOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (orderId: string) => api.orders.cancel(orderId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
  });
}

export function usePreviewOrder() {
  return useMutation({
    mutationFn: api.orders.preview,
  });
}

// --- Conviction hooks ---

/**
 * Polls the conviction cache version endpoint every 5 minutes.
 * When last_computed_at changes, invalidates all conviction-dependent queries
 * so they re-fetch from the (now-fresh) backend cache.
 */
export function useConvictionVersion() {
  const queryClient = useQueryClient();
  const prevComputedAt = useRef<string | null>(null);

  const query = useQuery({
    queryKey: ["conviction", "version"],
    queryFn: api.conviction.getVersion,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    const computedAt = query.data?.last_computed_at ?? null;
    if (
      computedAt &&
      prevComputedAt.current !== null &&
      computedAt !== prevComputedAt.current
    ) {
      queryClient.invalidateQueries({ queryKey: ["conviction", "scan"] });
      queryClient.invalidateQueries({
        queryKey: ["stocks", "conviction-snapshots"],
      });
      queryClient.invalidateQueries({ queryKey: ["stocks", "deep-dips"] });
      queryClient.invalidateQueries({ queryKey: ["stocks", "pullbacks"] });
      queryClient.invalidateQueries({
        queryKey: ["stocks", "recommendations"],
      });
    }
    prevComputedAt.current = computedAt;
  }, [query.data?.last_computed_at, queryClient]);

  return query;
}

export function useDataStoreStatus() {
  return useQuery({
    queryKey: ["conviction", "status"],
    queryFn: api.conviction.getStatus,
  });
}

export function useBootstrapData() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (days?: number) => api.conviction.bootstrap(days),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conviction"] });
    },
  });
}

export function useUpdateDailyData() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.conviction.updateDaily,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conviction"] });
    },
  });
}

export function useConvictionScan(symbols?: string, autoRun = false) {
  return useQuery({
    queryKey: ["conviction", "scan", symbols],
    queryFn: () => api.conviction.scan(symbols),
    enabled: autoRun,
    staleTime: Infinity,
    gcTime: 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}

export function useConvictionSignal(ticker: string) {
  return useQuery({
    queryKey: ["conviction", "signal", ticker],
    queryFn: () => api.conviction.getSignal(ticker),
    enabled: !!ticker,
  });
}

export function useTriggerConvictionScan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (symbols?: string) => api.conviction.scan(symbols),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conviction", "scan"] });
    },
  });
}

// --- Stocks module hooks ---

export function useActivePullbacks() {
  return useQuery({
    queryKey: ["stocks", "pullbacks"],
    queryFn: api.stocks.getActivePullbacks,
    staleTime: Infinity,
    gcTime: 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}

export function useStockRecommendations() {
  return useQuery({
    queryKey: ["stocks", "recommendations"],
    queryFn: api.stocks.getRecommendations,
    staleTime: Infinity,
    gcTime: 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}

export function useConvictionHistory(ticker: string, days = 30) {
  return useQuery({
    queryKey: ["stocks", "history", ticker, days],
    queryFn: () => api.stocks.getConvictionHistory(ticker, days),
    enabled: !!ticker,
  });
}

export function useConvictionTransitions(days = 7, toStates?: string) {
  return useQuery({
    queryKey: ["stocks", "transitions", days, toStates],
    queryFn: () => api.stocks.getTransitions(days, toStates),
  });
}

export function useRefreshConviction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.stocks.refreshConviction(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stocks"] });
    },
  });
}

export function useCspFallbacks() {
  return useQuery({
    queryKey: ["stocks", "csp-fallbacks"],
    queryFn: api.stocks.getCspFallbacks,
    refetchInterval: 60_000,
  });
}

export function useExpiredCsps() {
  return useQuery({
    queryKey: ["stocks", "expired-csps"],
    queryFn: api.stocks.getExpiredCsps,
  });
}

export function useRecordCspExpiry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      ticker: string;
      strike: number;
      expiry_date: string;
      premium_collected: number;
    }) => api.stocks.recordCspExpiry(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stocks"] });
    },
  });
}

export function useRemoveCspExpiry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ticker: string) => api.stocks.removeCspExpiry(ticker),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stocks"] });
    },
  });
}

export function useConvictionSnapshots(asOfDate?: string) {
  return useQuery({
    queryKey: ["stocks", "conviction-snapshots", asOfDate],
    queryFn: () => api.stocks.getConvictionSnapshots(asOfDate),
    staleTime: Infinity,
    gcTime: 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}

export function useDeepDips() {
  return useQuery({
    queryKey: ["stocks", "deep-dips"],
    queryFn: () => api.stocks.getDeepDips(),
    staleTime: Infinity,
    gcTime: 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}

export function useTickerGates(ticker: string | null) {
  return useQuery({
    queryKey: ["stocks", "gates", ticker],
    queryFn: () => api.stocks.getTickerGates(ticker!),
    enabled: !!ticker,
  });
}

export function useBacktestProfiles() {
  return useQuery({
    queryKey: ["stocks", "backtest-profiles"],
    queryFn: () => api.stocks.getBacktestProfiles(),
    staleTime: 1000 * 60 * 60,
  });
}

export function useBacktestProfile(ticker: string | null) {
  return useQuery({
    queryKey: ["stocks", "backtest-profile", ticker],
    queryFn: () => api.stocks.getBacktestProfile(ticker!),
    enabled: !!ticker,
  });
}

// --- Position Monitor hooks ---

export function useTrackedPositions() {
  return useQuery({
    queryKey: ["monitor", "positions"],
    queryFn: api.positionMonitor.getPositions,
    refetchInterval: 30_000,
  });
}

export function useTrackPosition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: import("@/types").TrackPositionRequest) =>
      api.positionMonitor.track(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["monitor"] });
    },
  });
}

export function useUntrackPosition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (optionSymbol: string) =>
      api.positionMonitor.untrack(optionSymbol),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["monitor"] });
    },
  });
}

// --- Order Intent hooks ---

export function useOrderIntents(status?: string) {
  return useQuery({
    queryKey: ["intents", status],
    queryFn: () => api.intents.list(status),
    refetchInterval: 15_000,
  });
}

export function useCreateIntent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: import("@/types").CreateIntentRequest) =>
      api.intents.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["intents"] });
    },
  });
}

export function useOrderIntent(id: string) {
  return useQuery({
    queryKey: ["intents", id],
    queryFn: () => api.intents.get(id),
    enabled: !!id,
  });
}

export function useApproveIntent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) =>
      api.intents.approve(id, note),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["intents"] });
    },
  });
}

export function useRejectIntent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      api.intents.reject(id, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["intents"] });
    },
  });
}

export function useRecordExecution() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: {
        fill_price: number;
        quantity: number;
        premium_received?: number;
        broker_confirmation?: string;
      };
    }) => api.intents.recordExecution(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["intents"] });
    },
  });
}

export function useBulkExpireIntents() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (maxAgeHours?: number) =>
      api.intents.bulkExpire(maxAgeHours),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["intents"] });
    },
  });
}

export function useStockPositions(activeOnly = false) {
  return useQuery({
    queryKey: ["stockPositions", activeOnly],
    queryFn: () => api.stocks.getPositions(activeOnly),
    staleTime: 30_000,
  });
}

export function useActivePositions() {
  return useQuery({
    queryKey: ["stockPositions", "active"],
    queryFn: () => api.stocks.getActivePositions(),
    staleTime: 30_000,
  });
}

export function useCreatePosition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      ticker: string;
      purchase_price: number;
      quantity: number;
      purchase_date: string;
      pullback_type: string;
    }) => api.stocks.createPosition(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stockPositions"] });
    },
  });
}

export function useExitPosition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      exitPrice,
      exitReason,
    }: {
      id: string;
      exitPrice: number;
      exitReason?: string;
    }) => api.stocks.exitPosition(id, exitPrice, exitReason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stockPositions"] });
    },
  });
}

export function useDeletePosition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.stocks.deletePosition(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stockPositions"] });
    },
  });
}

export function useCheckExits() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.stocks.checkExits(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stockPositions"] });
      queryClient.invalidateQueries({ queryKey: ["exitSignals"] });
    },
  });
}

export function useRecentSignals() {
  return useQuery({
    queryKey: ["exitSignals"],
    queryFn: () => api.stocks.getRecentSignals(),
    staleTime: 30_000,
  });
}

// --- News ---

export function useNewsSignals() {
  return useQuery({
    queryKey: ["news", "signals"],
    queryFn: () => api.news.getSignals(),
    staleTime: 2 * 60 * 1000,
    gcTime: 5 * 60 * 1000,
  });
}

export function useNewsArticles(ticker: string | null) {
  return useQuery({
    queryKey: ["news", "articles", ticker],
    queryFn: () => api.news.getArticles(ticker!),
    enabled: !!ticker,
    staleTime: 2 * 60 * 1000,
  });
}

export function useTriggerNewsIngest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.news.triggerIngest(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["news"] });
    },
  });
}

export function useFilingSignals() {
  return useQuery({
    queryKey: ["filings", "signals"],
    queryFn: () => api.filings.getSignals(),
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}

export function useFiling8K(ticker: string | null, days = 90) {
  return useQuery({
    queryKey: ["filings", "8k", ticker, days],
    queryFn: () => api.filings.get8KFilings(ticker!, days),
    enabled: !!ticker,
    staleTime: 5 * 60 * 1000,
  });
}

export function useInsiderTransactions(ticker: string | null, days = 90) {
  return useQuery({
    queryKey: ["filings", "insider", ticker, days],
    queryFn: () => api.filings.getInsiderTransactions(ticker!, days),
    enabled: !!ticker,
    staleTime: 5 * 60 * 1000,
  });
}

export function useTriggerEdgarIngest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.filings.triggerIngest(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["filings"] });
    },
  });
}

// --- Covered Calls ---

export function useCCAnalysis() {
  return useMutation({
    mutationFn: ({
      positions,
      targetDte,
    }: {
      positions: import("@/types").CCPosition[];
      targetDte?: number;
    }) => api.coveredCalls.analyze(positions, targetDte),
  });
}

export function useCCTickerAnalysis(
  ticker: string | null,
  params: { shares?: number; cost_basis?: number; target_dte?: number } = {},
) {
  return useQuery({
    queryKey: ["coveredCalls", ticker, params],
    queryFn: () => api.coveredCalls.analyzeTicker(ticker!, params),
    enabled: !!ticker,
    staleTime: 60_000,
  });
}
