import { useState } from "react";
import { Card } from "@/components/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { DataTable, type DataTableColumn } from "@/components/DataTable";
import {
  useDataStoreStatus,
  useBootstrapData,
  useUpdateDailyData,
  useConvictionScan,
  useTriggerConvictionScan,
} from "@/hooks/useApi";
import type { ConvictionSignal } from "@/types";
import { ChevronDown, ChevronRight, Check, X, Minus, Database } from "lucide-react";
import { convictionSortValue } from "@/lib/format";

const TREND_VARIANT: Record<string, "success" | "warning" | "danger" | "neutral" | "info"> = {
  strong_uptrend: "success",
  uptrend: "success",
  pullback_to_8ema: "warning",
  pullback_to_21ema: "warning",
  consolidation: "neutral",
  downtrend: "danger",
  insufficient_data: "neutral",
};

const signalColumns: DataTableColumn<ConvictionSignal>[] = [
  {
    key: "ticker",
    header: "Ticker",
    accessor: (r) => r.ticker,
    sortable: true,
    width: "90px",
    render: (r) => (
      <span className="font-mono font-bold text-gray-900">{r.ticker}</span>
    ),
  },
  {
    key: "trend_state",
    header: "Trend",
    accessor: (r) => r.trend_state,
    sortable: true,
    render: (r) => (
      <StatusBadge
        label={r.trend_state.replace(/_/g, " ")}
        variant={TREND_VARIANT[r.trend_state] ?? "neutral"}
      />
    ),
  },
  {
    key: "conviction_level",
    header: "Conviction",
    accessor: (r) => convictionSortValue(r.conviction_level),
    sortable: true,
    render: (r) => {
      const colors: Record<string, string> = {
        high: "text-emerald-600",
        medium: "text-amber-600",
        low: "text-red-600",
        none: "text-gray-400",
      };
      return (
        <span className={`font-medium ${colors[r.conviction_level] ?? "text-gray-400"}`}>
          {r.conviction_level}
        </span>
      );
    },
  },
  {
    key: "last_close",
    header: "Price",
    accessor: (r) => r.last_close,
    sortable: true,
    align: "right",
    render: (r) =>
      r.last_close > 0 ? (
        <span className="font-mono text-gray-700">${r.last_close.toFixed(2)}</span>
      ) : (
        <span className="text-gray-300">—</span>
      ),
  },
  {
    key: "price_to_8ema_pct",
    header: "% to 8-EMA",
    accessor: (r) => r.price_to_8ema_pct,
    sortable: true,
    align: "right",
    render: (r) =>
      r.last_close > 0 ? (
        <span className="font-mono text-xs text-gray-400">
          {r.price_to_8ema_pct >= 0 ? "+" : ""}
          {r.price_to_8ema_pct.toFixed(2)}%
        </span>
      ) : (
        <span className="text-gray-300">—</span>
      ),
  },
  {
    key: "days_above_both_emas",
    header: "Days Above",
    accessor: (r) => r.days_above_both_emas,
    sortable: true,
    align: "right",
    render: (r) =>
      r.last_close > 0 ? (
        <span className="text-gray-700">{r.days_above_both_emas}d</span>
      ) : (
        <span className="text-gray-300">—</span>
      ),
  },
  {
    key: "csp_eligible",
    header: "CSP",
    accessor: (r) => (r.csp_eligible ? 1 : 0),
    sortable: true,
    render: (r) => {
      if (r.csp_eligible) {
        return (
          <span className="flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-600 border border-emerald-200">
            <Check className="h-3 w-3" /> Eligible
          </span>
        );
      }
      const failedGate = r.gate_results?.find((g) => !g.passed && g.actual !== "—");
      if (failedGate) {
        return (
          <span className="text-xs text-gray-400 truncate max-w-48">
            Failed: {failedGate.gate}
          </span>
        );
      }
      return <span className="text-xs text-gray-300">—</span>;
    },
  },
];

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

      {scanData && !scanLoading && (
        <>
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

          {eligible.length > 0 && (
            <Card title="CSP Eligible" subtitle={`${eligible.length} ticker(s) passed all gates`}>
              <DataTable
                data={eligible}
                columns={signalColumns}
                searchField={(r) => r.ticker}
                rowKey={(r) => r.ticker}
                defaultSortKey="conviction_level"
                defaultSortDir="desc"
                defaultPageSize={15}
                expandedRow={(r) => <SignalDetail signal={r} />}
              />
            </Card>
          )}

          {excluded.length > 0 && (
            <Card title="Excluded" subtitle={`${excluded.length} ticker(s) failed one or more gates — expand to see why`}>
              <DataTable
                data={excluded}
                columns={signalColumns}
                searchField={(r) => r.ticker}
                rowKey={(r) => r.ticker}
                defaultSortKey="conviction_level"
                defaultSortDir="desc"
                defaultPageSize={15}
                expandedRow={(r) => <SignalDetail signal={r} />}
              />
            </Card>
          )}

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

function SignalDetail({ signal: s }: { signal: ConvictionSignal }) {
  return (
    <div className="flex gap-6">
      <div className="flex-1 rounded-lg border border-gray-200 bg-gray-50 p-4">
        <div className="mb-3 flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-900">{s.ticker}</span>
          {s.csp_eligible ? (
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
          {(s.gate_results ?? []).map((gate, i) => {
            const isLast = i === (s.gate_results?.length ?? 0) - 1;
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

      {s.last_close > 0 && (
        <div className="w-52 shrink-0 rounded-lg border border-gray-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-400">
            Metrics
          </h4>
          <div className="space-y-2.5 text-sm">
            <MetricRow label="Close" value={`$${s.last_close.toFixed(2)}`} />
            <MetricRow label="EMA 8" value={`$${s.ema_8.toFixed(2)}`} />
            <MetricRow label="EMA 21" value={`$${s.ema_21.toFixed(2)}`} />
            <MetricRow label="8-EMA Dist" value={`${s.price_to_8ema_pct.toFixed(2)}%`} />
            <MetricRow label="Days Above" value={`${s.days_above_both_emas}d`} />
            <MetricRow label="Volume" value={s.latest_volume.toLocaleString()} />
            <MetricRow label="Avg Vol 20d" value={s.avg_volume_20d.toLocaleString()} />
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
