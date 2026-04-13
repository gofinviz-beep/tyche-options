import { useState } from "react";
import { Card } from "@/components/Card";
import {
  useNewsSignals,
  useFilingSignals,
  useTriggerNewsIngest,
  useTriggerEdgarIngest,
} from "@/hooks/useApi";
import { AlertTriangle, FileWarning, RefreshCw, Newspaper, FileText, Users } from "lucide-react";
import { Link } from "react-router-dom";
import type { NewsSignal, FilingSignal } from "@/types";

export function IntelligenceDashboard() {
  const { data: newsSignals, isLoading: newsLoading } = useNewsSignals();
  const { data: filingSignals, isLoading: filingsLoading } = useFilingSignals();
  const triggerNews = useTriggerNewsIngest();
  const triggerEdgar = useTriggerEdgarIngest();
  const [newsTriggered, setNewsTriggered] = useState(false);
  const [edgarTriggered, setEdgarTriggered] = useState(false);

  const isLoading = newsLoading || filingsLoading;

  const newsRiskSignals = newsSignals?.filter((s) => s.has_risk) ?? [];
  const filingRiskSignals = filingSignals?.filter((s) => s.has_risk) ?? [];
  const insiderSells = filingSignals?.filter((s) => s.insider_sell_count_30d > 0) ?? [];
  const insiderBuys = filingSignals?.filter((s) => s.insider_buy_count_30d > 0) ?? [];
  const clusterSells = filingSignals?.filter((s) => s.insider_cluster_sell) ?? [];

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
      </div>
    );
  }

  const hasNoData = !newsSignals?.length && !filingSignals?.length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Intelligence</h1>
          <p className="mt-1 text-sm text-gray-500">
            News articles, SEC filings, and insider activity across the universe
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => {
              triggerNews.mutate();
              setNewsTriggered(true);
            }}
            disabled={triggerNews.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            <Newspaper className="h-4 w-4" />
            {newsTriggered ? "News Running..." : "Ingest News"}
          </button>
          <button
            onClick={() => {
              triggerEdgar.mutate();
              setEdgarTriggered(true);
            }}
            disabled={triggerEdgar.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700 disabled:opacity-50"
          >
            <FileText className="h-4 w-4" />
            {edgarTriggered ? "EDGAR Running..." : "Ingest EDGAR"}
          </button>
        </div>
      </div>

      {(newsTriggered || edgarTriggered) && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700">
          <RefreshCw className="mr-1.5 inline h-4 w-4 animate-spin" />
          Pipeline running in background. Refresh the page in a few minutes to see results.
        </div>
      )}

      {hasNoData && !newsTriggered && !edgarTriggered && (
        <Card>
          <div className="py-8 text-center">
            <p className="text-gray-500">No intelligence data yet.</p>
            <p className="mt-1 text-sm text-gray-400">
              Click "Ingest News" or "Ingest EDGAR" above to start pulling data.
              The first run may take several minutes.
            </p>
          </div>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="News Risk Alerts"
          value={newsRiskSignals.length}
          icon={<AlertTriangle className="h-5 w-5 text-amber-500" />}
          color="amber"
          link="/intelligence/news"
        />
        <StatCard
          label="Filing Risk Alerts"
          value={filingRiskSignals.length}
          icon={<FileWarning className="h-5 w-5 text-red-500" />}
          color="red"
          link="/intelligence/filings"
        />
        <StatCard
          label="Insider Cluster Sells"
          value={clusterSells.length}
          icon={<Users className="h-5 w-5 text-red-600" />}
          color="red"
          link="/intelligence/insider"
        />
        <StatCard
          label="Insider Buys (30d)"
          value={insiderBuys.length}
          icon={<Users className="h-5 w-5 text-emerald-500" />}
          color="emerald"
          link="/intelligence/insider"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card
          title="News Risk Signals"
          subtitle={`${newsSignals?.length ?? 0} tickers with news data`}
          action={
            <Link
              to="/intelligence/news"
              className="text-xs font-medium text-blue-600 hover:text-blue-700"
            >
              View all
            </Link>
          }
        >
          {newsRiskSignals.length === 0 ? (
            <p className="py-4 text-center text-sm text-gray-400">
              No active news risk signals
            </p>
          ) : (
            <div className="space-y-2">
              {newsRiskSignals.slice(0, 8).map((s) => (
                <NewsSignalRow key={s.ticker} signal={s} />
              ))}
              {newsRiskSignals.length > 8 && (
                <p className="pt-1 text-center text-xs text-gray-400">
                  +{newsRiskSignals.length - 8} more
                </p>
              )}
            </div>
          )}
        </Card>

        <Card
          title="Filing & Insider Alerts"
          subtitle={`${filingSignals?.length ?? 0} tickers with filing data`}
          action={
            <Link
              to="/intelligence/filings"
              className="text-xs font-medium text-blue-600 hover:text-blue-700"
            >
              View all
            </Link>
          }
        >
          {filingRiskSignals.length === 0 && insiderSells.length === 0 ? (
            <p className="py-4 text-center text-sm text-gray-400">
              No active filing or insider alerts
            </p>
          ) : (
            <div className="space-y-2">
              {filingRiskSignals.slice(0, 8).map((s) => (
                <FilingSignalRow key={s.ticker} signal={s} />
              ))}
              {filingRiskSignals.length > 8 && (
                <p className="pt-1 text-center text-xs text-gray-400">
                  +{filingRiskSignals.length - 8} more
                </p>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  icon,
  color,
  link,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
  color: string;
  link: string;
}) {
  const bg =
    color === "amber"
      ? "bg-amber-50"
      : color === "red"
        ? "bg-red-50"
        : "bg-emerald-50";
  return (
    <Link to={link}>
      <div
        className={`rounded-xl border border-gray-200 ${bg} p-4 transition-shadow hover:shadow-md`}
      >
        <div className="flex items-center justify-between">
          {icon}
          <span className="text-2xl font-bold text-gray-900">{value}</span>
        </div>
        <p className="mt-2 text-xs font-medium text-gray-500">{label}</p>
      </div>
    </Link>
  );
}

function NewsSignalRow({ signal }: { signal: NewsSignal }) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-amber-50 px-3 py-2 text-sm">
      <div className="flex items-center gap-2">
        <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
        <Link
          to={`/intelligence/news?ticker=${signal.ticker}`}
          className="font-semibold text-gray-900 hover:text-blue-600"
        >
          {signal.ticker}
        </Link>
      </div>
      <div className="flex items-center gap-3 text-xs text-gray-500">
        <span>{signal.negative_count_24h} negative</span>
        <span className="font-mono">{signal.news_impact_score.toFixed(2)}</span>
        {signal.dominant_event_type && (
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">
            {signal.dominant_event_type.replace(/_/g, " ")}
          </span>
        )}
      </div>
    </div>
  );
}

function FilingSignalRow({ signal }: { signal: FilingSignal }) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-red-50 px-3 py-2 text-sm">
      <div className="flex items-center gap-2">
        <FileWarning className="h-3.5 w-3.5 text-red-500" />
        <Link
          to={`/intelligence/filings?ticker=${signal.ticker}`}
          className="font-semibold text-gray-900 hover:text-blue-600"
        >
          {signal.ticker}
        </Link>
      </div>
      <div className="flex items-center gap-3 text-xs text-gray-500">
        {signal.insider_cluster_sell && (
          <span className="rounded bg-red-100 px-1.5 py-0.5 font-medium text-red-700">
            cluster sell
          </span>
        )}
        {signal.insider_sell_count_30d > 0 && (
          <span>{signal.insider_sell_count_30d} sells</span>
        )}
        {signal.eightk_count_30d > 0 && (
          <span>{signal.eightk_count_30d} 8-K</span>
        )}
      </div>
    </div>
  );
}
