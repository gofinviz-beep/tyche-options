import { useState } from "react";
import { Card } from "@/components/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { DataTable, type DataTableColumn } from "@/components/DataTable";
import {
  useConvictionSnapshots,
  useRefreshConviction,
  useTickerGates,
  useBacktestProfile,
} from "@/hooks/useApi";
import type { ConvictionSnapshot, GateResult, BacktestProfile } from "@/types";
import { Check, X, Minus } from "lucide-react";
import { convictionSortValue, formatMarketCap } from "@/lib/format";

const TREND_VARIANT: Record<
  string,
  "success" | "warning" | "danger" | "neutral" | "info"
> = {
  strong_uptrend: "success",
  uptrend: "success",
  pullback_to_8ema: "warning",
  pullback_to_21ema: "danger",
  consolidation: "neutral",
  downtrend: "danger",
  insufficient_data: "neutral",
};

const CONVICTION_VARIANT: Record<
  string,
  "success" | "warning" | "neutral" | "danger"
> = {
  high: "success",
  medium: "warning",
  low: "neutral",
  none: "danger",
};

const snapshotColumns: DataTableColumn<ConvictionSnapshot>[] = [
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
    header: "Trend State",
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
    key: "raw_conviction",
    header: "Conviction",
    accessor: (r) => convictionSortValue(r.raw_conviction ?? r.conviction_level),
    sortable: true,
    render: (r) => {
      const conv = r.raw_conviction ?? r.conviction_level;
      return (
        <StatusBadge
          label={conv}
          variant={CONVICTION_VARIANT[conv] ?? "neutral"}
        />
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
    key: "last_close",
    header: "Price",
    accessor: (r) => r.last_close,
    sortable: true,
    align: "right",
    render: (r) =>
      r.last_close > 0 ? (
        <span className="font-mono">${r.last_close.toFixed(2)}</span>
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
        {r.market_cap ? formatMarketCap(r.market_cap) : "—"}
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
      if (r.institutional_pct == null)
        return <span className="text-xs text-gray-300">—</span>;
      const pct = r.institutional_pct * 100;
      const color =
        pct >= 60
          ? "text-emerald-600"
          : pct >= 40
            ? "text-amber-600"
            : "text-red-500";
      return (
        <span className={`font-mono text-xs ${color}`}>{pct.toFixed(0)}%</span>
      );
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
        <span className="font-mono text-xs text-gray-600">
          {r.price_to_8ema_pct >= 0 ? "+" : ""}
          {r.price_to_8ema_pct.toFixed(2)}%
        </span>
      ) : (
        <span className="text-gray-300">—</span>
      ),
  },
  {
    key: "price_to_21ema_pct",
    header: "% to 21-EMA",
    accessor: (r) => r.price_to_21ema_pct,
    sortable: true,
    align: "right",
    render: (r) =>
      r.last_close > 0 ? (
        <span className="font-mono text-xs text-gray-600">
          {r.price_to_21ema_pct >= 0 ? "+" : ""}
          {r.price_to_21ema_pct.toFixed(2)}%
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
    header: "Days Above",
    accessor: (r) => r.days_above_both_emas,
    sortable: true,
    align: "right",
    render: (r) => (
      <span className="text-gray-600">{r.days_above_both_emas}d</span>
    ),
  },
  {
    key: "volume_declining",
    header: "Vol ↓",
    accessor: (r) => r.volume_declining,
    sortable: true,
    render: (r) => (
      <span
        className={r.volume_declining ? "text-emerald-600" : "text-gray-400"}
      >
        {r.volume_declining ? "Yes" : "No"}
      </span>
    ),
    filter: {
      type: "boolean",
      placeholder: "All",
      options: [
        { value: "true", label: "Yes" },
        { value: "false", label: "No" },
      ],
    },
  },
  {
    key: "pullback_entry",
    header: "Pullback Entry",
    accessor: (r) => {
      const isPullback = r.trend_state.startsWith("pullback_to_");
      return isPullback ? 1 : 0;
    },
    sortable: true,
    render: (r) => {
      const isPullback = r.trend_state.startsWith("pullback_to_");
      return isPullback ? (
        <StatusBadge label="Entry Zone" variant="success" />
      ) : (
        <span className="text-gray-300">—</span>
      );
    },
  },
];

export function StocksConviction() {
  const { data: snapshots, isLoading, error } = useConvictionSnapshots();
  const refreshMutation = useRefreshConviction();
  const [refreshResult, setRefreshResult] = useState<string | null>(null);

  const handleRefresh = () => {
    setRefreshResult(null);
    refreshMutation.mutate(undefined, {
      onSuccess: (result) => {
        setRefreshResult(
          `Batch complete: ${result.signals_computed} signals, ` +
            `${result.snapshots_upserted} snapshots, ` +
            `${result.transitions_detected} transitions ` +
            `(${result.new_pullback_transitions} new pullbacks), ` +
            `${result.duration_ms.toFixed(0)}ms`,
        );
      },
      onError: (err) => {
        setRefreshResult(`Error: ${(err as Error).message}`);
      },
    });
  };

  const allSnapshots = snapshots ?? [];

  const pullbacks = allSnapshots.filter((s) =>
    s.trend_state.startsWith("pullback_to_"),
  );
  const others = allSnapshots.filter(
    (s) => !s.trend_state.startsWith("pullback_to_"),
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Stocks — Conviction Engine
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            8/21 EMA trend analysis from a stock buying perspective — identify
            pullback entry zones for direct stock purchases. Expand any row to
            see the full gate ladder.
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshMutation.isPending}
          className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
        >
          {refreshMutation.isPending ? "Refreshing..." : "Refresh Conviction"}
        </button>
      </div>

      {refreshResult && (
        <div
          className={`rounded-lg border p-3 text-sm ${
            refreshResult.startsWith("Error")
              ? "border-red-200 bg-red-50 text-red-700"
              : "border-green-200 bg-green-50 text-green-700"
          }`}
        >
          {refreshResult}
        </div>
      )}

      {isLoading && (
        <div className="flex h-24 items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
        </div>
      )}

      {error && (
        <Card title="Error">
          <p className="text-sm text-red-600">{(error as Error).message}</p>
        </Card>
      )}

      {!isLoading && !error && (
        <>
          <div className="flex flex-wrap gap-3 text-sm">
            <div className="rounded-lg border border-gray-200 bg-white px-4 py-2 shadow-sm">
              <span className="text-gray-400">Total: </span>
              <span className="font-semibold text-gray-900">
                {allSnapshots.length}
              </span>
            </div>
            <div
              className={`rounded-lg border px-4 py-2 shadow-sm ${
                pullbacks.length > 0
                  ? "border-emerald-200 bg-emerald-50"
                  : "border-gray-200 bg-gray-50"
              }`}
            >
              <span
                className={
                  pullbacks.length > 0 ? "text-emerald-600" : "text-gray-500"
                }
              >
                Pullback Entries: {pullbacks.length}
              </span>
            </div>
          </div>

          {pullbacks.length > 0 && (
            <Card
              title="Pullback Entry Zones"
              subtitle={`${pullbacks.length} ticker(s) in pullback state — potential stock buy entries`}
            >
              <DataTable
                data={pullbacks}
                columns={snapshotColumns}
                searchField={(r) => r.ticker}
                rowKey={(r) => r.ticker}
                defaultSortKey="raw_conviction"
                defaultSortDir="desc"
                defaultPageSize={15}
                expandedRow={(r) => <SnapshotDetail snapshot={r} />}
              />
            </Card>
          )}

          {others.length > 0 && (
            <Card
              title="Other States"
              subtitle={`${others.length} ticker(s) — not currently in pullback`}
            >
              <DataTable
                data={others}
                columns={snapshotColumns}
                searchField={(r) => r.ticker}
                rowKey={(r) => r.ticker}
                defaultSortKey="raw_conviction"
                defaultSortDir="desc"
                defaultPageSize={15}
                expandedRow={(r) => <SnapshotDetail snapshot={r} />}
              />
            </Card>
          )}

          {allSnapshots.length === 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
              <p className="text-sm font-medium text-amber-600">
                No conviction snapshots found
              </p>
              <p className="mt-1 text-xs text-gray-400">
                Run &quot;Refresh Conviction&quot; to compute and persist
                snapshots for the full universe.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function LazyGateDetail({ ticker }: { ticker: string }) {
  const { data, isLoading } = useTickerGates(ticker);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-2 text-xs text-gray-400">
        <div className="h-3 w-3 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
        Loading gates…
      </div>
    );
  }

  if (!data || data.error) {
    return (
      <p className="py-2 text-xs text-gray-400">
        {data?.error ?? "Gate data unavailable"}
      </p>
    );
  }

  const gates: GateResult[] = data.gate_results;
  if (gates.length === 0) return null;

  return (
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
                <span className="text-sm font-medium text-gray-900">
                  {gate.gate}
                </span>
                {gate.passed ? (
                  <span className="text-[10px] font-medium text-emerald-600">
                    PASS
                  </span>
                ) : isSkipped ? (
                  <span className="text-[10px] font-medium text-gray-400">
                    SKIPPED
                  </span>
                ) : (
                  <span className="text-[10px] font-medium text-red-600">
                    FAIL
                  </span>
                )}
              </div>
              <p
                className={`mt-0.5 text-xs ${isSkipped ? "text-gray-300" : "text-gray-500"}`}
              >
                {gate.reason}
              </p>
              {!isSkipped && (
                <div className="mt-1 flex gap-4 text-[11px]">
                  <span className="text-gray-400">
                    Actual:{" "}
                    <span className="font-mono text-gray-600">
                      {gate.actual}
                    </span>
                  </span>
                  <span className="text-gray-400">
                    Required:{" "}
                    <span className="font-mono text-gray-600">
                      {gate.threshold}
                    </span>
                  </span>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function BounceProfileCard({ profile }: { profile: BacktestProfile }) {
  return (
    <div className="rounded border border-indigo-100 bg-indigo-50 p-3">
      <h5 className="text-xs font-semibold text-indigo-700 uppercase mb-2">
        {profile.pullback_type.toUpperCase()} Pullback History &middot;{" "}
        {profile.event_count} events
      </h5>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <MetricRow
          label="Med. bounce"
          value={`${profile.median_peak_gain_pct.toFixed(1)}%`}
        />
        <MetricRow
          label="Exit target (p75)"
          value={`${profile.p75_peak_gain_pct.toFixed(1)}%`}
        />
        <MetricRow
          label="Win ≥5%"
          value={`${(profile.win_rate_5pct * 100).toFixed(0)}%`}
        />
        <MetricRow
          label="Win ≥10%"
          value={`${(profile.win_rate_10pct * 100).toFixed(0)}%`}
        />
        <MetricRow
          label="Med. exit gain"
          value={`${profile.median_exit_gain_pct.toFixed(1)}%`}
        />
        <MetricRow
          label="Avg drawdown"
          value={`${profile.avg_max_drawdown_pct.toFixed(1)}%`}
        />
        <MetricRow
          label="Days to peak"
          value={`${profile.median_days_to_peak}d`}
        />
        <MetricRow
          label="Days to exit"
          value={`${profile.median_days_to_exit}d`}
        />
      </div>
    </div>
  );
}

function LazyBounceProfiles({ ticker }: { ticker: string }) {
  const { data, isLoading } = useBacktestProfile(ticker);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-2 text-xs text-gray-400">
        <div className="h-3 w-3 animate-spin rounded-full border border-gray-300 border-t-indigo-500" />
        Loading bounce history...
      </div>
    );
  }

  if (!data?.profiles?.length) return null;

  return (
    <div className="space-y-2">
      {data.profiles.map((p) => (
        <BounceProfileCard key={p.pullback_type} profile={p} />
      ))}
    </div>
  );
}

function SnapshotDetail({ snapshot: s }: { snapshot: ConvictionSnapshot }) {
  return (
    <div className="flex gap-6">
      <div className="flex-1 rounded-lg border border-gray-200 bg-gray-50 p-4">
        <div className="mb-3 flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-900">
            {s.ticker}
          </span>
          {s.trend_state.startsWith("pullback_to_") ? (
            <span className="rounded-full bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-[10px] font-medium text-emerald-600">
              PULLBACK ENTRY
            </span>
          ) : (
            <span className="rounded-full bg-gray-100 border border-gray-200 px-2 py-0.5 text-[10px] font-medium text-gray-500">
              {s.trend_state.replace(/_/g, " ").toUpperCase()}
            </span>
          )}
        </div>

        <LazyGateDetail ticker={s.ticker} />
        <div className="mt-3">
          <LazyBounceProfiles ticker={s.ticker} />
        </div>
      </div>

      {s.last_close > 0 && (
        <div className="w-52 shrink-0 rounded-lg border border-gray-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-400">
            Metrics
          </h4>
          <div className="space-y-2.5 text-sm">
            <MetricRow label="Close" value={`$${s.last_close.toFixed(2)}`} />
            <MetricRow
              label="Mkt Cap"
              value={s.market_cap ? formatMarketCap(s.market_cap) : "—"}
            />
            <MetricRow
              label="Inst. Own"
              value={
                s.institutional_pct != null
                  ? `${(s.institutional_pct * 100).toFixed(0)}%`
                  : "—"
              }
            />
            <MetricRow label="EMA 8" value={`$${s.ema_8.toFixed(2)}`} />
            <MetricRow label="EMA 21" value={`$${s.ema_21.toFixed(2)}`} />
            <MetricRow label="EMA 50" value={`$${s.ema_50.toFixed(2)}`} />
            <MetricRow
              label="50-EMA Slope"
              value={`${s.ema_50_slope > 0 ? "▲" : "▼"} ${Math.abs(s.ema_50_slope).toFixed(4)}`}
            />
            <MetricRow label="RSI (14)" value={s.rsi_14.toFixed(1)} />
            <MetricRow
              label="8-EMA Dist"
              value={`${s.price_to_8ema_pct.toFixed(2)}%`}
            />
            <MetricRow
              label="21-EMA Dist"
              value={`${s.price_to_21ema_pct.toFixed(2)}%`}
            />
            <MetricRow
              label="Days Above"
              value={`${s.days_above_both_emas}d`}
            />
            <MetricRow
              label="Volume"
              value={s.latest_volume.toLocaleString()}
            />
            <MetricRow
              label="Avg Vol 20d"
              value={s.avg_volume_20d.toLocaleString()}
            />
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
