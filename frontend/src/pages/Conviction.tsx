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
import { ChevronDown, ChevronRight, Check, X, Minus, Database, Star } from "lucide-react";
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

function formatMarketCap(cap: number | null): string {
  if (cap == null || cap <= 0) return "—";
  if (cap >= 1e12) return `$${(cap / 1e12).toFixed(1)}T`;
  if (cap >= 1e9) return `$${(cap / 1e9).toFixed(1)}B`;
  if (cap >= 1e6) return `$${(cap / 1e6).toFixed(0)}M`;
  return `$${cap.toLocaleString()}`;
}

function formatInstPct(pct: number | null): string {
  if (pct == null) return "—";
  return `${(pct * 100).toFixed(0)}%`;
}

const signalColumns: DataTableColumn<ConvictionSignal>[] = [
  {
    key: "ticker",
    header: "Ticker",
    accessor: (r) => r.ticker,
    sortable: true,
    width: "90px",
    render: (r) => (
      <span className="flex items-center gap-1 font-mono font-bold text-gray-900">
        {r.ticker}
        {r.is_watchlist && <Star className="h-3 w-3 fill-amber-400 text-amber-400" />}
      </span>
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
    filter: {
      type: "select",
      placeholder: "All",
      options: [
        { value: "strong_uptrend", label: "Strong Uptrend" },
        { value: "uptrend", label: "Uptrend" },
        { value: "pullback_to_8ema", label: "Pullback 8-EMA" },
        { value: "pullback_to_21ema", label: "Pullback 21-EMA" },
        { value: "consolidation", label: "Consolidation" },
        { value: "downtrend", label: "Downtrend" },
      ],
    },
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
    filter: {
      type: "select",
      placeholder: "All",
      options: [
        { value: "3", label: "High" },
        { value: "2", label: "Medium" },
        { value: "1", label: "Low" },
        { value: "0", label: "None" },
      ],
    },
  },
  {
    key: "conviction_score",
    header: "Score",
    accessor: (r) => r.conviction_score ?? 0,
    sortable: true,
    align: "right",
    render: (r) => {
      const s = r.conviction_score ?? 0;
      const pct = Math.round(s * 100);
      const color =
        pct >= 70
          ? "text-emerald-600"
          : pct >= 40
            ? "text-amber-600"
            : "text-gray-400";
      return (
        <div className="flex items-center gap-1.5 justify-end">
          <div className="h-1.5 w-12 rounded-full bg-gray-100 overflow-hidden">
            <div
              className={`h-full rounded-full ${pct >= 70 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-400" : "bg-gray-300"}`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className={`font-mono text-xs ${color}`}>{pct}</span>
        </div>
      );
    },
    filter: { type: "min", placeholder: "Min" },
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
    key: "market_cap",
    header: "Mkt Cap",
    accessor: (r) => r.market_cap ?? 0,
    sortable: true,
    align: "right",
    render: (r) => (
      <span className="font-mono text-xs text-gray-500">
        {formatMarketCap(r.market_cap)}
      </span>
    ),
    filter: {
      type: "min",
      placeholder: "All",
      options: [
        { value: "4e9", label: "$4B" },
        { value: "10e9", label: "$10B" },
        { value: "50e9", label: "$50B" },
        { value: "100e9", label: "$100B" },
        { value: "200e9", label: "$200B" },
      ],
    },
  },
  {
    key: "institutional_pct",
    header: "Inst %",
    accessor: (r) => r.institutional_pct ?? -1,
    sortable: true,
    align: "right",
    render: (r) => {
      if (r.institutional_pct == null) return <span className="text-xs text-gray-300">—</span>;
      const pct = r.institutional_pct * 100;
      const color = pct >= 60 ? "text-emerald-600" : pct >= 40 ? "text-amber-600" : "text-red-500";
      return <span className={`font-mono text-xs ${color}`}>{pct.toFixed(0)}%</span>;
    },
    filter: {
      type: "min",
      placeholder: "All",
      options: [
        { value: "0.4", label: "40%" },
        { value: "0.5", label: "50%" },
        { value: "0.6", label: "60%" },
        { value: "0.7", label: "70%" },
        { value: "0.8", label: "80%" },
      ],
    },
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
    key: "rsi_14",
    header: "RSI",
    accessor: (r) => r.rsi_14,
    sortable: true,
    align: "right",
    render: (r) => {
      if (r.last_close <= 0) return <span className="text-gray-300">—</span>;
      const color = r.rsi_14 < 30 ? "text-red-600" : r.rsi_14 < 40 ? "text-amber-600" : r.rsi_14 > 70 ? "text-purple-600" : "text-gray-700";
      return <span className={`font-mono text-xs ${color}`}>{r.rsi_14.toFixed(0)}</span>;
    },
    filter: {
      type: "range",
      minOptions: [
        { value: "30", label: "30" },
        { value: "40", label: "40" },
        { value: "50", label: "50" },
      ],
      maxOptions: [
        { value: "40", label: "40" },
        { value: "50", label: "50" },
        { value: "60", label: "60" },
        { value: "70", label: "70" },
      ],
    },
  },
  {
    key: "iv_rank",
    header: "IV Rank",
    accessor: (r) => r.iv_rank ?? -1,
    sortable: true,
    align: "right",
    render: (r) => {
      if (r.iv_rank == null) return <span className="text-gray-300">—</span>;
      const color = r.iv_rank < 20 ? "text-emerald-600" : r.iv_rank > 80 ? "text-red-600" : "text-gray-700";
      return <span className={`font-mono text-xs ${color}`}>{r.iv_rank.toFixed(0)}</span>;
    },
    filter: {
      type: "range",
      minOptions: [
        { value: "0", label: "0" },
        { value: "20", label: "20" },
        { value: "40", label: "40" },
        { value: "60", label: "60" },
        { value: "80", label: "80" },
      ],
      maxOptions: [
        { value: "20", label: "20" },
        { value: "40", label: "40" },
        { value: "60", label: "60" },
        { value: "80", label: "80" },
        { value: "100", label: "100" },
      ],
    },
  },
  {
    key: "vrp",
    header: "VRP",
    accessor: (r) => r.vrp ?? 0,
    sortable: true,
    align: "right",
    render: (r) => {
      if (r.vrp == null) return <span className="text-gray-300">—</span>;
      const color = r.vrp > 0 ? "text-emerald-600" : r.vrp < 0 ? "text-red-600" : "text-gray-700";
      return <span className={`font-mono text-xs ${color}`}>{(r.vrp * 100).toFixed(1)}%</span>;
    },
    filter: {
      type: "range",
      minOptions: [
        { value: "-0.2", label: "-20%" },
        { value: "-0.1", label: "-10%" },
        { value: "0", label: "0%" },
      ],
      maxOptions: [
        { value: "0.1", label: "10%" },
        { value: "0.2", label: "20%" },
        { value: "0.5", label: "50%" },
      ],
    },
  },
  {
    key: "ema_50_slope",
    header: "50-EMA",
    accessor: (r) => r.ema_50_slope,
    sortable: true,
    align: "right",
    render: (r) => {
      if (r.last_close <= 0) return <span className="text-gray-300">—</span>;
      const rising = r.ema_50_slope > 0;
      return (
        <span className={`font-mono text-xs ${rising ? "text-emerald-600" : "text-red-500"}`}>
          {rising ? "▲" : "▼"} {Math.abs(r.ema_50_slope).toFixed(2)}
        </span>
      );
    },
    filter: {
      type: "boolean",
      placeholder: "All",
      options: [
        { value: "true", label: "Rising ▲" },
        { value: "false", label: "Falling ▼" },
      ],
    },
  },
  {
    key: "days_above_both_emas",
    header: "Streak",
    accessor: (r) => r.trend_state.startsWith("pullback") ? r.prior_streak : r.days_above_both_emas,
    sortable: true,
    align: "right",
    render: (r) => {
      if (r.last_close <= 0) return <span className="text-gray-300">—</span>;
      if (r.trend_state.startsWith("pullback")) {
        return (
          <span className="font-mono text-xs text-blue-600" title="Prior streak before pullback">
            {r.prior_streak}d prior
          </span>
        );
      }
      return <span className="text-gray-700">{r.days_above_both_emas}d</span>;
    },
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

  const allSignals = scanData?.signals ?? [];

  const pullbackEligible = allSignals.filter(
    (s) => s.csp_eligible && s.trend_state.startsWith("pullback"),
  );
  const uptrendEligible = allSignals.filter(
    (s) => s.csp_eligible && !s.trend_state.startsWith("pullback"),
  );
  const pullbackNotEligible = allSignals.filter(
    (s) => !s.csp_eligible && s.trend_state.startsWith("pullback"),
  );
  const eligible = allSignals.filter((s) => s.csp_eligible);

  const storeReady = !!status?.exists;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Conviction Engine</h1>
        <p className="mt-1 text-sm text-gray-500">
          8/21 EMA trend analysis — pullback CSPs (Path B, 76.8% win rate) are
          shown first, followed by uptrend CSPs (Path A). Full universe scan.
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
            <div className={`rounded-lg border px-4 py-2 shadow-sm ${(scanData.pullback_eligible ?? 0) > 0 ? "border-blue-200 bg-blue-50" : "border-gray-200 bg-gray-50"}`}>
              <span className={(scanData.pullback_eligible ?? 0) > 0 ? "text-blue-600 font-semibold" : "text-gray-500"}>
                Pullback CSP: {scanData.pullback_eligible ?? pullbackEligible.length}
              </span>
            </div>
            <div className={`rounded-lg border px-4 py-2 shadow-sm ${(scanData.uptrend_eligible ?? 0) > 0 ? "border-emerald-200 bg-emerald-50" : "border-gray-200 bg-gray-50"}`}>
              <span className={(scanData.uptrend_eligible ?? 0) > 0 ? "text-emerald-600" : "text-gray-500"}>
                Uptrend CSP: {scanData.uptrend_eligible ?? uptrendEligible.length}
              </span>
            </div>
            {(scanData.pullback_count ?? 0) > 0 && (
              <div className="rounded-lg border border-blue-100 bg-blue-50/50 px-4 py-2 shadow-sm">
                <span className="text-blue-500">
                  Pullbacks Detected: {scanData.pullback_count}
                </span>
              </div>
            )}
          </div>

          {scanData.trend_summary && (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-7">
              <TrendPill label="Strong Up" count={scanData.trend_summary.strong_uptrend} color="emerald" />
              <TrendPill label="Uptrend" count={scanData.trend_summary.uptrend} color="green" />
              <TrendPill label="PB → 8EMA" count={scanData.trend_summary.pullback_to_8ema} color="blue" />
              <TrendPill label="PB → 21EMA" count={scanData.trend_summary.pullback_to_21ema} color="indigo" />
              <TrendPill label="Consolidation" count={scanData.trend_summary.consolidation} color="gray" />
              <TrendPill label="Downtrend" count={scanData.trend_summary.downtrend} color="red" />
              <TrendPill label="No Data" count={scanData.trend_summary.insufficient_data} color="gray" />
            </div>
          )}

          {pullbackEligible.length > 0 && (
            <Card
              title="Pullback CSP Eligible (Path B)"
              subtitle={`${pullbackEligible.length} ticker(s) pulling back to EMA support with confirmed prior uptrend — highest win rate`}
            >
              <DataTable
                data={pullbackEligible}
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

          {pullbackNotEligible.length > 0 && (
            <Card
              title="Pullbacks Forming"
              subtitle={`${pullbackNotEligible.length} ticker(s) pulling back to EMA but not yet CSP-eligible — expand to see why`}
            >
              <DataTable
                data={pullbackNotEligible}
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

          {uptrendEligible.length > 0 && (
            <Card
              title="Uptrend CSP Eligible (Path A)"
              subtitle={`${uptrendEligible.length} ticker(s) above both EMAs in the sweet spot`}
            >
              <DataTable
                data={uptrendEligible}
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

          {eligible.length === 0 && pullbackNotEligible.length === 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
              <p className="text-sm font-medium text-amber-600">
                No CSP-eligible tickers found
              </p>
              <p className="mt-1 text-xs text-gray-400">
                {scanData.total_screened} ticker(s) screened.
                {(scanData.pullback_count ?? 0) === 0
                  ? " No stocks are currently pulling back to their EMAs in the universe."
                  : ` ${scanData.pullback_count} pullback(s) detected but none met the prior streak or slope requirements.`}
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
        <div className="w-56 shrink-0 rounded-lg border border-gray-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-400">
            Metrics
          </h4>
          <div className="space-y-2.5 text-sm">
            <MetricRow label="Close" value={`$${s.last_close.toFixed(2)}`} />
            <MetricRow label="EMA 8" value={`$${s.ema_8.toFixed(2)}`} />
            <MetricRow label="EMA 21" value={`$${s.ema_21.toFixed(2)}`} />
            <MetricRow label="8-EMA Dist" value={`${s.price_to_8ema_pct.toFixed(2)}%`} />
            <MetricRow label="21-EMA Dist" value={`${s.price_to_21ema_pct.toFixed(2)}%`} />
            {s.trend_state.startsWith("pullback") ? (
              <MetricRow label="Prior Streak" value={`${s.prior_streak}d`} />
            ) : (
              <MetricRow label="Days Above" value={`${s.days_above_both_emas}d`} />
            )}
            <MetricRow label="EMA 50" value={`$${s.ema_50.toFixed(2)}`} />
            <MetricRow label="50-EMA Slope" value={`${s.ema_50_slope > 0 ? "▲" : "▼"} ${Math.abs(s.ema_50_slope).toFixed(4)}`} />
            <MetricRow label="RSI (14)" value={s.rsi_14.toFixed(1)} />
            <MetricRow label="ATM IV" value={s.atm_iv != null ? `${(s.atm_iv * 100).toFixed(1)}%` : "—"} />
            <MetricRow label="IV Rank" value={s.iv_rank != null ? s.iv_rank.toFixed(1) : "—"} />
            <MetricRow label="IV Percentile" value={s.iv_percentile != null ? s.iv_percentile.toFixed(1) : "—"} />
            <MetricRow label="VRP" value={s.vrp != null ? `${(s.vrp * 100).toFixed(1)}%` : "—"} />
            <MetricRow label="Mkt Cap" value={formatMarketCap(s.market_cap)} />
            <MetricRow label="Inst. Own" value={formatInstPct(s.institutional_pct)} />
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

const PILL_COLORS: Record<string, string> = {
  emerald: "bg-emerald-50 text-emerald-700 border-emerald-200",
  green: "bg-green-50 text-green-700 border-green-200",
  blue: "bg-blue-50 text-blue-700 border-blue-200",
  indigo: "bg-indigo-50 text-indigo-700 border-indigo-200",
  gray: "bg-gray-50 text-gray-500 border-gray-200",
  red: "bg-red-50 text-red-600 border-red-200",
};

function TrendPill({ label, count, color }: { label: string; count: number; color: string }) {
  return (
    <div className={`rounded-lg border px-3 py-1.5 text-center text-xs ${PILL_COLORS[color] ?? PILL_COLORS.gray}`}>
      <div className="font-semibold">{count}</div>
      <div className="text-[10px] opacity-70">{label}</div>
    </div>
  );
}
