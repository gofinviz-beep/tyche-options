import { useState, useMemo } from "react";
import {
  Compass,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  TrendingDown,
  TrendingUp,
  DollarSign,
  Clock,
  Zap,
} from "lucide-react";
import { Card } from "@/components/Card";
import { DataTable, type DataTableColumn } from "@/components/DataTable";
import { useConvictionScan, useExploreOptions } from "@/hooks/useApi";
import type { ConvictionSignal, ExploreCandidate } from "@/types";

function formatCurrency(n: number) {
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPct(n: number) {
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function TickerChip({
  signal,
  selected,
  onClick,
}: {
  signal: ConvictionSignal;
  selected: boolean;
  onClick: () => void;
}) {
  const isPullback = signal.trend_state.startsWith("pullback");
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-all ${
        selected
          ? isPullback
            ? "border-blue-400 bg-blue-50 text-blue-700"
            : "border-emerald-400 bg-emerald-50 text-emerald-700"
          : "border-gray-200 bg-white text-gray-500 hover:border-gray-300"
      }`}
    >
      <span className="font-semibold">{signal.ticker}</span>
      <span className="text-[10px] opacity-70">
        {formatCurrency(signal.last_close)}
      </span>
    </button>
  );
}

export function Explore() {
  const { data: convictionData, isLoading: convictionLoading } =
    useConvictionScan(undefined, true);
  const explore = useExploreOptions();

  const [selectedTickers, setSelectedTickers] = useState<Set<string>>(
    new Set(),
  );
  const [customTickers, setCustomTickers] = useState("");

  const pullbackEligible = useMemo(
    () =>
      (convictionData?.signals ?? []).filter(
        (s) => s.csp_eligible && s.trend_state.startsWith("pullback"),
      ),
    [convictionData],
  );

  const uptrendEligible = useMemo(
    () =>
      (convictionData?.signals ?? []).filter(
        (s) =>
          s.csp_eligible &&
          (s.trend_state === "strong_uptrend" || s.trend_state === "uptrend"),
      ),
    [convictionData],
  );

  const toggleTicker = (ticker: string) => {
    setSelectedTickers((prev) => {
      const next = new Set(prev);
      if (next.has(ticker)) next.delete(ticker);
      else next.add(ticker);
      return next;
    });
  };

  const selectAll = (signals: ConvictionSignal[]) => {
    setSelectedTickers((prev) => {
      const next = new Set(prev);
      signals.forEach((s) => next.add(s.ticker));
      return next;
    });
  };

  const deselectAll = (signals: ConvictionSignal[]) => {
    setSelectedTickers((prev) => {
      const next = new Set(prev);
      signals.forEach((s) => next.delete(s.ticker));
      return next;
    });
  };

  const handleExplore = () => {
    const custom = customTickers
      .split(/[,\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    const all = [...selectedTickers, ...custom];
    const unique = [...new Set(all)];
    if (unique.length === 0) return;
    explore.mutate({ symbols: unique.join(",") });
  };

  const totalSelected =
    selectedTickers.size +
    customTickers
      .split(/[,\s]+/)
      .filter((s) => s.trim()).length;

  const resultColumns: DataTableColumn<ExploreCandidate>[] = [
    {
      key: "symbol",
      header: "Ticker",
      accessor: (r) => r.symbol,
      sortable: true,
      render: (r) => {
        const isPullback = pullbackEligible.some((s) => s.ticker === r.symbol);
        return (
          <span className="font-semibold">
            <span
              className={`mr-1.5 inline-block h-2 w-2 rounded-full ${isPullback ? "bg-blue-500" : "bg-emerald-500"}`}
            />
            {r.symbol}
          </span>
        );
      },
    },
    {
      key: "strike",
      header: "Strike",
      accessor: (r) => r.strike,
      sortable: true,
      align: "right",
      render: (r) => formatCurrency(r.strike),
    },
    {
      key: "underlying_price",
      header: "Price",
      accessor: (r) => r.underlying_price,
      sortable: true,
      align: "right",
      render: (r) => formatCurrency(r.underlying_price),
    },
    {
      key: "expiration",
      header: "Exp",
      accessor: (r) => r.expiration,
      sortable: true,
      render: (r) => (
        <span className="text-xs text-gray-600">
          {r.expiration} ({r.dte}d)
        </span>
      ),
    },
    {
      key: "bid",
      header: "Bid",
      accessor: (r) => r.bid,
      sortable: true,
      align: "right",
      render: (r) => formatCurrency(r.bid),
    },
    {
      key: "ask",
      header: "Ask",
      accessor: (r) => r.ask,
      sortable: true,
      align: "right",
      render: (r) => formatCurrency(r.ask),
    },
    {
      key: "open_interest",
      header: "OI",
      accessor: (r) => r.open_interest,
      sortable: true,
      align: "right",
      render: (r) => r.open_interest.toLocaleString(),
    },
    {
      key: "delta",
      header: "Delta",
      accessor: (r) => r.delta,
      sortable: true,
      align: "right",
      render: (r) => r.delta.toFixed(3),
    },
    {
      key: "premium_per_contract",
      header: "Premium",
      accessor: (r) => r.premium_per_contract,
      sortable: true,
      align: "right",
      render: (r) => (
        <span className="font-medium text-emerald-700">
          {formatCurrency(r.premium_per_contract)}
        </span>
      ),
    },
    {
      key: "max_contracts",
      header: "Max Qty",
      accessor: (r) => r.max_contracts,
      sortable: true,
      align: "right",
    },
    {
      key: "total_premium",
      header: "Total Prem",
      accessor: (r) => r.total_premium,
      sortable: true,
      align: "right",
      render: (r) => (
        <span className="font-semibold text-emerald-700">
          {formatCurrency(r.total_premium)}
        </span>
      ),
    },
    {
      key: "collateral",
      header: "Collateral",
      accessor: (r) => r.collateral,
      sortable: true,
      align: "right",
      render: (r) => formatCurrency(r.collateral),
    },
    {
      key: "annualized_return_pct",
      header: "Ann. Return",
      accessor: (r) => r.annualized_return_pct,
      sortable: true,
      align: "right",
      render: (r) => (
        <span
          className={`font-semibold ${r.annualized_return_pct >= 50 ? "text-emerald-700" : r.annualized_return_pct >= 20 ? "text-emerald-600" : "text-gray-700"}`}
        >
          {formatPct(r.annualized_return_pct)}
        </span>
      ),
    },
    {
      key: "implied_volatility",
      header: "IV",
      accessor: (r) => r.implied_volatility,
      sortable: true,
      align: "right",
      render: (r) => `${(r.implied_volatility * 100).toFixed(1)}%`,
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
          <Compass className="h-5 w-5 text-indigo-600" />
          Options Explorer
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Select conviction-eligible tickers, see all available put contracts
          for the next expiration. No rigid pipeline — you decide what to trade.
        </p>
      </div>

      {/* Ticker Selection */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title={
            <span className="flex items-center gap-2">
              <TrendingDown className="h-4 w-4 text-blue-600" />
              Path B — Pullback CSPs
              <span className="ml-1 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700">
                {pullbackEligible.length}
              </span>
            </span>
          }
          action={
            <div className="flex gap-2">
              <button
                onClick={() => selectAll(pullbackEligible)}
                className="rounded bg-blue-50 px-2 py-1 text-[10px] font-medium text-blue-700 hover:bg-blue-100"
              >
                Select All
              </button>
              <button
                onClick={() => deselectAll(pullbackEligible)}
                className="rounded bg-gray-50 px-2 py-1 text-[10px] font-medium text-gray-600 hover:bg-gray-100"
              >
                Clear
              </button>
            </div>
          }
        >
          {convictionLoading ? (
            <div className="flex items-center gap-2 py-4 text-sm text-gray-400">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading conviction
              data...
            </div>
          ) : pullbackEligible.length === 0 ? (
            <p className="text-sm text-gray-400">No pullback CSP-eligible tickers</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {pullbackEligible.map((s) => (
                <TickerChip
                  key={s.ticker}
                  signal={s}
                  selected={selectedTickers.has(s.ticker)}
                  onClick={() => toggleTicker(s.ticker)}
                />
              ))}
            </div>
          )}
        </Card>

        <Card
          title={
            <span className="flex items-center gap-2">
              <TrendingUp className="h-4 w-4 text-emerald-600" />
              Path A — Uptrend CSPs
              <span className="ml-1 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700">
                {uptrendEligible.length}
              </span>
            </span>
          }
          action={
            <div className="flex gap-2">
              <button
                onClick={() => selectAll(uptrendEligible)}
                className="rounded bg-emerald-50 px-2 py-1 text-[10px] font-medium text-emerald-700 hover:bg-emerald-100"
              >
                Select All
              </button>
              <button
                onClick={() => deselectAll(uptrendEligible)}
                className="rounded bg-gray-50 px-2 py-1 text-[10px] font-medium text-gray-600 hover:bg-gray-100"
              >
                Clear
              </button>
            </div>
          }
        >
          {convictionLoading ? (
            <div className="flex items-center gap-2 py-4 text-sm text-gray-400">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading...
            </div>
          ) : uptrendEligible.length === 0 ? (
            <p className="text-sm text-gray-400">No uptrend CSP-eligible tickers</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {uptrendEligible.map((s) => (
                <TickerChip
                  key={s.ticker}
                  signal={s}
                  selected={selectedTickers.has(s.ticker)}
                  onClick={() => toggleTicker(s.ticker)}
                />
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* Custom tickers + Explore button */}
      <Card>
        <div className="flex items-center gap-4">
          <input
            type="text"
            value={customTickers}
            onChange={(e) => setCustomTickers(e.target.value)}
            placeholder="Add custom tickers: AAPL, MSFT, GOOG ..."
            className="flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-indigo-400 focus:outline-none"
            onKeyDown={(e) => e.key === "Enter" && handleExplore()}
          />
          <button
            onClick={handleExplore}
            disabled={totalSelected === 0 || explore.isPending}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {explore.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Zap className="h-4 w-4" />
            )}
            Explore Options
            {totalSelected > 0 && (
              <span className="rounded bg-indigo-500 px-1.5 py-0.5 text-[10px]">
                {totalSelected}
              </span>
            )}
          </button>
        </div>
      </Card>

      {/* Results */}
      {explore.isError && (
        <Card>
          <div className="flex items-center gap-2 text-sm text-red-600">
            <AlertTriangle className="h-4 w-4" />
            {(explore.error as Error).message}
          </div>
        </Card>
      )}

      {explore.data && (
        <>
          {/* Summary */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
            {[
              {
                label: "Tickers w/ Options",
                value: `${explore.data.symbols_with_options} / ${explore.data.symbols_requested}`,
                icon: CheckCircle2,
                color: "text-emerald-600",
              },
              {
                label: "Contracts Found",
                value: explore.data.total_contracts.toString(),
                icon: DollarSign,
                color: "text-indigo-600",
              },
              {
                label: "Target Expiration",
                value: explore.data.expiration ?? "—",
                icon: Clock,
                color: "text-amber-600",
              },
              {
                label: "Capital",
                value: formatCurrency(explore.data.available_capital),
                icon: DollarSign,
                color: "text-gray-600",
              },
              {
                label: "Duration",
                value: `${explore.data.duration_ms.toFixed(0)}ms`,
                icon: Zap,
                color: explore.data.duration_ms < 1000 ? "text-emerald-600" : "text-amber-600",
              },
              {
                label: "Cached Chains",
                value: explore.data.broker_cache.chains?.toString() ?? "—",
                icon: Zap,
                color: "text-blue-600",
              },
            ].map((stat) => (
              <div
                key={stat.label}
                className="rounded-lg border border-gray-100 bg-white px-3 py-2.5 shadow-sm"
              >
                <div className="flex items-center gap-1.5">
                  <stat.icon className={`h-3.5 w-3.5 ${stat.color}`} />
                  <span className="text-[10px] font-medium uppercase tracking-wide text-gray-400">
                    {stat.label}
                  </span>
                </div>
                <p className={`mt-1 text-sm font-bold ${stat.color}`}>
                  {stat.value}
                </p>
              </div>
            ))}
          </div>

          {explore.data.errors.length > 0 && (
            <Card
              title={
                <span className="flex items-center gap-2 text-amber-600">
                  <AlertTriangle className="h-4 w-4" />
                  {explore.data.errors.length} ticker(s) had issues
                </span>
              }
            >
              <ul className="space-y-1 text-xs text-gray-500">
                {explore.data.errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </Card>
          )}

          {/* Main results table */}
          <Card
            title={
              <span className="flex items-center gap-2">
                <DollarSign className="h-4 w-4 text-emerald-600" />
                Put Options — {explore.data.expiration ?? ""}
                <span className="ml-1 text-xs font-normal text-gray-400">
                  {explore.data.total_contracts} contracts across{" "}
                  {explore.data.symbols_with_options} tickers
                </span>
              </span>
            }
            subtitle="Sorted by annualized return. Blue dot = pullback (Path B), green dot = uptrend (Path A)."
          >
            {explore.data.candidates.length === 0 ? (
              <p className="py-8 text-center text-sm text-gray-400">
                No put contracts found for the selected tickers.
              </p>
            ) : (
              <DataTable
                data={explore.data.candidates}
                columns={resultColumns}
                rowKey={(r) => r.option_symbol}
                searchField={(r) => r.symbol}
                defaultSortKey="annualized_return_pct"
                defaultSortDir="desc"
                defaultPageSize={20}
              />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
