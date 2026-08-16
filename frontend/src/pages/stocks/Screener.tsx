import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Gem, Download, Info } from "lucide-react";
import { Card } from "@/components/Card";
import { DataTable, type DataTableColumn } from "@/components/DataTable";
import { useScreener } from "@/hooks/useApi";
import { formatMarketCap } from "@/lib/format";
import type { ScreenerParams, ScreenerRow } from "@/types";

const MIN_CAP_STORAGE_KEY = "tyche_screener_min_market_cap_m";

function loadMinCap(defaultValue: number): number {
  const raw =
    typeof window !== "undefined" ? window.localStorage.getItem(MIN_CAP_STORAGE_KEY) : null;
  const parsed = raw != null ? Number(raw) : NaN;
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

const SETUP_LABEL_META: Record<
  string,
  { className: string }
> = {
  "Prime Pullback": { className: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  "Structural Uptrend": { className: "bg-blue-50 text-blue-700 border-blue-200" },
  "Emerging Breakout": { className: "bg-violet-50 text-violet-700 border-violet-200" },
  "Overextended": { className: "bg-red-50 text-red-700 border-red-200" },
  "Weak Structure": { className: "bg-red-50 text-red-700 border-red-200" },
  "Watch / Base Building": { className: "bg-gray-100 text-gray-500 border-gray-200" },
};

function SetupBadge({ label }: { label: string }) {
  const meta = SETUP_LABEL_META[label] ?? SETUP_LABEL_META["Watch / Base Building"];
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium ${meta.className}`}
    >
      {label}
    </span>
  );
}

const SETUP_LABEL_OPTIONS = [
  { value: "Prime Pullback", label: "Prime Pullback" },
  { value: "Structural Uptrend", label: "Structural Uptrend" },
  { value: "Emerging Breakout", label: "Emerging Breakout" },
  { value: "Overextended", label: "Overextended" },
  { value: "Weak Structure", label: "Weak Structure" },
  { value: "Watch / Base Building", label: "Watch / Base Building" },
];

const SECTOR_OPTIONS = [
  { value: "Communication Services", label: "Comm Svc" },
  { value: "Consumer Discretionary", label: "Cons Disc" },
  { value: "Consumer Staples", label: "Cons Stpl" },
  { value: "Energy", label: "Energy" },
  { value: "Financials", label: "Financials" },
  { value: "Health Care", label: "Health Care" },
  { value: "Industrials", label: "Industrials" },
  { value: "Information Technology", label: "Info Tech" },
  { value: "Materials", label: "Materials" },
  { value: "Real Estate", label: "Real Est" },
  { value: "Utilities", label: "Utilities" },
];

const RSI_RANGE_OPTIONS = [
  { value: "30", label: "30" },
  { value: "40", label: "40" },
  { value: "45", label: "45" },
  { value: "50", label: "50" },
  { value: "55", label: "55" },
  { value: "58", label: "58" },
  { value: "60", label: "60" },
  { value: "65", label: "65" },
  { value: "70", label: "70" },
];

interface Recipe {
  id: string;
  label: string;
  hint: string;
  speculative?: boolean;
  params: ScreenerParams;
}

const BASE_LIMIT = 200;

const RECIPES: Recipe[] = [
  {
    id: "prime_pullback",
    label: "Diamond — Prime Pullback",
    hint: "Highest conviction: strong quarterly structure (RSI ≥ 58) cooled to a 35-52 daily RSI buy zone, above the 200-SMA. The core \"buy strength on a dip\" setup.",
    params: {
      setup_label: "Prime Pullback",
      q_rsi_min: 58,
      d_rsi_min: 35,
      d_rsi_max: 52,
      above_sma200: true,
      stack_score_min: 2,
      ext_max_pct: 6,
      min_market_cap_millions: 4000,
      sort: "setup_score",
      desc: true,
      limit: BASE_LIMIT,
    },
  },
  {
    id: "structural_breakout",
    label: "Structural Breakout Pulling Back",
    hint: "Confirmed structural uptrend (quarterly RSI ≥ 55) pulling back to a 35-55 daily RSI zone, above the 200-SMA. Wider net than Prime Pullback.",
    params: {
      q_rsi_min: 55,
      d_rsi_min: 35,
      d_rsi_max: 55,
      above_sma200: true,
      ext_max_pct: 8,
      min_market_cap_millions: 1000,
      sort: "setup_score",
      desc: true,
      limit: BASE_LIMIT,
    },
  },
  {
    id: "emerging_breakout",
    label: "Emerging Breakout (early)",
    hint: "Catches the quarterly-RSI 50→60 regime change before it's obvious — earlier and riskier than the confirmed structural setups.",
    params: {
      q_rsi_min: 50,
      q_rsi_max: 60,
      d_rsi_min: 40,
      d_rsi_max: 60,
      above_sma200: true,
      min_market_cap_millions: 1000,
      sort: "setup_score",
      desc: true,
      limit: BASE_LIMIT,
    },
  },
  {
    id: "deep_reversal",
    label: "Deep Reversal (higher risk)",
    hint: "Speculative reclaim plays — quarterly RSI 40-55 (basing, not yet confirmed) with an oversold daily RSI (≤ 35). Pair with a catalyst check on the Deep Dive page.",
    speculative: true,
    params: {
      q_rsi_min: 40,
      q_rsi_max: 55,
      d_rsi_max: 35,
      min_market_cap_millions: 1000,
      sort: "setup_score",
      desc: true,
      limit: BASE_LIMIT,
    },
  },
  {
    id: "avoid_list",
    label: "Show me what to AVOID",
    hint: "Teaching tool: Overextended (daily RSI spike on weak structure) and Weak Structure (value-trap risk) names — a short-avoid list, not a buy list.",
    params: {
      setup_label: "Overextended,Weak Structure",
      min_market_cap_millions: 1000,
      sort: "setup_score",
      desc: false,
      limit: BASE_LIMIT,
    },
  },
];

const DEFAULT_RECIPE = RECIPES[0];

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

function buildColumns(): DataTableColumn<ScreenerRow>[] {
  return [
    {
      key: "ticker",
      header: "Ticker",
      accessor: (r) => r.ticker,
      sortable: true,
      render: (r) => (
        <Link
          to={`/stocks/deep-dive?ticker=${encodeURIComponent(r.ticker)}`}
          className="font-semibold text-indigo-700 hover:text-indigo-900 hover:underline"
          title={`Open ${r.ticker} in Deep Dive`}
        >
          {r.ticker}
        </Link>
      ),
    },
    {
      key: "setup_label",
      header: "Setup",
      accessor: (r) => r.setup_label,
      sortable: true,
      render: (r) => <SetupBadge label={r.setup_label} />,
      filter: {
        type: "multiselect",
        options: SETUP_LABEL_OPTIONS,
      },
    },
    {
      key: "setup_score",
      header: "Score",
      align: "right",
      sortable: true,
      accessor: (r) => r.setup_score,
      render: (r) => (
        <span className="font-bold tabular-nums text-gray-900">{r.setup_score.toFixed(0)}</span>
      ),
      filter: {
        type: "min",
        minOptions: [
          { value: "50", label: "50" },
          { value: "60", label: "60" },
          { value: "70", label: "70" },
          { value: "80", label: "80" },
        ],
      },
    },
    {
      key: "rsi_quarterly",
      header: "Q-RSI",
      align: "right",
      sortable: true,
      accessor: (r) => r.rsi_quarterly,
      render: (r) => <span className="tabular-nums text-gray-700">{r.rsi_quarterly.toFixed(0)}</span>,
      filter: { type: "range", minOptions: RSI_RANGE_OPTIONS, maxOptions: RSI_RANGE_OPTIONS },
    },
    {
      key: "rsi_monthly",
      header: "M-RSI",
      align: "right",
      sortable: true,
      accessor: (r) => r.rsi_monthly,
      render: (r) => <span className="tabular-nums text-gray-700">{r.rsi_monthly.toFixed(0)}</span>,
      filter: { type: "range", minOptions: RSI_RANGE_OPTIONS, maxOptions: RSI_RANGE_OPTIONS },
    },
    {
      key: "rsi_weekly",
      header: "W-RSI",
      align: "right",
      sortable: true,
      accessor: (r) => r.rsi_weekly,
      render: (r) => <span className="tabular-nums text-gray-700">{r.rsi_weekly.toFixed(0)}</span>,
      filter: { type: "range", minOptions: RSI_RANGE_OPTIONS, maxOptions: RSI_RANGE_OPTIONS },
    },
    {
      key: "rsi_daily",
      header: "D-RSI",
      align: "right",
      sortable: true,
      accessor: (r) => r.rsi_daily,
      render: (r) => {
        const v = r.rsi_daily;
        const color = v >= 70 ? "text-red-600" : v <= 35 ? "text-emerald-600" : "text-gray-700";
        return <span className={`tabular-nums ${color}`}>{v.toFixed(0)}</span>;
      },
      filter: { type: "range", minOptions: RSI_RANGE_OPTIONS, maxOptions: RSI_RANGE_OPTIONS },
    },
    {
      key: "stack_score",
      header: "Stack",
      align: "right",
      sortable: true,
      accessor: (r) => r.stack_score,
      render: (r) => <span className="tabular-nums text-gray-600">{r.stack_score}/3</span>,
      filter: {
        type: "min",
        minOptions: [
          { value: "1", label: "1" },
          { value: "2", label: "2" },
          { value: "3", label: "3" },
        ],
      },
    },
    {
      key: "pct_vs_ema_8",
      header: "% vs 8-EMA",
      align: "right",
      sortable: true,
      accessor: (r) => r.pct_vs_ema_8,
      render: (r) => {
        const positive = r.pct_vs_ema_8 >= 0;
        return (
          <span className={`tabular-nums ${positive ? "text-emerald-600" : "text-red-500"}`}>
            {pct(r.pct_vs_ema_8)}
          </span>
        );
      },
      filter: {
        type: "max",
        maxOptions: [
          { value: "3", label: "3%" },
          { value: "6", label: "6%" },
          { value: "8", label: "8%" },
          { value: "12", label: "12%" },
        ],
      },
    },
    {
      key: "above_sma_200",
      header: "Above 200-SMA",
      align: "center",
      sortable: true,
      accessor: (r) => r.above_sma_200,
      render: (r) =>
        r.above_sma_200 ? (
          <span className="text-emerald-600">Yes</span>
        ) : (
          <span className="text-gray-400">No</span>
        ),
      filter: {
        type: "boolean",
        options: [{ value: "true", label: "Yes" }, { value: "false", label: "No" }],
      },
    },
    {
      key: "ret_3m",
      header: "3M Return",
      align: "right",
      sortable: true,
      accessor: (r) => r.ret_3m ?? null,
      render: (r) =>
        r.ret_3m == null ? (
          <span className="text-xs text-gray-400">—</span>
        ) : (
          <span className={`tabular-nums ${r.ret_3m >= 0 ? "text-emerald-600" : "text-red-500"}`}>
            {pct(r.ret_3m)}
          </span>
        ),
    },
    {
      key: "ret_6m",
      header: "6M Return",
      align: "right",
      sortable: true,
      accessor: (r) => r.ret_6m ?? null,
      render: (r) =>
        r.ret_6m == null ? (
          <span className="text-xs text-gray-400">—</span>
        ) : (
          <span className={`tabular-nums ${r.ret_6m >= 0 ? "text-emerald-600" : "text-red-500"}`}>
            {pct(r.ret_6m)}
          </span>
        ),
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
          <span className="tabular-nums text-gray-600">{r.pct_off_52w_high.toFixed(1)}%</span>
        ),
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
      /* Unlike the other percent columns here, this arrives as a 0-1 fraction. */
      accessor: (r) => r.institutional_pct ?? null,
      render: (r) =>
        r.institutional_pct == null ? (
          <span className="text-xs text-gray-400">—</span>
        ) : (
          <span className="tabular-nums text-gray-600">
            {(r.institutional_pct * 100).toFixed(0)}%
          </span>
        ),
      filter: {
        type: "min",
        minOptions: [
          { value: "0.3", label: "30%" },
          { value: "0.5", label: "50%" },
          { value: "0.7", label: "70%" },
        ],
      },
    },
    {
      key: "sector",
      header: "Sector",
      accessor: (r) => r.sector ?? "",
      sortable: true,
      render: (r) =>
        r.sector ? (
          <span className="truncate text-xs text-gray-500" title={r.sector}>
            {r.sector}
          </span>
        ) : (
          <span className="text-xs text-gray-300">—</span>
        ),
      filter: {
        type: "multiselect",
        options: SECTOR_OPTIONS,
      },
    },
  ];
}

function exportToExcel(rows: ScreenerRow[], asOfDate: string | null) {
  const headers = [
    "Ticker", "Setup", "Score", "Q-RSI", "M-RSI", "W-RSI", "D-RSI", "Stack",
    "% vs 8-EMA", "Above 200-SMA", "3M Return (%)", "6M Return (%)",
    "Off 52w High (%)", "Price", "Market Cap", "Inst Own (%)", "Sector",
  ];
  const rowsOut = rows.map((r) => [
    r.ticker,
    r.setup_label,
    r.setup_score.toFixed(1),
    r.rsi_quarterly.toFixed(1),
    r.rsi_monthly.toFixed(1),
    r.rsi_weekly.toFixed(1),
    r.rsi_daily.toFixed(1),
    String(r.stack_score),
    r.pct_vs_ema_8.toFixed(1),
    r.above_sma_200 ? "Yes" : "No",
    r.ret_3m != null ? r.ret_3m.toFixed(1) : "",
    r.ret_6m != null ? r.ret_6m.toFixed(1) : "",
    r.pct_off_52w_high != null ? r.pct_off_52w_high.toFixed(1) : "",
    r.last_close.toFixed(2),
    r.market_cap?.toFixed(0) ?? "",
    r.institutional_pct != null ? (r.institutional_pct * 100).toFixed(0) : "",
    r.sector ?? "",
  ]);

  const escape = (v: string) => {
    if (v.includes("\t") || v.includes('"') || v.includes("\n")) return `"${v.replace(/"/g, '""')}"`;
    return v;
  };
  const tsv = [headers.map(escape).join("\t"), ...rowsOut.map((r) => r.map(escape).join("\t"))].join(
    "\n",
  );
  const bom = "\uFEFF";
  const blob = new Blob([bom + tsv], { type: "text/tab-separated-values;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const dateStr = asOfDate ?? new Date().toISOString().slice(0, 10);
  a.download = `tyche_screener_${dateStr}.tsv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function Pill({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-4 py-2">
      <div className="text-xs text-gray-400">{label}</div>
      <div className={`text-lg font-bold ${accent ?? "text-gray-900"}`}>{value}</div>
    </div>
  );
}

export function Screener() {
  const [activeRecipeId, setActiveRecipeId] = useState<string>(DEFAULT_RECIPE.id);
  const [filters, setFilters] = useState<ScreenerParams>(() => ({
    ...DEFAULT_RECIPE.params,
    min_market_cap_millions: loadMinCap(DEFAULT_RECIPE.params.min_market_cap_millions ?? 1000),
  }));

  const { data, isLoading, error } = useScreener(filters);
  const columns = useMemo(() => buildColumns(), []);
  const rows = data?.rows ?? [];
  const activeRecipe = RECIPES.find((r) => r.id === activeRecipeId) ?? null;

  const handleRecipeClick = (recipe: Recipe) => {
    setActiveRecipeId(recipe.id);
    setFilters((prev) => ({
      ...recipe.params,
      min_market_cap_millions:
        recipe.params.min_market_cap_millions ?? prev.min_market_cap_millions ?? 1000,
    }));
    if (recipe.params.min_market_cap_millions != null && typeof window !== "undefined") {
      window.localStorage.setItem(
        MIN_CAP_STORAGE_KEY,
        String(recipe.params.min_market_cap_millions),
      );
    }
  };

  const handleMinCapChange = (value: number) => {
    setFilters((prev) => ({ ...prev, min_market_cap_millions: value }));
    if (typeof window !== "undefined") {
      window.localStorage.setItem(MIN_CAP_STORAGE_KEY, String(value));
    }
  };

  const handleClear = () => {
    setActiveRecipeId("custom");
    setFilters((prev) => ({
      min_market_cap_millions: prev.min_market_cap_millions,
      sort: "setup_score",
      desc: true,
      limit: BASE_LIMIT,
    }));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-gray-900">
            <Gem className="h-6 w-6 text-indigo-600" />
            Stock Screener
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-gray-500">
            Universe-wide "Diamond Finder" — buy strong stocks in confirmed structural uptrends
            when they pull back to support, not extended and not a falling knife. High quarterly
            RSI + cooled daily RSI + price on/near the 8/21-EMA + above the 200-SMA is the
            backtest-validated setup. Click a ticker to confirm the thesis on its Deep Dive.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-600">
            <span className="whitespace-nowrap text-gray-500">Min Mkt Cap</span>
            <select
              value={filters.min_market_cap_millions ?? 1000}
              onChange={(e) => handleMinCapChange(Number(e.target.value))}
              className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-800 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              {[250, 500, 1000, 2000, 4000, 10000].map((v) => (
                <option key={v} value={v}>
                  {v >= 1000 ? `$${v / 1000}B+` : `$${v}M+`}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => exportToExcel(rows, data?.as_of_date ?? null)}
            disabled={rows.length === 0}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            title="Export current rows to CSV (opens in Excel)"
          >
            <Download className="h-4 w-4" />
            Export
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {RECIPES.map((recipe) => (
          <button
            key={recipe.id}
            type="button"
            onClick={() => handleRecipeClick(recipe)}
            title={recipe.hint}
            className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
              activeRecipeId === recipe.id
                ? recipe.speculative
                  ? "border-amber-300 bg-amber-50 text-amber-700"
                  : "border-indigo-300 bg-indigo-50 text-indigo-700"
                : "border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50"
            }`}
          >
            {recipe.label}
          </button>
        ))}
        <button
          type="button"
          onClick={handleClear}
          className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
            activeRecipeId === "custom"
              ? "border-indigo-300 bg-indigo-50 text-indigo-700"
              : "border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50"
          }`}
        >
          Custom / All
        </button>
      </div>

      {activeRecipe && (
        <div className="flex items-start gap-2 rounded-lg border border-gray-200 bg-gray-50 px-4 py-2 text-xs text-gray-500">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gray-400" />
          <span>
            {activeRecipe.hint}
            {activeRecipe.speculative && (
              <span className="ml-1 font-medium text-amber-600">Speculative — pair with a Deep Dive catalyst check.</span>
            )}
          </span>
        </div>
      )}

      <div className="flex flex-wrap gap-3">
        <Pill label="Matched" value={String(data?.total ?? 0)} />
        <Pill label="As Of" value={data?.as_of_date ?? "—"} />
        {data?.stale && <Pill label="Status" value="Stale / Empty" accent="text-amber-600" />}
      </div>

      <Card title="Screener Results">
        {isLoading ? (
          <div className="py-12 text-center text-sm text-gray-400">Loading screener index…</div>
        ) : error ? (
          <div className="py-12 text-center text-sm text-red-500">{(error as Error).message}</div>
        ) : rows.length === 0 ? (
          <div className="py-12 text-center text-sm text-gray-400">
            No tickers match the current recipe/filters yet. The nightly screener batch may not
            have run — try widening the recipe or the Min Mkt Cap filter.
          </div>
        ) : (
          <DataTable
            data={rows}
            columns={columns}
            rowKey={(r) => r.ticker}
            searchField={(r) => r.ticker}
            defaultSortKey="setup_score"
            defaultSortDir="desc"
            defaultPageSize={20}
            emptyMessage="No rows match the current filters."
          />
        )}
      </Card>
    </div>
  );
}
