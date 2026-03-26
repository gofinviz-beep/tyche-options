import { useState } from "react";
import { Card } from "@/components/Card";
import { StatusBadge } from "@/components/StatusBadge";
import {
  useDataStoreStatus,
  useBootstrapData,
  useUpdateDailyData,
  useTriggerConvictionScan,
} from "@/hooks/useApi";
import type { ConvictionSignal } from "@/types";

export function Conviction() {
  const { data: status, isLoading: statusLoading } = useDataStoreStatus();
  const bootstrap = useBootstrapData();
  const updateDaily = useUpdateDailyData();
  const scan = useTriggerConvictionScan();
  const [symbols, setSymbols] = useState("");

  const handleScan = () => {
    scan.mutate(symbols || undefined);
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Conviction Engine</h1>

      {/* Data Store Status */}
      <Card
        title="OHLCV Data Store"
        subtitle={statusLoading ? "Loading..." : status?.exists ? "Active" : "Not initialized"}
      >
        {status ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
              <DataStat label="Tickers" value={status.ticker_count.toLocaleString()} />
              <DataStat label="Total Rows" value={status.total_rows.toLocaleString()} />
              <DataStat label="Earliest" value={status.earliest_date ?? "—"} />
              <DataStat label="Latest" value={status.latest_date ?? "—"} />
            </div>

            <div className="flex gap-3">
              {!status.exists && (
                <button
                  onClick={() => bootstrap.mutate(120)}
                  disabled={bootstrap.isPending}
                  className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
                >
                  {bootstrap.isPending ? "Bootstrapping..." : "Bootstrap 120 Days"}
                </button>
              )}
              {status.exists && (
                <button
                  onClick={() => updateDaily.mutate()}
                  disabled={updateDaily.isPending}
                  className="rounded-lg bg-gray-800 px-4 py-2 text-sm font-medium text-gray-300 transition-colors hover:bg-gray-700 disabled:opacity-50"
                >
                  {updateDaily.isPending ? "Updating..." : "Update Daily"}
                </button>
              )}
            </div>

            {bootstrap.isError && (
              <p className="text-sm text-red-400">
                Bootstrap failed: {bootstrap.error.message}
              </p>
            )}
            {updateDaily.isError && (
              <p className="text-sm text-red-400">
                Update failed: {updateDaily.error.message}
              </p>
            )}
            {bootstrap.isSuccess && (
              <p className="text-sm text-emerald-400">
                Bootstrap complete. Data store is ready.
              </p>
            )}
          </div>
        ) : statusLoading ? (
          <div className="flex h-16 items-center justify-center">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-gray-600 border-t-white" />
          </div>
        ) : null}
      </Card>

      {/* Conviction Scan */}
      <Card title="EMA Conviction Scan" subtitle="8/21 EMA trend analysis">
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <input
              type="text"
              placeholder="AAPL,PL,NVDA... (blank = watchlist)"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              className="flex-1 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:border-blue-500 focus:outline-none"
            />
            <button
              onClick={handleScan}
              disabled={scan.isPending || !status?.exists}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
            >
              {scan.isPending ? "Scanning..." : "Run Scan"}
            </button>
          </div>

          {!status?.exists && (
            <p className="text-sm text-amber-400">
              Bootstrap the data store first to run conviction scans.
            </p>
          )}

          {scan.isError && (
            <p className="text-sm text-red-400">
              Scan failed: {scan.error.message}
            </p>
          )}

          {scan.data && (
            <div className="space-y-3">
              <div className="flex gap-4 text-sm">
                <span className="text-gray-500">
                  Screened: {scan.data.total_screened}
                </span>
                <span className="text-emerald-400">
                  Eligible: {scan.data.eligible_count}
                </span>
              </div>

              {scan.data.signals.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-gray-800 text-xs text-gray-500">
                        <th className="pb-2 pr-3">Ticker</th>
                        <th className="pb-2 pr-3">Trend</th>
                        <th className="pb-2 pr-3">Conviction</th>
                        <th className="pb-2 pr-3 text-right">Close</th>
                        <th className="pb-2 pr-3 text-right">EMA 8</th>
                        <th className="pb-2 pr-3 text-right">EMA 21</th>
                        <th className="pb-2 pr-3 text-right">8-EMA Dist</th>
                        <th className="pb-2 pr-3 text-right">Days Above</th>
                        <th className="pb-2 text-center">CSP</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scan.data.signals.map((s: ConvictionSignal) => (
                        <SignalRow key={s.ticker} signal={s} />
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-sm text-gray-500">
                  No signals from this scan.
                </p>
              )}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

function SignalRow({ signal: s }: { signal: ConvictionSignal }) {
  const convictionColor: Record<string, string> = {
    high: "text-emerald-400",
    medium: "text-amber-400",
    low: "text-red-400",
    none: "text-gray-500",
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

  return (
    <tr className="border-b border-gray-800/50 text-gray-300 hover:bg-gray-800/30">
      <td className="py-2.5 pr-3 font-semibold text-white">{s.ticker}</td>
      <td className="py-2.5 pr-3">
        <StatusBadge
          label={s.trend_state.replace(/_/g, " ")}
          variant={trendVariant[s.trend_state] ?? "neutral"}
        />
      </td>
      <td className={`py-2.5 pr-3 font-medium ${convictionColor[s.conviction_level] ?? "text-gray-500"}`}>
        {s.conviction_level}
      </td>
      <td className="py-2.5 pr-3 text-right font-mono">
        ${s.last_close.toFixed(2)}
      </td>
      <td className="py-2.5 pr-3 text-right font-mono">
        ${s.ema_8.toFixed(2)}
      </td>
      <td className="py-2.5 pr-3 text-right font-mono">
        ${s.ema_21.toFixed(2)}
      </td>
      <td className="py-2.5 pr-3 text-right font-mono text-xs">
        {s.price_to_8ema_pct.toFixed(2)}%
      </td>
      <td className="py-2.5 pr-3 text-right">{s.days_above_both_emas}d</td>
      <td className="py-2.5 text-center">
        {s.csp_eligible ? (
          <span className="text-emerald-400">Yes</span>
        ) : (
          <span className="text-gray-600">—</span>
        )}
      </td>
    </tr>
  );
}

function DataStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-gray-500">{label}</p>
      <p className="mt-0.5 text-sm font-semibold text-white">{value}</p>
    </div>
  );
}
