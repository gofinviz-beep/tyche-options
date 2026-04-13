import { useState } from "react";
import { Card } from "@/components/Card";
import { useFilingSignals, useInsiderTransactions } from "@/hooks/useApi";
import {
  ChevronDown,
  ChevronRight,
  ArrowUpCircle,
  ArrowDownCircle,
} from "lucide-react";
import type { FilingSignal, InsiderTransaction } from "@/types";

export function IntelligenceInsider() {
  const { data: signals, isLoading } = useFilingSignals();
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "sells" | "buys" | "cluster">(
    "all",
  );
  const [search, setSearch] = useState("");

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
      </div>
    );
  }

  const withActivity = (signals ?? []).filter(
    (s) => s.insider_sell_count_30d > 0 || s.insider_buy_count_30d > 0,
  );

  const filtered = withActivity
    .filter((s) => {
      if (filter === "sells") return s.insider_sell_count_30d > 0;
      if (filter === "buys") return s.insider_buy_count_30d > 0;
      if (filter === "cluster") return s.insider_cluster_sell;
      return true;
    })
    .filter(
      (s) => !search || s.ticker.toLowerCase().includes(search.toLowerCase()),
    )
    .sort((a, b) => {
      if (a.insider_cluster_sell !== b.insider_cluster_sell)
        return a.insider_cluster_sell ? -1 : 1;
      return b.insider_sell_count_30d - a.insider_sell_count_30d;
    });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Insider Activity</h1>
        <p className="mt-1 text-sm text-gray-500">
          Form 4 insider transactions from SEC EDGAR (30-day window)
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <input
          type="text"
          placeholder="Search ticker..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-48 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <div className="flex gap-1">
          {(
            [
              ["all", "All"],
              ["sells", "Sells"],
              ["buys", "Buys"],
              ["cluster", "Cluster Sells"],
            ] as const
          ).map(([val, label]) => (
            <button
              key={val}
              onClick={() => setFilter(val)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                filter === val
                  ? "bg-gray-900 text-white"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="ml-auto text-xs text-gray-400">
          {filtered.length} ticker{filtered.length !== 1 ? "s" : ""}
        </span>
      </div>

      {filtered.length === 0 ? (
        <Card>
          <p className="py-8 text-center text-sm text-gray-400">
            No insider activity found. Run the EDGAR ingestion pipeline first.
          </p>
        </Card>
      ) : (
        <div className="space-y-2">
          {filtered.map((signal) => (
            <InsiderTickerRow
              key={signal.ticker}
              signal={signal}
              isOpen={selectedTicker === signal.ticker}
              onToggle={() =>
                setSelectedTicker(
                  selectedTicker === signal.ticker ? null : signal.ticker,
                )
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

function InsiderTickerRow({
  signal,
  isOpen,
  onToggle,
}: {
  signal: FilingSignal;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <div className="flex items-center gap-3">
          {isOpen ? (
            <ChevronDown className="h-4 w-4 text-gray-400" />
          ) : (
            <ChevronRight className="h-4 w-4 text-gray-400" />
          )}
          <span className="font-semibold text-gray-900">{signal.ticker}</span>
          {signal.insider_cluster_sell && (
            <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-700">
              cluster sell
            </span>
          )}
        </div>
        <div className="flex items-center gap-4 text-xs">
          {signal.insider_sell_count_30d > 0 && (
            <span className="flex items-center gap-1 text-red-600">
              <ArrowDownCircle className="h-3.5 w-3.5" />
              {signal.insider_sell_count_30d} sell
              {signal.insider_sell_count_30d !== 1 ? "s" : ""}
            </span>
          )}
          {signal.insider_buy_count_30d > 0 && (
            <span className="flex items-center gap-1 text-emerald-600">
              <ArrowUpCircle className="h-3.5 w-3.5" />
              {signal.insider_buy_count_30d} buy
              {signal.insider_buy_count_30d !== 1 ? "s" : ""}
            </span>
          )}
          <span className="font-mono text-gray-500">
            net {signal.insider_net_shares_30d >= 0 ? "+" : ""}
            {formatShares(signal.insider_net_shares_30d)}
          </span>
        </div>
      </button>
      {isOpen && <TransactionList ticker={signal.ticker} />}
    </div>
  );
}

function TransactionList({ ticker }: { ticker: string }) {
  const { data: transactions, isLoading } = useInsiderTransactions(ticker);

  if (isLoading) {
    return (
      <div className="border-t border-gray-100 px-4 py-4 text-center text-sm text-gray-400">
        Loading transactions...
      </div>
    );
  }

  if (!transactions?.length) {
    return (
      <div className="border-t border-gray-100 px-4 py-4 text-center text-sm text-gray-400">
        No insider transactions found for {ticker}
      </div>
    );
  }

  return (
    <div className="border-t border-gray-100 px-4 py-3">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-100 text-left text-gray-500">
              <th className="pb-2 pr-3 font-medium">Date</th>
              <th className="pb-2 pr-3 font-medium">Insider</th>
              <th className="pb-2 pr-3 font-medium">Title</th>
              <th className="pb-2 pr-3 font-medium">Type</th>
              <th className="pb-2 pr-3 text-right font-medium">Shares</th>
              <th className="pb-2 pr-3 text-right font-medium">Price</th>
              <th className="pb-2 text-right font-medium">Value</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((tx, i) => (
              <TransactionRow key={`${tx.accession_no}-${i}`} tx={tx} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TransactionRow({ tx }: { tx: InsiderTransaction }) {
  const isSell = tx.acquisition_or_disposition === "D";
  const date = tx.period_of_report
    ? new Date(tx.period_of_report).toLocaleDateString()
    : tx.filed_at
      ? new Date(tx.filed_at).toLocaleDateString()
      : "";

  return (
    <tr className="border-b border-gray-50 last:border-0">
      <td className="py-2 pr-3 text-gray-500">{date}</td>
      <td className="py-2 pr-3 font-medium text-gray-900">
        {tx.insider_name}
      </td>
      <td className="py-2 pr-3 text-gray-500">{tx.insider_title ?? ""}</td>
      <td className="py-2 pr-3">
        <span
          className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-medium ${
            isSell
              ? "bg-red-50 text-red-600"
              : "bg-emerald-50 text-emerald-600"
          }`}
        >
          {isSell ? (
            <ArrowDownCircle className="h-3 w-3" />
          ) : (
            <ArrowUpCircle className="h-3 w-3" />
          )}
          {isSell ? "Sell" : "Buy"}
        </span>
      </td>
      <td className="py-2 pr-3 text-right font-mono text-gray-900">
        {formatShares(tx.shares)}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-gray-500">
        ${tx.price_per_share.toFixed(2)}
      </td>
      <td className="py-2 text-right font-mono text-gray-900">
        ${formatValue(tx.total_value)}
      </td>
    </tr>
  );
}

function formatShares(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function formatValue(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toFixed(0);
}
