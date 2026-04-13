import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "@/components/Card";
import { useNewsSignals, useNewsArticles } from "@/hooks/useApi";
import { AlertTriangle, ExternalLink, ChevronDown, ChevronRight } from "lucide-react";
import type { NewsSignal, NewsArticle } from "@/types";

export function IntelligenceNews() {
  const { data: signals, isLoading } = useNewsSignals();
  const [searchParams] = useSearchParams();
  const preselected = searchParams.get("ticker");
  const [selectedTicker, setSelectedTicker] = useState<string | null>(preselected);
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
      (s) =>
        !search || s.ticker.toLowerCase().includes(search.toLowerCase()),
    )
    .sort((a, b) => a.news_impact_score - b.news_impact_score);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">News Intelligence</h1>
        <p className="mt-1 text-sm text-gray-500">
          News articles from Polygon and Finnhub with LLM classification
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
            No news signals found. Run the News ingestion pipeline first.
          </p>
        </Card>
      ) : (
        <div className="space-y-2">
          {filtered.map((signal) => (
            <NewsTickerRow
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

function NewsTickerRow({
  signal,
  isOpen,
  onToggle,
}: {
  signal: NewsSignal;
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
            <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
          )}
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-500">
          <span>
            {signal.negative_count_24h} neg / {signal.positive_count_24h} pos
          </span>
          <span
            className={`font-mono ${signal.news_impact_score < 0 ? "text-red-600" : "text-emerald-600"}`}
          >
            {signal.news_impact_score.toFixed(2)}
          </span>
          {signal.dominant_event_type && (
            <span className="rounded bg-gray-100 px-1.5 py-0.5">
              {signal.dominant_event_type.replace(/_/g, " ")}
            </span>
          )}
        </div>
      </button>
      {isOpen && <ArticleList ticker={signal.ticker} />}
    </div>
  );
}

function ArticleList({ ticker }: { ticker: string }) {
  const { data: articles, isLoading } = useNewsArticles(ticker);

  if (isLoading) {
    return (
      <div className="border-t border-gray-100 px-4 py-4 text-center text-sm text-gray-400">
        Loading articles...
      </div>
    );
  }

  if (!articles?.length) {
    return (
      <div className="border-t border-gray-100 px-4 py-4 text-center text-sm text-gray-400">
        No articles found for {ticker}
      </div>
    );
  }

  return (
    <div className="border-t border-gray-100 px-4 py-3">
      <div className="space-y-3">
        {articles.map((article) => (
          <ArticleRow key={article.article_id} article={article} />
        ))}
      </div>
    </div>
  );
}

function ArticleRow({ article }: { article: NewsArticle }) {
  const sentimentColor =
    article.sentiment === "positive"
      ? "text-emerald-600 bg-emerald-50"
      : article.sentiment === "negative"
        ? "text-red-600 bg-red-50"
        : "text-gray-600 bg-gray-50";

  const published = article.published_at
    ? new Date(article.published_at).toLocaleString()
    : "";

  return (
    <div className="rounded-lg bg-gray-50 px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <a
            href={article.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm font-medium text-gray-900 hover:text-blue-600"
          >
            {article.title}
            <ExternalLink className="ml-1 inline h-3 w-3 text-gray-400" />
          </a>
          {article.summary && (
            <p className="mt-0.5 line-clamp-2 text-xs text-gray-500">
              {article.summary}
            </p>
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-gray-400">
            <span>{article.source}</span>
            {article.author && <span>by {article.author}</span>}
            <span>{published}</span>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {article.sentiment && (
            <span
              className={`rounded px-1.5 py-0.5 text-xs font-medium ${sentimentColor}`}
            >
              {article.sentiment}
            </span>
          )}
          {article.impact_score != null && (
            <span className="font-mono text-xs text-gray-500">
              {article.impact_score.toFixed(2)}
            </span>
          )}
          {article.event_type && (
            <span className="rounded bg-blue-50 px-1.5 py-0.5 text-xs text-blue-600">
              {article.event_type.replace(/_/g, " ")}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
