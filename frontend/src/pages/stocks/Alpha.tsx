import { useMemo, useState } from "react";
import {
  Rocket,
  RefreshCw,
  Zap,
  TrendingUp,
  Telescope,
  Star,
  Brain,
} from "lucide-react";
import { Card } from "@/components/Card";
import { DataTable, type DataTableColumn } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { useAlphaScan, useRecomputeAlpha } from "@/hooks/useApi";
import { formatMarketCap } from "@/lib/format";
import type { AlphaSignal } from "@/types";

const SIGNAL_META: Record<
  string,
  { label: string; variant: "success" | "warning" | "danger" | "info" | "neutral" }
> = {
  strong_buy: { label: "Strong Buy", variant: "success" },
  buy: { label: "Buy", variant: "info" },
  watch: { label: "Watch", variant: "warning" },
  avoid: { label: "Avoid", variant: "neutral" },
};

const HORIZON_META: Record<string, { label: string; icon: typeof Zap; hint: string }> = {
  swing: { label: "Swing", icon: Zap, hint: "~40 day move (25%+ target)" },
  trend: { label: "Trend", icon: TrendingUp, hint: "~60 day move (40%+ target)" },
  thematic: { label: "Thematic", icon: Telescope, hint: "~120 day move (60%+ target)" },
  none: { label: "—", icon: Zap, hint: "No clear directional horizon" },
};

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

const MARKET_CAP_PRESETS: { value: number; label: string }[] = [
  { value: 250, label: "$250M+" },
  { value: 500, label: "$500M+" },
  { value: 1000, label: "$1B+" },
  { value: 2000, label: "$2B+" },
  { value: 4000, label: "$4B+" },
  { value: 10000, label: "$10B+" },
];

const MIN_CAP_STORAGE_KEY = "tyche_alpha_min_market_cap_m";

function loadMinCap(): number {
  const raw =
    typeof window !== "undefined"
      ? window.localStorage.getItem(MIN_CAP_STORAGE_KEY)
      : null;
  const parsed = raw != null ? Number(raw) : NaN;
  return Number.isFinite(parsed) ? parsed : 1000;
}

function FactorBar({ label, value }: { label: string; value: number }) {
  const w = Math.round(Math.min(1, Math.max(0, value)) * 100);
  const color =
    value >= 0.66 ? "bg-emerald-500" : value >= 0.33 ? "bg-amber-500" : "bg-gray-300";
  return (
    <div className="flex items-center gap-2">
      <span className="w-24 shrink-0 text-xs text-gray-500">{label}</span>
      <div className="h-2 flex-1 rounded-full bg-gray-100">
        <div className={`h-2 rounded-full ${color}`} style={{ width: `${w}%` }} />
      </div>
      <span className="w-8 shrink-0 text-right text-xs tabular-nums text-gray-600">
        {w}
      </span>
    </div>
  );
}

function buildColumns(): DataTableColumn<AlphaSignal>[] {
  return [
    {
      key: "signal",
      header: "Signal",
      accessor: (r) =>
        r.signal === "strong_buy" ? 3 : r.signal === "buy" ? 2 : r.signal === "watch" ? 1 : 0,
      render: (r) => {
        const m = SIGNAL_META[r.signal] ?? SIGNAL_META.avoid;
        return <StatusBadge label={m.label} variant={m.variant} />;
      },
      filter: {
        type: "multiselect",
        options: [
          { value: "strong_buy", label: "Strong Buy" },
          { value: "buy", label: "Buy" },
          { value: "watch", label: "Watch" },
          { value: "avoid", label: "Avoid" },
        ],
      },
    },
    {
      key: "ticker",
      header: "Ticker",
      accessor: (r) => r.ticker,
      render: (r) => (
        <div className="flex items-center gap-1.5">
          <span className="font-semibold text-gray-900">{r.ticker}</span>
          {r.is_watchlist && <Star className="h-3 w-3 fill-amber-400 text-amber-400" />}
        </div>
      ),
    },
    {
      key: "alpha_score",
      header: "Alpha",
      align: "right",
      sortable: true,
      accessor: (r) => r.alpha_score,
      render: (r) => (
        <span className="font-bold tabular-nums text-gray-900">
          {r.alpha_score.toFixed(0)}
        </span>
      ),
      filter: {
        type: "min",
        minOptions: [
          { value: "70", label: "≥ 70" },
          { value: "60", label: "≥ 60" },
          { value: "50", label: "≥ 50" },
        ],
      },
    },
    {
      key: "horizon",
      header: "Horizon",
      accessor: (r) => r.horizon,
      render: (r) => {
        const m = HORIZON_META[r.horizon] ?? HORIZON_META.none;
        const Icon = m.icon;
        if (r.horizon === "none")
          return <span className="text-xs text-gray-400">—</span>;
        return (
          <div className="flex items-center gap-1" title={m.hint}>
            <Icon className="h-3.5 w-3.5 text-indigo-500" />
            <span className="text-xs font-medium text-gray-700">{m.label}</span>
          </div>
        );
      },
      filter: {
        type: "multiselect",
        options: [
          { value: "swing", label: "Swing" },
          { value: "trend", label: "Trend" },
          { value: "thematic", label: "Thematic" },
        ],
      },
    },
    {
      key: "breakout_prob",
      header: "Move Prob",
      align: "right",
      sortable: true,
      accessor: (r) =>
        Math.max(
          r.breakout_prob_swing ?? 0,
          r.breakout_prob_trend ?? 0,
          r.breakout_prob_thematic ?? 0,
        ),
      render: (r) => {
        const best = Math.max(
          r.breakout_prob_swing ?? 0,
          r.breakout_prob_trend ?? 0,
          r.breakout_prob_thematic ?? 0,
        );
        if (best <= 0) return <span className="text-xs text-gray-400">—</span>;
        const color =
          best >= 0.5 ? "text-emerald-600" : best >= 0.25 ? "text-amber-600" : "text-gray-500";
        return (
          <span className={`flex items-center justify-end gap-1 tabular-nums ${color}`}>
            <Brain className="h-3 w-3" />
            {pct(best, 0)}
          </span>
        );
      },
    },
    {
      key: "rs_126d",
      header: "RS vs SPY (6m)",
      align: "right",
      sortable: true,
      accessor: (r) => r.rs_126d ?? null,
      render: (r) => {
        if (r.rs_126d == null) return <span className="text-xs text-gray-400">—</span>;
        const positive = r.rs_126d >= 0;
        return (
          <span className={`tabular-nums ${positive ? "text-emerald-600" : "text-red-500"}`}>
            {positive ? "+" : ""}
            {pct(r.rs_126d)}
          </span>
        );
      },
    },
    {
      key: "return_126d",
      header: "Return (6m)",
      align: "right",
      sortable: true,
      accessor: (r) => r.return_126d ?? null,
      render: (r) => {
        if (r.return_126d == null) return <span className="text-xs text-gray-400">—</span>;
        const positive = r.return_126d >= 0;
        return (
          <span className={`tabular-nums ${positive ? "text-emerald-600" : "text-red-500"}`}>
            {positive ? "+" : ""}
            {pct(r.return_126d)}
          </span>
        );
      },
    },
    {
      key: "pct_off_52w_high",
      header: "Off 52w High",
      align: "right",
      sortable: true,
      accessor: (r) => r.pct_off_52w_high ?? null,
      render: (r) =>
        r.pct_off_52w_high == null ? (
          <span className="text-xs text-gray-400">—</span>
        ) : (
          <span className="tabular-nums text-gray-600">
            {r.pct_off_52w_high.toFixed(1)}%
          </span>
        ),
    },
    {
      key: "last_close",
      header: "Price",
      align: "right",
      sortable: true,
      accessor: (r) => r.last_close,
      render: (r) => <span className="tabular-nums text-gray-700">${r.last_close.toFixed(2)}</span>,
    },
    {
      key: "market_cap",
      header: "Mkt Cap",
      align: "right",
      sortable: true,
      accessor: (r) => r.market_cap ?? null,
      render: (r) =>
        r.market_cap ? (
          <span className="tabular-nums text-gray-600">{formatMarketCap(r.market_cap)}</span>
        ) : (
          <span className="text-xs text-gray-400">—</span>
        ),
    },
    {
      key: "institutional_pct",
      header: "Inst Own",
      align: "right",
      sortable: true,
      accessor: (r) => r.institutional_pct ?? null,
      render: (r) =>
        r.institutional_pct == null ? (
          <span className="text-xs text-gray-400">—</span>
        ) : (
          <span className="tabular-nums text-gray-600">{pct(r.institutional_pct, 0)}</span>
        ),
      filter: {
        type: "min",
        minOptions: [
          { value: "0.7", label: "≥ 70%" },
          { value: "0.5", label: "≥ 50%" },
          { value: "0.3", label: "≥ 30%" },
        ],
      },
    },
  ];
}

function ExpandedRow({ r }: { r: AlphaSignal }) {
  return (
    <div className="grid grid-cols-1 gap-6 p-4 md:grid-cols-2">
      <div>
        <h4 className="mb-3 text-sm font-semibold text-gray-700">Factor Breakdown</h4>
        <div className="space-y-2">
          <FactorBar label="Momentum" value={r.factors.momentum} />
          <FactorBar label="Rel. Strength" value={r.factors.relative_strength} />
          <FactorBar label="Trend Quality" value={r.factors.trend_quality} />
          <FactorBar label="Breakout" value={r.factors.breakout} />
          <FactorBar label="Volume Thrust" value={r.factors.volume_thrust} />
        </div>
      </div>
      <div>
        <h4 className="mb-3 text-sm font-semibold text-gray-700">
          ML Big-Move Probabilities
        </h4>
        <div className="space-y-2 text-sm">
          <ProbRow label="Swing (+25% / 40d)" value={r.breakout_prob_swing} />
          <ProbRow label="Trend (+40% / 60d)" value={r.breakout_prob_trend} />
          <ProbRow label="Thematic (+60% / 120d)" value={r.breakout_prob_thematic} />
        </div>
        <div className="mt-4 grid grid-cols-3 gap-3 text-xs">
          <Metric label="Return 3m" value={pct(r.return_63d)} />
          <Metric label="Return 6m" value={pct(r.return_126d)} />
          <Metric label="Return 12m" value={pct(r.return_252d)} />
          <Metric label="RS 6m vs SPY" value={pct(r.rs_126d)} />
          <Metric label="EMA Stack" value={`${r.ema_stack_score}/3`} />
          <Metric
            label="Vol Thrust"
            value={r.volume_thrust_ratio ? `${r.volume_thrust_ratio.toFixed(2)}x` : "—"}
          />
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
          {r.sector && (
            <span>
              Sector: <span className="font-medium text-gray-700">{r.sector}</span>
            </span>
          )}
          <span>
            Market Cap:{" "}
            <span className="font-medium text-gray-700">
              {r.market_cap ? formatMarketCap(r.market_cap) : "—"}
            </span>
          </span>
          <span>
            Institutional Own:{" "}
            <span className="font-medium text-gray-700">
              {r.institutional_pct == null ? "—" : pct(r.institutional_pct, 0)}
            </span>
          </span>
        </div>
      </div>
    </div>
  );
}

function ProbRow({ label, value }: { label: string; value: number | null }) {
  const w = value != null ? Math.round(value * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <span className="w-40 shrink-0 text-xs text-gray-500">{label}</span>
      <div className="h-2 flex-1 rounded-full bg-gray-100">
        <div className="h-2 rounded-full bg-indigo-500" style={{ width: `${w}%` }} />
      </div>
      <span className="w-10 shrink-0 text-right text-xs tabular-nums text-gray-600">
        {value != null ? `${w}%` : "—"}
      </span>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-gray-50 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-gray-400">{label}</div>
      <div className="font-semibold tabular-nums text-gray-700">{value}</div>
    </div>
  );
}

function Pill({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-4 py-2">
      <div className="text-xs text-gray-400">{label}</div>
      <div className={`text-lg font-bold ${accent ?? "text-gray-900"}`}>{value}</div>
    </div>
  );
}

export function Alpha() {
  const [minCapM, setMinCapM] = useState<number>(loadMinCap);
  const { data, isLoading, error } = useAlphaScan({
    limit: 500,
    minMarketCapMillions: minCapM,
  });
  const recompute = useRecomputeAlpha();
  const [recomputeMsg, setRecomputeMsg] = useState<string | null>(null);

  const columns = useMemo(() => buildColumns(), []);
  const signals = data?.signals ?? [];

  const handleMinCapChange = (value: number) => {
    setMinCapM(value);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(MIN_CAP_STORAGE_KEY, String(value));
    }
  };

  const handleRecompute = async () => {
    setRecomputeMsg(null);
    try {
      const res = await recompute.mutateAsync(undefined);
      setRecomputeMsg(
        res.status === "started"
          ? "Recompute started in the background — refresh in a few minutes."
          : `Recomputed ${res.signals} signals.`,
      );
    } catch (e) {
      setRecomputeMsg((e as Error).message);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-gray-900">
            <Rocket className="h-6 w-6 text-indigo-600" />
            Directional Alpha
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            High-conviction buy signals for large upside moves — momentum, relative strength,
            and an ML big-move model. Complements (does not replace) the CSP / Covered Call income engine.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-600">
            <span className="whitespace-nowrap text-gray-500">Min Mkt Cap</span>
            <select
              value={minCapM}
              onChange={(e) => handleMinCapChange(Number(e.target.value))}
              className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-800 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              {MARKET_CAP_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={handleRecompute}
            disabled={recompute.isPending}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${recompute.isPending ? "animate-spin" : ""}`} />
            Recompute
          </button>
        </div>
      </div>

      {recomputeMsg && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-700">
          {recomputeMsg}
        </div>
      )}

      <div className="flex flex-wrap gap-3">
        <Pill label="Ranked" value={String(data?.total ?? 0)} />
        <Pill
          label="Strong Buy"
          value={String(data?.strong_buy_count ?? 0)}
          accent="text-emerald-600"
        />
        <Pill label="Buy" value={String(data?.buy_count ?? 0)} accent="text-blue-600" />
        <Pill
          label="ML Model"
          value={data?.ml_available ? "Active" : "Rules-only"}
          accent={data?.ml_available ? "text-indigo-600" : "text-gray-500"}
        />
        <Pill label="As Of" value={data?.as_of_date ?? "—"} />
      </div>

      <Card title="Directional Candidates">
        {isLoading ? (
          <div className="py-12 text-center text-sm text-gray-400">Loading alpha signals…</div>
        ) : error ? (
          <div className="py-12 text-center text-sm text-red-500">
            {(error as Error).message}
          </div>
        ) : signals.length === 0 ? (
          <div className="py-12 text-center text-sm text-gray-400">
            No alpha signals yet. Click <span className="font-medium">Recompute</span> to run the
            first directional scan (this builds features for the full universe and may take a few
            minutes).
          </div>
        ) : (
          <DataTable
            data={signals}
            columns={columns}
            rowKey={(r) => r.ticker}
            searchField={(r) => r.ticker}
            defaultSortKey="alpha_score"
            defaultSortDir="desc"
            defaultPageSize={20}
            expandedRow={(r) => <ExpandedRow r={r} />}
            emptyMessage="No signals match the current filters."
          />
        )}
      </Card>
    </div>
  );
}
