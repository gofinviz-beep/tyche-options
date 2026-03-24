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
