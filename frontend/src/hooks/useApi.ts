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
    retry: false,
  });
}

export function useTriggerScan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      symbols,
      topN,
    }: {
      symbols?: string;
      topN?: number;
    }) => api.scanner.triggerScan(symbols, topN),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scanner"] });
    },
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
