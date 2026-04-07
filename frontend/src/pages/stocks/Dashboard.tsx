import { useState } from "react";
import { Card } from "@/components/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { DataTable, type DataTableColumn } from "@/components/DataTable";
import {
  useActivePullbacks,
  useStockRecommendations,
  useExpiredCsps,
  useRecordCspExpiry,
  useRemoveCspExpiry,
  useRefreshConviction,
  useTickerGates,
  useActivePositions,
  useCreatePosition,
  useExitPosition,
  useDeletePosition,
  useCheckExits,
  useRecentSignals,
} from "@/hooks/useApi";
import type {
  PullbackAlert,
  ConvictionTransition,
  StockBuyRecommendation,
  GateResult,
  StockPosition,
  ExitSignal,
} from "@/types";
import { formatMarketCap, convictionSortValue } from "@/lib/format";

function TransitionBanner({
  transitions,
}: {
  transitions: ConvictionTransition[];
}) {
  const pullbackTransitions = transitions.filter((t) =>
    ["pullback_to_8ema", "pullback_to_21ema"].includes(t.to_state),
  );

  if (pullbackTransitions.length === 0) return null;

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
      <h4 className="text-sm font-semibold text-amber-800">
        New Pullback Transitions Today
      </h4>
      <div className="mt-2 space-y-1">
        {pullbackTransitions.map((t) => (
          <div key={t.id} className="flex items-center gap-2 text-sm">
            <span className="font-mono font-semibold text-gray-800">
              {t.ticker}
            </span>
            <span className="text-gray-400">
              {t.from_state.replace(/_/g, " ")}
            </span>
            <span className="text-gray-400">&rarr;</span>
            <StatusBadge
              label={t.to_state.replace(/_/g, " ")}
              variant={
                t.to_state === "pullback_to_21ema" ? "danger" : "warning"
              }
            />
            <span className="text-gray-500">${t.last_close.toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const CONVICTION_VARIANT: Record<
  string,
  "success" | "warning" | "neutral" | "danger"
> = {
  high: "success",
  medium: "warning",
  low: "neutral",
  none: "danger",
};

function GateDetails({ ticker }: { ticker: string }) {
  const { data, isLoading } = useTickerGates(ticker);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-1 text-xs text-gray-400">
        <div className="h-3 w-3 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
        Loading gates…
      </div>
    );
  }

  if (!data || data.error) {
    return (
      <p className="text-xs text-gray-400">
        {data?.error ?? "Gate data unavailable"}
      </p>
    );
  }

  const gates: GateResult[] = data.gate_results;
  if (gates.length === 0) return null;

  return (
    <div className="mt-2">
      <p className="mb-1 text-xs font-semibold text-gray-500">
        Eligibility Gates
      </p>
      <div className="grid grid-cols-1 gap-1 md:grid-cols-2">
        {gates.map((g) => (
          <div
            key={g.gate}
            className={`flex items-center gap-2 rounded px-2 py-1 text-xs ${
              g.passed ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"
            }`}
          >
            <span>{g.passed ? "✓" : "✗"}</span>
            <span className="font-medium">{g.gate}</span>
            <span className="text-[10px] text-gray-500">{g.actual}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function BounceStatsPanel({
  bounce,
}: {
  bounce: PullbackAlert["historical_bounce"];
}) {
  if (!bounce) return null;

  return (
    <div className="rounded border border-indigo-100 bg-indigo-50 p-3">
      <h5 className="text-xs font-semibold text-indigo-700 uppercase mb-2">
        Historical Bounce Profile ({bounce.pullback_type.toUpperCase()} pullbacks
        &middot; {bounce.event_count} events)
      </h5>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <div>
          <span className="text-gray-500">Median peak gain</span>
          <div className="font-mono font-semibold text-indigo-800">
            {bounce.median_peak_gain_pct.toFixed(1)}%
          </div>
        </div>
        <div>
          <span className="text-gray-500">75th pctl (exit target)</span>
          <div className="font-mono font-semibold text-indigo-800">
            {bounce.suggested_exit_pct.toFixed(1)}%
          </div>
        </div>
        <div>
          <span className="text-gray-500">Win rate (≥5%)</span>
          <div className="font-mono font-semibold text-emerald-700">
            {(bounce.win_rate_5pct * 100).toFixed(0)}%
          </div>
        </div>
        <div>
          <span className="text-gray-500">Win rate (≥10%)</span>
          <div className="font-mono font-semibold text-emerald-700">
            {(bounce.win_rate_10pct * 100).toFixed(0)}%
          </div>
        </div>
        <div>
          <span className="text-gray-500">Median exit gain</span>
          <div className="font-mono text-gray-700">
            {bounce.median_exit_gain_pct.toFixed(1)}%
          </div>
        </div>
        <div>
          <span className="text-gray-500">Days to peak</span>
          <div className="font-mono text-gray-700">
            {bounce.median_days_to_peak}d
          </div>
        </div>
        <div>
          <span className="text-gray-500">Days to exit</span>
          <div className="font-mono text-gray-700">
            {bounce.median_days_to_exit}d
          </div>
        </div>
        <div>
          <span className="text-gray-500">Avg drawdown</span>
          <div className="font-mono text-red-600">
            {bounce.avg_max_drawdown_pct.toFixed(1)}%
          </div>
        </div>
      </div>
    </div>
  );
}

function BuyModal({
  ticker,
  price,
  pullbackType,
  onClose,
}: {
  ticker: string;
  price: number;
  pullbackType: string;
  onClose: () => void;
}) {
  const createPosition = useCreatePosition();
  const [form, setForm] = useState({
    quantity: "1",
    purchase_price: price.toFixed(2),
    purchase_date: new Date().toISOString().slice(0, 10),
  });

  const handleSubmit = () => {
    const qty = parseInt(form.quantity, 10);
    const px = parseFloat(form.purchase_price);
    if (!qty || qty < 1 || !px || px <= 0) return;

    createPosition.mutate(
      {
        ticker,
        purchase_price: px,
        quantity: qty,
        purchase_date: form.purchase_date,
        pullback_type: pullbackType,
      },
      { onSuccess: onClose },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-2xl">
        <h3 className="mb-4 text-lg font-bold text-gray-900">
          Record Stock Purchase — {ticker}
        </h3>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-gray-500">
              Purchase Price
            </label>
            <input
              type="number"
              step="0.01"
              value={form.purchase_price}
              onChange={(e) =>
                setForm({ ...form, purchase_price: e.target.value })
              }
              className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500">
              Quantity (shares)
            </label>
            <input
              type="number"
              min="1"
              value={form.quantity}
              onChange={(e) => setForm({ ...form, quantity: e.target.value })}
              className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500">
              Purchase Date
            </label>
            <input
              type="date"
              value={form.purchase_date}
              onChange={(e) =>
                setForm({ ...form, purchase_date: e.target.value })
              }
              className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm"
            />
          </div>
          <p className="text-xs text-gray-400">
            Entry type:{" "}
            <span className="font-semibold">
              {pullbackType === "8ema" ? "8-EMA pullback" : "21-EMA pullback"}
            </span>
          </p>
        </div>
        <div className="mt-5 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded px-4 py-2 text-sm text-gray-600 hover:bg-gray-100"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={createPosition.isPending}
            className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {createPosition.isPending ? "Saving..." : "Record Purchase"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ExpandedPullbackRow({
  alert,
  recommendation,
}: {
  alert: PullbackAlert;
  recommendation?: StockBuyRecommendation;
}) {
  const [showBuyModal, setShowBuyModal] = useState(false);
  const pullbackType = alert.alert_type === "pullback_21ema" ? "21ema" : "8ema";

  return (
    <div className="space-y-2">
      {recommendation ? (
        <>
          <div className="rounded bg-gray-50 p-2 text-sm text-gray-600">
            {recommendation.recommendation}
          </div>
          <p className="text-xs text-gray-400">
            {recommendation.risk_reward_note}
          </p>
          {recommendation.related_csp_strike != null && (
            <p className="text-xs text-blue-600">
              Related CSP strike: ${recommendation.related_csp_strike.toFixed(2)}
            </p>
          )}
        </>
      ) : (
        <div className="text-sm text-gray-600">{alert.suggested_action}</div>
      )}
      <BounceStatsPanel bounce={alert.historical_bounce} />
      <GateDetails ticker={alert.ticker} />
      <div className="pt-1">
        <button
          onClick={() => setShowBuyModal(true)}
          className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
        >
          I Bought This
        </button>
      </div>
      {showBuyModal && (
        <BuyModal
          ticker={alert.ticker}
          price={alert.last_close}
          pullbackType={pullbackType}
          onClose={() => setShowBuyModal(false)}
        />
      )}
    </div>
  );
}

const pullbackColumns: DataTableColumn<PullbackAlert>[] = [
  {
    key: "ticker",
    header: "Ticker",
    accessor: (r) => r.ticker,
    sortable: true,
    width: "120px",
    render: (r) => (
      <div>
        <span className="font-mono font-bold text-gray-900">{r.ticker}</span>
        {r.name && (
          <div className="text-[11px] text-gray-400 truncate max-w-[100px]">
            {r.name}
          </div>
        )}
      </div>
    ),
  },
  {
    key: "alert_type",
    header: "Type",
    accessor: (r) => r.alert_type,
    sortable: true,
    render: (r) => (
      <StatusBadge
        label={r.alert_type === "pullback_21ema" ? "21-EMA" : "8-EMA"}
        variant={r.alert_type === "pullback_21ema" ? "danger" : "info"}
      />
    ),
    filter: {
      type: "select",
      placeholder: "All",
      options: [
        { value: "pullback_21ema", label: "21-EMA" },
        { value: "pullback_8ema", label: "8-EMA" },
      ],
    },
  },
  {
    key: "conviction_level",
    header: "Conviction",
    accessor: (r) => convictionSortValue(r.conviction_level),
    sortable: true,
    render: (r) => (
      <StatusBadge
        label={r.conviction_level}
        variant={CONVICTION_VARIANT[r.conviction_level] ?? "neutral"}
      />
    ),
    filter: {
      type: "select",
      placeholder: "All",
      options: [
        { value: "3", label: "High" },
        { value: "2", label: "Medium" },
        { value: "1", label: "Low" },
      ],
    },
  },
  {
    key: "last_close",
    header: "Price",
    accessor: (r) => r.last_close,
    sortable: true,
    align: "right",
    render: (r) => (
      <span className="font-mono">${r.last_close.toFixed(2)}</span>
    ),
  },
  {
    key: "market_cap",
    header: "Mkt Cap",
    accessor: (r) => r.market_cap,
    sortable: true,
    align: "right",
    render: (r) => (
      <span className="text-gray-600">
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
        { value: "200e9", label: "$200B" },
      ],
    },
  },
  {
    key: "institutional_pct",
    header: "Inst%",
    accessor: (r) => r.institutional_pct,
    sortable: true,
    align: "right",
    render: (r) => (
      <span className="text-gray-600">
        {r.institutional_pct != null
          ? `${(r.institutional_pct * 100).toFixed(1)}%`
          : "—"}
      </span>
    ),
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
    key: "exchange",
    header: "Exch",
    accessor: (r) => r.exchange,
    sortable: true,
    render: (r) => (
      <span className="text-xs text-gray-500">{r.exchange || "—"}</span>
    ),
  },
  {
    key: "rsi_14",
    header: "RSI",
    accessor: (r) => r.rsi_14,
    sortable: true,
    align: "right",
    render: (r) => {
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
    key: "ema_values",
    header: "8 / 21 EMA",
    accessor: (r) => r.ema_8,
    render: (r) => (
      <span className="text-xs text-gray-600">
        ${r.ema_8.toFixed(2)} / ${r.ema_21.toFixed(2)}
      </span>
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
    key: "days_above_both_emas",
    header: "Days",
    accessor: (r) => r.days_above_both_emas,
    sortable: true,
    align: "right",
    render: (r) => (
      <span className="text-gray-600">{r.days_above_both_emas}d</span>
    ),
  },
  {
    key: "position_size_hint",
    header: "Size",
    accessor: (r) => r.position_size_hint,
    sortable: true,
    render: (r) => (
      <span className="capitalize text-gray-600">{r.position_size_hint}</span>
    ),
  },
  {
    key: "stop_loss_level",
    header: "Stop",
    accessor: (r) => r.stop_loss_level,
    align: "right",
    render: (r) => (
      <span className="font-mono text-xs text-gray-500">
        ${r.stop_loss_level.toFixed(2)}
      </span>
    ),
  },
  {
    key: "historical_bounce",
    header: "Hist. Bounce",
    accessor: (r) => r.historical_bounce?.median_peak_gain_pct ?? -999,
    sortable: true,
    align: "right",
    render: (r) => {
      const b = r.historical_bounce;
      if (!b) return <span className="text-gray-300">—</span>;
      return (
        <span title={`${b.event_count} events, ${(b.win_rate_5pct * 100).toFixed(0)}% win rate (≥5%)`}>
          <span className="font-mono text-sm">
            {b.median_peak_gain_pct.toFixed(1)}%
          </span>
          <span className="ml-1 text-[10px] text-gray-400">
            ({b.event_count})
          </span>
        </span>
      );
    },
  },
];

function PullbackSection({
  title,
  alerts,
  emptyMessage,
  recMap,
}: {
  title: string;
  alerts: PullbackAlert[];
  emptyMessage: string;
  recMap: Map<string, StockBuyRecommendation>;
}) {
  return (
    <Card title={title} subtitle={`${alerts.length} pullback(s)`}>
      {alerts.length === 0 ? (
        <p className="text-sm text-gray-400">{emptyMessage}</p>
      ) : (
        <DataTable
          data={alerts}
          columns={pullbackColumns}
          searchField={(r) => `${r.ticker} ${r.name}`}
          rowKey={(r) => r.ticker}
          defaultSortKey="conviction_level"
          defaultSortDir="desc"
          defaultPageSize={15}
          emptyMessage={emptyMessage}
          expandedRow={(r) => (
            <ExpandedPullbackRow
              alert={r}
              recommendation={recMap.get(r.ticker)}
            />
          )}
        />
      )}
    </Card>
  );
}

const positionColumns: DataTableColumn<StockPosition>[] = [
  {
    key: "ticker",
    header: "Ticker",
    accessor: (r) => r.ticker,
    sortable: true,
    width: "100px",
    render: (r) => (
      <span className="font-mono font-bold text-gray-900">{r.ticker}</span>
    ),
  },
  {
    key: "quantity",
    header: "Qty",
    accessor: (r) => r.quantity,
    sortable: true,
    align: "right",
    render: (r) => <span className="font-mono">{r.quantity}</span>,
  },
  {
    key: "purchase_price",
    header: "Entry",
    accessor: (r) => r.purchase_price,
    sortable: true,
    align: "right",
    render: (r) => (
      <span className="font-mono">${r.purchase_price.toFixed(2)}</span>
    ),
  },
  {
    key: "current_price",
    header: "Current",
    accessor: (r) => r.current_price ?? 0,
    sortable: true,
    align: "right",
    render: (r) => (
      <span className="font-mono">
        {r.current_price ? `$${r.current_price.toFixed(2)}` : "—"}
      </span>
    ),
  },
  {
    key: "current_gain_pct",
    header: "Gain %",
    accessor: (r) => r.current_gain_pct ?? 0,
    sortable: true,
    align: "right",
    render: (r) => {
      if (r.current_gain_pct == null) return <span className="text-gray-300">—</span>;
      const color =
        r.current_gain_pct > 0
          ? "text-emerald-600"
          : r.current_gain_pct < 0
            ? "text-red-600"
            : "text-gray-500";
      return (
        <span className={`font-mono font-semibold ${color}`}>
          {r.current_gain_pct > 0 ? "+" : ""}
          {r.current_gain_pct.toFixed(2)}%
        </span>
      );
    },
  },
  {
    key: "target_exit_price",
    header: "Target",
    accessor: (r) => r.target_exit_price ?? 0,
    sortable: true,
    align: "right",
    render: (r) => (
      <span className="font-mono text-xs text-indigo-600">
        {r.target_exit_price ? `$${r.target_exit_price.toFixed(2)}` : "—"}
        {r.target_exit_pct ? (
          <span className="ml-1 text-gray-400">({r.target_exit_pct.toFixed(1)}%)</span>
        ) : null}
      </span>
    ),
  },
  {
    key: "stop_loss_price",
    header: "Stop (8-EMA)",
    accessor: (r) => r.stop_loss_price ?? 0,
    align: "right",
    render: (r) => (
      <span className="font-mono text-xs text-red-500">
        {r.stop_loss_price ? `$${r.stop_loss_price.toFixed(2)}` : "—"}
      </span>
    ),
  },
  {
    key: "pullback_type",
    header: "Entry Type",
    accessor: (r) => r.pullback_type,
    sortable: true,
    render: (r) => (
      <StatusBadge
        label={r.pullback_type}
        variant={r.pullback_type === "21ema" ? "danger" : r.pullback_type === "8ema" ? "info" : "neutral"}
      />
    ),
  },
  {
    key: "purchase_date",
    header: "Date",
    accessor: (r) => r.purchase_date ?? "",
    sortable: true,
    render: (r) => (
      <span className="text-xs text-gray-500">{r.purchase_date ?? "—"}</span>
    ),
  },
  {
    key: "status",
    header: "Status",
    accessor: (r) => r.status,
    sortable: true,
    render: (r) => {
      const v =
        r.status === "active"
          ? "success"
          : r.status === "profit_target_hit"
            ? "info"
            : r.status === "stop_loss_hit"
              ? "danger"
              : "neutral";
      return <StatusBadge label={r.status.replace(/_/g, " ")} variant={v} />;
    },
  },
];

function PositionsPanel() {
  const { data: positions, isLoading } = useActivePositions();
  const { data: signals } = useRecentSignals();
  const checkExits = useCheckExits();
  const exitPosition = useExitPosition();
  const deletePos = useDeletePosition();
  const createPosition = useCreatePosition();
  const [showAddForm, setShowAddForm] = useState(false);
  const [addForm, setAddForm] = useState({
    ticker: "",
    purchase_price: "",
    quantity: "1",
    purchase_date: new Date().toISOString().slice(0, 10),
    pullback_type: "manual",
  });

  const handleAdd = () => {
    const px = parseFloat(addForm.purchase_price);
    const qty = parseInt(addForm.quantity, 10);
    if (!addForm.ticker || !px || px <= 0 || !qty || qty < 1) return;
    createPosition.mutate(
      {
        ticker: addForm.ticker.toUpperCase(),
        purchase_price: px,
        quantity: qty,
        purchase_date: addForm.purchase_date,
        pullback_type: addForm.pullback_type,
      },
      {
        onSuccess: () => {
          setAddForm({
            ticker: "",
            purchase_price: "",
            quantity: "1",
            purchase_date: new Date().toISOString().slice(0, 10),
            pullback_type: "manual",
          });
          setShowAddForm(false);
        },
      },
    );
  };

  const activeSignals = (signals ?? []).filter((s: ExitSignal) => {
    const age = Date.now() - new Date(s.triggered_at ?? 0).getTime();
    return age < 7 * 24 * 60 * 60 * 1000;
  });

  return (
    <Card
      title="Stock Positions"
      subtitle="Tracked purchases with data-driven exit targets"
    >
      {activeSignals.length > 0 && (
        <div className="mb-4 space-y-2">
          {activeSignals.map((s: ExitSignal) => (
            <div
              key={s.id}
              className={`flex items-center justify-between rounded-lg border p-3 text-sm ${
                s.signal_type === "profit_target"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : "border-red-200 bg-red-50 text-red-800"
              }`}
            >
              <div className="flex items-center gap-3">
                <span className="text-lg">
                  {s.signal_type === "profit_target" ? "🎯" : "🛑"}
                </span>
                <div>
                  <span className="font-semibold">{s.ticker}</span>
                  {" — "}
                  {s.signal_type === "profit_target"
                    ? "Profit target reached"
                    : "Close below 8-EMA stop loss"}
                </div>
              </div>
              <div className="text-right font-mono text-xs">
                <div>
                  ${s.current_price.toFixed(2)} ({s.gain_pct > 0 ? "+" : ""}
                  {s.gain_pct.toFixed(2)}%)
                </div>
                <div className="text-gray-400">
                  Trigger: ${s.trigger_price.toFixed(2)}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="mb-3 flex items-center gap-2">
        <button
          onClick={() => checkExits.mutate()}
          disabled={checkExits.isPending}
          className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {checkExits.isPending ? "Checking..." : "Check Exit Signals"}
        </button>
        <button
          onClick={() => setShowAddForm(!showAddForm)}
          className="rounded border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          {showAddForm ? "Cancel" : "Add Position"}
        </button>
      </div>

      {showAddForm && (
        <div className="mb-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
          <h4 className="mb-3 text-sm font-semibold text-gray-700">
            Manual Position Entry
          </h4>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <input
              type="text"
              placeholder="Ticker"
              value={addForm.ticker}
              onChange={(e) =>
                setAddForm({ ...addForm, ticker: e.target.value })
              }
              className="rounded border border-gray-300 px-3 py-1.5 text-sm"
            />
            <input
              type="number"
              step="0.01"
              placeholder="Price"
              value={addForm.purchase_price}
              onChange={(e) =>
                setAddForm({ ...addForm, purchase_price: e.target.value })
              }
              className="rounded border border-gray-300 px-3 py-1.5 text-sm"
            />
            <input
              type="number"
              min="1"
              placeholder="Qty"
              value={addForm.quantity}
              onChange={(e) =>
                setAddForm({ ...addForm, quantity: e.target.value })
              }
              className="rounded border border-gray-300 px-3 py-1.5 text-sm"
            />
            <input
              type="date"
              value={addForm.purchase_date}
              onChange={(e) =>
                setAddForm({ ...addForm, purchase_date: e.target.value })
              }
              className="rounded border border-gray-300 px-3 py-1.5 text-sm"
            />
            <select
              value={addForm.pullback_type}
              onChange={(e) =>
                setAddForm({ ...addForm, pullback_type: e.target.value })
              }
              className="rounded border border-gray-300 px-3 py-1.5 text-sm"
            >
              <option value="manual">Manual</option>
              <option value="8ema">8-EMA Pullback</option>
              <option value="21ema">21-EMA Pullback</option>
            </select>
          </div>
          <button
            onClick={handleAdd}
            disabled={createPosition.isPending}
            className="mt-3 rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {createPosition.isPending ? "Saving..." : "Record Purchase"}
          </button>
        </div>
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-8">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-blue-600 border-t-transparent" />
        </div>
      ) : !positions || positions.length === 0 ? (
        <p className="py-4 text-center text-sm text-gray-400">
          No active positions. Use &quot;I Bought This&quot; on a pullback alert
          or add one manually.
        </p>
      ) : (
        <DataTable
          data={positions}
          columns={positionColumns}
          searchField={(r) => r.ticker}
          rowKey={(r) => r.id}
          defaultSortKey="current_gain_pct"
          defaultSortDir="desc"
          defaultPageSize={10}
          emptyMessage="No active positions."
          expandedRow={(r) => (
            <PositionActions
              position={r}
              onExit={exitPosition.mutate}
              onDelete={deletePos.mutate}
            />
          )}
        />
      )}
    </Card>
  );
}

function PositionActions({
  position,
  onExit,
  onDelete,
}: {
  position: StockPosition;
  onExit: (args: { id: string; exitPrice: number; exitReason?: string }) => void;
  onDelete: (id: string) => void;
}) {
  const [exitPrice, setExitPrice] = useState(
    (position.current_price ?? position.purchase_price).toFixed(2),
  );

  return (
    <div className="flex items-center gap-4 py-2">
      <div className="flex items-center gap-2">
        <label className="text-xs text-gray-500">Sell price:</label>
        <input
          type="number"
          step="0.01"
          value={exitPrice}
          onChange={(e) => setExitPrice(e.target.value)}
          className="w-24 rounded border border-gray-300 px-2 py-1 text-sm"
        />
        <button
          onClick={() =>
            onExit({
              id: position.id,
              exitPrice: parseFloat(exitPrice),
              exitReason: "manual",
            })
          }
          className="rounded bg-amber-600 px-3 py-1 text-xs font-medium text-white hover:bg-amber-700"
        >
          Mark as Sold
        </button>
      </div>
      <button
        onClick={() => {
          if (window.confirm(`Delete position ${position.ticker}?`)) {
            onDelete(position.id);
          }
        }}
        className="text-xs text-red-500 hover:text-red-700"
      >
        Delete
      </button>
      {position.target_exit_price && position.current_price && (
        <div className="ml-auto text-xs text-gray-500">
          {position.current_price >= position.target_exit_price ? (
            <span className="font-semibold text-emerald-600">
              Target reached — consider selling
            </span>
          ) : (
            <span>
              {(
                ((position.target_exit_price - position.current_price) /
                  position.current_price) *
                100
              ).toFixed(1)}
              % to target
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function CspExpiryTracker() {
  const { data: expiredCsps } = useExpiredCsps();
  const recordExpiry = useRecordCspExpiry();
  const removeExpiry = useRemoveCspExpiry();
  const [open, setOpen] = useState(false);

  const [expiryForm, setExpiryForm] = useState({
    ticker: "",
    strike: "",
    expiry_date: "",
    premium_collected: "",
  });

  const handleRecordExpiry = () => {
    if (
      !expiryForm.ticker ||
      !expiryForm.strike ||
      !expiryForm.expiry_date ||
      !expiryForm.premium_collected
    )
      return;
    recordExpiry.mutate(
      {
        ticker: expiryForm.ticker.toUpperCase(),
        strike: parseFloat(expiryForm.strike),
        expiry_date: expiryForm.expiry_date,
        premium_collected: parseFloat(expiryForm.premium_collected),
      },
      {
        onSuccess: () =>
          setExpiryForm({
            ticker: "",
            strike: "",
            expiry_date: "",
            premium_collected: "",
          }),
      },
    );
  };

  return (
    <Card
      title={
        <button
          onClick={() => setOpen(!open)}
          className="flex w-full items-center justify-between text-left"
        >
          <span>CSP Expiry Watchlist</span>
          <span className="text-xs text-gray-400">
            {open ? "▲ Collapse" : "▼ Expand"}
          </span>
        </button>
      }
      subtitle="Track CSPs that expired worthless for fallback stock buy alerts"
    >
      {open && (
        <div className="space-y-4">
          <div className="grid grid-cols-5 gap-2">
            <input
              type="text"
              placeholder="Ticker"
              value={expiryForm.ticker}
              onChange={(e) =>
                setExpiryForm({ ...expiryForm, ticker: e.target.value })
              }
              className="rounded border border-gray-300 px-3 py-1.5 text-sm"
            />
            <input
              type="number"
              placeholder="Strike"
              value={expiryForm.strike}
              onChange={(e) =>
                setExpiryForm({ ...expiryForm, strike: e.target.value })
              }
              className="rounded border border-gray-300 px-3 py-1.5 text-sm"
            />
            <input
              type="date"
              value={expiryForm.expiry_date}
              onChange={(e) =>
                setExpiryForm({ ...expiryForm, expiry_date: e.target.value })
              }
              className="rounded border border-gray-300 px-3 py-1.5 text-sm"
            />
            <input
              type="number"
              placeholder="Premium"
              value={expiryForm.premium_collected}
              onChange={(e) =>
                setExpiryForm({
                  ...expiryForm,
                  premium_collected: e.target.value,
                })
              }
              className="rounded border border-gray-300 px-3 py-1.5 text-sm"
            />
            <button
              onClick={handleRecordExpiry}
              disabled={recordExpiry.isPending}
              className="rounded bg-gray-800 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-50"
            >
              Record
            </button>
          </div>

          {expiredCsps && expiredCsps.length > 0 ? (
            <div className="divide-y divide-gray-100 rounded-lg border border-gray-200">
              {expiredCsps.map((csp) => (
                <div
                  key={`${csp.ticker}-${csp.expired_strike}`}
                  className="flex items-center justify-between px-4 py-3"
                >
                  <div className="flex items-center gap-4">
                    <span className="font-mono font-semibold">
                      {csp.ticker}
                    </span>
                    <span className="text-sm text-gray-500">
                      ${csp.expired_strike.toFixed(2)} strike
                    </span>
                    <span className="text-sm text-gray-500">
                      {csp.expiry_date}
                    </span>
                    <span className="text-sm text-green-600">
                      +${csp.premium_collected.toFixed(2)} premium
                    </span>
                  </div>
                  <button
                    onClick={() => removeExpiry.mutate(csp.ticker)}
                    className="text-xs text-red-500 hover:text-red-700"
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400">
              No expired CSPs recorded yet.
            </p>
          )}
        </div>
      )}
    </Card>
  );
}

export function StocksDashboard() {
  const { data, isLoading, error } = useActivePullbacks();
  const { data: recsData } = useStockRecommendations();
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

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
      </div>
    );
  }

  if (error) {
    return (
      <Card title="Error">
        <p className="text-sm text-red-600">{(error as Error).message}</p>
      </Card>
    );
  }

  const watchlist = data?.watchlist ?? [];
  const universe = data?.universe ?? [];
  const transitions = data?.transitions_today ?? [];

  const recMap = new Map<string, StockBuyRecommendation>();
  for (const rec of recsData?.recommendations ?? []) {
    recMap.set(rec.ticker, rec);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">
            Stocks — Pullback Dashboard
          </h1>
          <p className="text-sm text-gray-400">
            EMA pullback opportunities from persisted conviction data
            {data?.as_of_date && ` (${data.as_of_date})`}
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshMutation.isPending}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
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

      <TransitionBanner transitions={transitions} />

      <PullbackSection
        title="Watchlist Pullbacks"
        alerts={watchlist}
        emptyMessage="No watchlist tickers are currently in a pullback state."
        recMap={recMap}
      />

      <PullbackSection
        title="Universe Pullbacks"
        alerts={universe}
        emptyMessage="No universe tickers are currently in a pullback state. Run 'Refresh Conviction' to scan the full universe."
        recMap={recMap}
      />

      <PositionsPanel />

      <CspExpiryTracker />
    </div>
  );
}
