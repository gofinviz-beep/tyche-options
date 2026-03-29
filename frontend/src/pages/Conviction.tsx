import { useState } from "react";
import { Card } from "@/components/Card";
import { StatusBadge } from "@/components/StatusBadge";
import {
  useDataStoreStatus,
  useBootstrapData,
  useUpdateDailyData,
  useConvictionScan,
  useTriggerConvictionScan,
} from "@/hooks/useApi";
import type { ConvictionSignal, GateResult } from "@/types";
import { ChevronDown, ChevronRight, Check, X, Minus, Database } from "lucide-react";

export function Conviction() {
  const { data: status, isLoading: statusLoading } = useDataStoreStatus();
  const bootstrap = useBootstrapData();
  const updateDaily = useUpdateDailyData();
  const manualScan = useTriggerConvictionScan();
  const [symbols, setSymbols] = useState("");
  const [showDataStore, setShowDataStore] = useState(false);

  const autoScan = useConvictionScan(undefined, !!status?.exists);

  const scanData = manualScan.data ?? autoScan.data;
  const scanLoading = manualScan.isPending || autoScan.isLoading;

  const handleScan = () => {
    manualScan.mutate(symbols || undefined);
  };

  const eligible = scanData?.signals.filter((s) => s.csp_eligible) ?? [];
  const excluded = scanData?.signals.filter((s) => !s.csp_eligible) ?? [];

  const storeReady = !!status?.exists;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Conviction Engine</h1>
        <p className="mt-1 text-sm text-gray-500">
          8/21 EMA trend analysis — identify stocks with strong uptrends for
          selling cash-secured puts. Expand any row to see the full eligibility
          pipeline.
        </p>
      </div>

      {/* Bootstrap prompt — only shown when data store is empty */}
      {!statusLoading && !storeReady && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <p className="text-sm font-medium text-amber-700">
            Data store not initialized
          </p>
          <p className="mt-1 text-xs text-amber-600">
            Bootstrap historical OHLCV data from Polygon.io before running scans.
          </p>
          <button
            onClick={() => bootstrap.mutate(120)}
            disabled={bootstrap.isPending}
            className="mt-3 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {bootstrap.isPending ? "Bootstrapping..." : "Bootstrap 120 Days"}
          </button>
          {bootstrap.isError && (
            <p className="mt-2 text-sm text-red-600">
              Failed: {bootstrap.error.message}
            </p>
          )}
          {bootstrap.isSuccess && (
            <p className="mt-2 text-sm text-emerald-600">
              Bootstrap complete. You can now run scans.
            </p>
          )}
        </div>
      )}

      {/* Scan input — the primary action */}
      {(storeReady || statusLoading) && (
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Enter tickers: AAPL, RIG, NO... (blank = watchlist or full universe)"
            value={symbols}
            onChange={(e) => setSymbols(e.target.value)}
            className="flex-1 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            onClick={handleScan}
            disabled={manualScan.isPending || !storeReady}
            className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {manualScan.isPending ? "Scanning..." : "Run Scan"}
          </button>
        </div>
      )}

      {manualScan.isError && (
        <p className="text-sm text-red-600">
          Scan failed: {manualScan.error.message}
        </p>
      )}

      {scanLoading && (
        <div className="flex h-24 items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
        </div>
      )}

      {/* Results */}
      {scanData && !scanLoading && (
        <>
          {/* Summary stats */}
          <div className="flex flex-wrap gap-3 text-sm">
            <div className="rounded-lg border border-gray-200 bg-white px-4 py-2 shadow-sm">
              <span className="text-gray-400">Screened: </span>
              <span className="font-semibold text-gray-900">{scanData.total_screened}</span>
            </div>
            <div className="rounded-lg border border-gray-200 bg-white px-4 py-2 shadow-sm">
              <span className="text-gray-400">Returned: </span>
              <span className="font-semibold text-gray-900">{scanData.signals.length}</span>
            </div>
            <div className={`rounded-lg border px-4 py-2 shadow-sm ${eligible.length > 0 ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}>
              <span className={eligible.length > 0 ? "text-emerald-600" : "text-amber-600"}>
                CSP Eligible: {eligible.length}
              </span>
            </div>
            {excluded.length > 0 && (
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-2">
                <span className="text-gray-500">
                  Excluded: {excluded.length}
                </span>
              </div>
            )}
          </div>

          {/* Eligible tickers */}
          {eligible.length > 0 && (
            <Card title="CSP Eligible" subtitle={`${eligible.length} ticker(s) passed all gates`}>
              <div className="space-y-0">
                {eligible.map((s) => (
                  <ExpandableSignalRow key={s.ticker} signal={s} />
                ))}
              </div>
            </Card>
          )}

          {/* Excluded tickers */}
          {excluded.length > 0 && (
            <Card title="Excluded" subtitle={`${excluded.length} ticker(s) failed one or more gates — expand to see why`}>
              <div className="space-y-0">
                {excluded.map((s) => (
                  <ExpandableSignalRow key={s.ticker} signal={s} />
                ))}
              </div>
            </Card>
          )}

          {/* No results at all */}
          {scanData.signals.length === 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
              <p className="text-sm font-medium text-amber-600">
                Scan completed — no tickers returned
              </p>
              <p className="mt-1 text-xs text-gray-400">
                {scanData.total_screened} ticker(s) were requested but none had
                data in the OHLCV store. Try updating the data store or
                checking ticker symbols.
              </p>
            </div>
          )}
        </>
      )}

      {/* Data Store — collapsible section at the bottom */}
      {storeReady && (
        <div className="rounded-lg border border-gray-200 bg-white">
          <button
            onClick={() => setShowDataStore(!showDataStore)}
            className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-gray-50"
          >
            <div className="flex items-center gap-2">
              <Database className="h-4 w-4 text-gray-400" />
              <span className="text-sm font-medium text-gray-700">Data Store</span>
              <span className="text-xs text-gray-400">
                {status?.ticker_count.toLocaleString()} tickers · Latest: {status?.latest_date ?? "—"}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  updateDaily.mutate();
                }}
                disabled={updateDaily.isPending}
                className="rounded px-2.5 py-1 text-xs font-medium text-blue-600 transition-colors hover:bg-blue-50 disabled:opacity-50"
              >
                {updateDaily.isPending ? "Updating..." : "Update Daily"}
              </button>
              {showDataStore ? (
                <ChevronDown className="h-4 w-4 text-gray-400" />
              ) : (
                <ChevronRight className="h-4 w-4 text-gray-400" />
              )}
            </div>
          </button>

          {showDataStore && (
            <div className="border-t border-gray-200 px-4 py-3">
              <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
                <DataStat label="Tickers" value={status?.ticker_count.toLocaleString() ?? "—"} />
                <DataStat label="Total Rows" value={status?.total_rows.toLocaleString() ?? "—"} />
                <DataStat label="Earliest" value={status?.earliest_date ?? "—"} />
                <DataStat label="Latest" value={status?.latest_date ?? "—"} />
              </div>
              {updateDaily.isError && (
                <p className="mt-2 text-sm text-red-600">
                  Update failed: {updateDaily.error.message}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ExpandableSignalRow({ signal: s }: { signal: ConvictionSignal }) {
  const [expanded, setExpanded] = useState(false);

  const convictionColor: Record<string, string> = {
    high: "text-emerald-600",
    medium: "text-amber-600",
    low: "text-red-600",
    none: "text-gray-400",
  };

  const trendVariant: Record<string, "success" | "warning" | "danger" | "neutral" | "info"> = {
    strong_uptrend: "success",
    uptrend: "success",
    pullback_to_8ema: "warning",
    pullback_to_21ema: "warning",
    consolidation: "neutral",
    downtrend: "danger",
    insufficient_data: "neutral",
  };

  const failedGate = s.gate_results?.find((g) => !g.passed && g.actual !== "—");
  const Chevron = expanded ? ChevronDown : ChevronRight;

  return (
    <div className="border-b border-gray-100 last:border-b-0">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-4 px-1 py-3 text-left text-sm transition-colors hover:bg-gray-50"
      >
        <Chevron className="h-4 w-4 shrink-0 text-gray-400" />

        <span className="w-16 font-semibold text-gray-900">{s.ticker}</span>

        <span className="w-36">
          <StatusBadge
            label={s.trend_state.replace(/_/g, " ")}
            variant={trendVariant[s.trend_state] ?? "neutral"}
          />
        </span>

        <span className={`w-16 font-medium ${convictionColor[s.conviction_level] ?? "text-gray-400"}`}>
          {s.conviction_level}
        </span>

        {s.last_close > 0 ? (
          <>
            <span className="w-20 text-right font-mono text-gray-700">${s.last_close.toFixed(2)}</span>
            <span className="w-20 text-right font-mono text-xs text-gray-400">
              {s.price_to_8ema_pct >= 0 ? "+" : ""}{s.price_to_8ema_pct.toFixed(2)}%
            </span>
            <span className="w-14 text-right text-gray-700">{s.days_above_both_emas}d</span>
          </>
        ) : (
          <>
            <span className="w-20 text-right text-gray-300">—</span>
            <span className="w-20 text-right text-gray-300">—</span>
            <span className="w-14 text-right text-gray-300">—</span>
          </>
        )}

        <span className="ml-auto flex items-center gap-2">
          {s.csp_eligible ? (
            <span className="flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-600 border border-emerald-200">
              <Check className="h-3 w-3" /> Eligible
            </span>
          ) : failedGate ? (
            <span className="text-xs text-gray-400 truncate max-w-48">
              Failed: {failedGate.gate}
            </span>
          ) : (
            <span className="text-xs text-gray-300">—</span>
          )}
        </span>
      </button>

      {expanded && (
        <div className="ml-5 mb-4 mr-1">
          <GateLadder
            gates={s.gate_results ?? []}
            eligible={s.csp_eligible}
            ticker={s.ticker}
            signal={s}
          />
        </div>
      )}
    </div>
  );
}

function GateLadder({
  gates,
  eligible,
  ticker,
  signal,
}: {
  gates: GateResult[];
  eligible: boolean;
  ticker: string;
  signal: ConvictionSignal;
}) {
  return (
    <div className="flex gap-6">
      <div className="flex-1 rounded-lg border border-gray-200 bg-gray-50 p-4">
        <div className="mb-3 flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-900">{ticker}</span>
          {eligible ? (
            <span className="rounded-full bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-[10px] font-medium text-emerald-600">
              CSP ELIGIBLE
            </span>
          ) : (
            <span className="rounded-full bg-red-50 border border-red-200 px-2 py-0.5 text-[10px] font-medium text-red-600">
              EXCLUDED
            </span>
          )}
        </div>

        <div className="relative">
          {gates.map((gate, i) => {
            const isLast = i === gates.length - 1;
            const isSkipped = gate.actual === "—";
            return (
              <div key={gate.gate} className="relative flex gap-3">
                {!isLast && (
                  <div className="absolute left-[11px] top-[24px] bottom-0 w-px bg-gray-200" />
                )}

                <div className="relative z-10 mt-0.5 shrink-0">
                  {gate.passed ? (
                    <div className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 border border-emerald-300">
                      <Check className="h-3.5 w-3.5 text-emerald-600" />
                    </div>
                  ) : isSkipped ? (
                    <div className="flex h-6 w-6 items-center justify-center rounded-full bg-gray-100 border border-gray-300">
                      <Minus className="h-3.5 w-3.5 text-gray-400" />
                    </div>
                  ) : (
                    <div className="flex h-6 w-6 items-center justify-center rounded-full bg-red-100 border border-red-300">
                      <X className="h-3.5 w-3.5 text-red-600" />
                    </div>
                  )}
                </div>

                <div className={`pb-5 ${isLast ? "pb-0" : ""}`}>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900">{gate.gate}</span>
                    {gate.passed ? (
                      <span className="text-[10px] font-medium text-emerald-600">PASS</span>
                    ) : isSkipped ? (
                      <span className="text-[10px] font-medium text-gray-400">SKIPPED</span>
                    ) : (
                      <span className="text-[10px] font-medium text-red-600">FAIL</span>
                    )}
                  </div>
                  <p className={`mt-0.5 text-xs ${isSkipped ? "text-gray-300" : "text-gray-500"}`}>
                    {gate.reason}
                  </p>
                  {!isSkipped && (
                    <div className="mt-1 flex gap-4 text-[11px]">
                      <span className="text-gray-400">
                        Actual: <span className="font-mono text-gray-600">{gate.actual}</span>
                      </span>
                      <span className="text-gray-400">
                        Required: <span className="font-mono text-gray-600">{gate.threshold}</span>
                      </span>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {signal.last_close > 0 && (
        <div className="w-52 shrink-0 rounded-lg border border-gray-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-400">
            Metrics
          </h4>
          <div className="space-y-2.5 text-sm">
            <MetricRow label="Close" value={`$${signal.last_close.toFixed(2)}`} />
            <MetricRow label="EMA 8" value={`$${signal.ema_8.toFixed(2)}`} />
            <MetricRow label="EMA 21" value={`$${signal.ema_21.toFixed(2)}`} />
            <MetricRow label="8-EMA Dist" value={`${signal.price_to_8ema_pct.toFixed(2)}%`} />
            <MetricRow label="Days Above" value={`${signal.days_above_both_emas}d`} />
            <MetricRow label="Volume" value={signal.latest_volume.toLocaleString()} />
            <MetricRow label="Avg Vol 20d" value={signal.avg_volume_20d.toLocaleString()} />
          </div>
        </div>
      )}
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-gray-400">{label}</span>
      <span className="font-mono text-gray-900">{value}</span>
    </div>
  );
}

function DataStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-gray-400">{label}</p>
      <p className="mt-0.5 text-sm font-semibold text-gray-900">{value}</p>
    </div>
  );
}
