import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "@/components/Card";
import { useFilingSignals, useFiling8K } from "@/hooks/useApi";
import {
  FileWarning,
  ExternalLink,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import type { FilingSignal, Filing8K } from "@/types";

export function IntelligenceFilings() {
  const { data: signals, isLoading } = useFilingSignals();
  const [searchParams] = useSearchParams();
  const preselected = searchParams.get("ticker");
  const [selectedTicker, setSelectedTicker] = useState<string | null>(
    preselected,
  );
  const [filterRiskOnly, setFilterRiskOnly] = useState(false);
  const [search, setSearch] = useState("");

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
      </div>
    );
  }

  const filtered = (signals ?? [])
    .filter((s) => !filterRiskOnly || s.has_risk)
    .filter(
      (s) => !search || s.ticker.toLowerCase().includes(search.toLowerCase()),
    )
    .sort((a, b) => {
      if (a.has_risk !== b.has_risk) return a.has_risk ? -1 : 1;
      return b.eightk_count_30d - a.eightk_count_30d;
    });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">SEC Filings</h1>
        <p className="mt-1 text-sm text-gray-500">
          8-K filings from SEC EDGAR with LLM-based event classification
        </p>
      </div>

      <div className="flex items-center gap-3">
        <input
          type="text"
          placeholder="Search ticker..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-48 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <label className="flex items-center gap-1.5 text-sm text-gray-600">
          <input
            type="checkbox"
            checked={filterRiskOnly}
            onChange={(e) => setFilterRiskOnly(e.target.checked)}
            className="rounded border-gray-300"
          />
          Risk only
        </label>
        <span className="ml-auto text-xs text-gray-400">
          {filtered.length} ticker{filtered.length !== 1 ? "s" : ""}
        </span>
      </div>

      {filtered.length === 0 ? (
        <Card>
          <p className="py-8 text-center text-sm text-gray-400">
            No filing signals found. Run the EDGAR ingestion pipeline first.
          </p>
        </Card>
      ) : (
        <div className="space-y-2">
          {filtered.map((signal) => (
            <FilingTickerRow
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

function FilingTickerRow({
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
          {signal.has_risk && (
            <FileWarning className="h-3.5 w-3.5 text-red-500" />
          )}
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-500">
          {signal.eightk_count_30d > 0 && (
            <span>{signal.eightk_count_30d} 8-K</span>
          )}
          {signal.last_8k_sentiment && (
            <span
              className={`rounded px-1.5 py-0.5 ${
                signal.last_8k_sentiment === "negative"
                  ? "bg-red-50 text-red-600"
                  : signal.last_8k_sentiment === "positive"
                    ? "bg-emerald-50 text-emerald-600"
                    : "bg-gray-100 text-gray-600"
              }`}
            >
              {signal.last_8k_sentiment}
            </span>
          )}
          {signal.insider_cluster_sell && (
            <span className="rounded bg-red-100 px-1.5 py-0.5 font-medium text-red-700">
              cluster sell
            </span>
          )}
        </div>
      </button>
      {isOpen && <Filing8KList ticker={signal.ticker} />}
    </div>
  );
}

function Filing8KList({ ticker }: { ticker: string }) {
  const { data: filings, isLoading } = useFiling8K(ticker);

  if (isLoading) {
    return (
      <div className="border-t border-gray-100 px-4 py-4 text-center text-sm text-gray-400">
        Loading 8-K filings...
      </div>
    );
  }

  if (!filings?.length) {
    return (
      <div className="border-t border-gray-100 px-4 py-4 text-center text-sm text-gray-400">
        No 8-K filings found for {ticker}
      </div>
    );
  }

  return (
    <div className="border-t border-gray-100 px-4 py-3">
      <div className="space-y-3">
        {filings.map((filing) => (
          <Filing8KRow key={filing.accession_no} filing={filing} />
        ))}
      </div>
    </div>
  );
}

function Filing8KRow({ filing }: { filing: Filing8K }) {
  const sentimentColor =
    filing.sentiment === "positive"
      ? "text-emerald-600 bg-emerald-50"
      : filing.sentiment === "negative"
        ? "text-red-600 bg-red-50"
        : "text-gray-600 bg-gray-50";

  const filed = filing.filed_at
    ? new Date(filing.filed_at).toLocaleDateString()
    : "";

  return (
    <div className="rounded-lg bg-gray-50 px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-900">
              {filing.form_type}
            </span>
            {filing.filing_url && (
              <a
                href={filing.filing_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-500 hover:text-blue-600"
              >
                <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>
          {filing.description && (
            <p className="mt-0.5 text-xs text-gray-600">{filing.description}</p>
          )}
          {filing.items_reported && (
            <p className="mt-0.5 text-xs text-gray-400">
              Items: {filing.items_reported}
            </p>
          )}
          {filing.content_summary && (
            <p className="mt-1 line-clamp-2 text-xs text-gray-500">
              {filing.content_summary}
            </p>
          )}
          <p className="mt-1 text-xs text-gray-400">Filed: {filed}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {filing.sentiment && (
            <span
              className={`rounded px-1.5 py-0.5 text-xs font-medium ${sentimentColor}`}
            >
              {filing.sentiment}
            </span>
          )}
          {filing.impact_score != null && (
            <span className="font-mono text-xs text-gray-500">
              {filing.impact_score.toFixed(2)}
            </span>
          )}
          {filing.event_type && (
            <span className="rounded bg-blue-50 px-1.5 py-0.5 text-xs text-blue-600">
              {filing.event_type.replace(/_/g, " ")}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
