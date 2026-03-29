import { useState, useMemo } from "react";
import {
  Search,
  ChevronDown,
  ChevronRight,
  Filter,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  ArrowRight,
  Loader2,
  BarChart3,
  Brain,
  Clock,
  History,
} from "lucide-react";
import { Card } from "@/components/Card";
import { PLValue } from "@/components/PLValue";
import { StatusBadge } from "@/components/StatusBadge";
import {
  useLatestScan,
  useTriggerScan,
  useScanHistory,
  useScanById,
} from "@/hooks/useApi";
import type {
  CSPCandidate,
  ScanResult,
  ScanHistoryEntry,
  PipelineStage,
  CSPAnalysis,
} from "@/types";

const TOP_N_LIMIT = 100;

export function Scanner() {
  const { data: latestScan } = useLatestScan();
  const { data: scanHistory } = useScanHistory(5);
  const triggerScan = useTriggerScan();
  const [symbols, setSymbols] = useState("");
  const [selectedScanId, setSelectedScanId] = useState<string | null>(null);
  const { data: selectedScan } = useScanById(selectedScanId);

  const scan: ScanResult | null | undefined =
    triggerScan.data ?? selectedScan ?? latestScan;

  const handleScan = () => {
    setSelectedScanId(null);
    triggerScan.mutate({ symbols: symbols || undefined, topN: TOP_N_LIMIT });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !triggerScan.isPending) handleScan();
  };

  const handleSelectScan = (scanId: string) => {
    setSelectedScanId(scanId === selectedScanId ? null : scanId);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Scanner</h1>
          <p className="mt-1 text-sm text-gray-500">
            Full pipeline: fundamental screen → conviction filter → options
            chain scan → LLM analysis
          </p>
        </div>
      </div>

      {/* Scan Input */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Enter tickers: AAPL, GOOG, META …  or leave blank for watchlist"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={triggerScan.isPending}
              className="w-full rounded-lg border border-gray-300 bg-white py-2.5 pl-10 pr-4 text-sm text-gray-900 placeholder-gray-400 transition-colors focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
            />
          </div>
          <button
            onClick={handleScan}
            disabled={triggerScan.isPending}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {triggerScan.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Scanning…
              </>
            ) : (
              <>
                <Search className="h-4 w-4" />
                Run Scan
              </>
            )}
          </button>
        </div>
      </div>

      {/* Recent Scans */}
      {scanHistory && scanHistory.length > 0 && (
        <RecentScans
          history={scanHistory}
          activeScanId={scan?.scan_id ?? null}
          onSelect={handleSelectScan}
        />
      )}

      {/* Loading State */}
      {triggerScan.isPending && <ScanProgress />}

      {/* Error */}
      {triggerScan.isError && (
        <div className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
          <XCircle className="mt-0.5 h-5 w-5 flex-shrink-0 text-red-500" />
          <div>
            <p className="text-sm font-medium text-red-800">Scan failed</p>
            <p className="mt-1 text-sm text-red-600">
              {triggerScan.error.message}
            </p>
          </div>
        </div>
      )}

      {/* Results */}
      {scan && !triggerScan.isPending && <ScanResults scan={scan} />}

      {/* Empty state — no scan ever run */}
      {!scan && !triggerScan.isPending && !triggerScan.isError && (
        <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50/50 py-16 text-center">
          <BarChart3 className="mx-auto h-10 w-10 text-gray-300" />
          <p className="mt-3 text-sm font-medium text-gray-500">
            No scan results yet
          </p>
          <p className="mt-1 text-xs text-gray-400">
            Enter tickers above and click Run Scan — or leave blank to scan your
            watchlist
          </p>
        </div>
      )}
    </div>
  );
}

/* ── Recent Scans Selector ─────────────────────────────────────── */

function RecentScans({
  history,
  activeScanId,
  onSelect,
}: {
  history: ScanHistoryEntry[];
  activeScanId: string | null;
  onSelect: (scanId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-5 py-3 text-left transition-colors hover:bg-gray-50"
      >
        <div className="flex items-center gap-2.5">
          <History className="h-4 w-4 text-gray-400" />
          <span className="text-sm font-medium text-gray-700">
            Recent Scans
          </span>
          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold text-gray-500">
            {history.length}
          </span>
        </div>
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-gray-400" />
        ) : (
          <ChevronRight className="h-4 w-4 text-gray-400" />
        )}
      </button>

      {expanded && (
        <div className="border-t border-gray-100">
          {history.map((entry) => {
            const isActive = entry.scan_id === activeScanId;
            const time = new Date(entry.scanned_at).toLocaleString(undefined, {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            });

            return (
              <button
                key={entry.scan_id}
                onClick={() => onSelect(entry.scan_id)}
                className={`flex w-full items-center justify-between px-5 py-2.5 text-left text-sm transition-colors hover:bg-blue-50 ${
                  isActive ? "bg-blue-50/70" : ""
                }`}
              >
                <div className="flex items-center gap-3">
                  <Clock className="h-3.5 w-3.5 text-gray-400" />
                  <span
                    className={`${isActive ? "font-semibold text-blue-700" : "text-gray-700"}`}
                  >
                    {time}
                  </span>
                  {entry.trigger === "scheduled" && (
                    <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[9px] font-medium text-gray-500">
                      AUTO
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-4 text-xs text-gray-500">
                  <span>
                    <span className="font-medium text-gray-700">
                      {entry.symbols_scanned}
                    </span>{" "}
                    tickers
                  </span>
                  <span>
                    <span className="font-medium text-emerald-600">
                      {entry.csp_candidate_count}
                    </span>{" "}
                    CSP
                  </span>
                  <span>
                    <span className="font-medium text-purple-600">
                      {entry.llm_analysis_count}
                    </span>{" "}
                    AI
                  </span>
                  {entry.errors_count > 0 && (
                    <span className="font-medium text-amber-500">
                      {entry.errors_count} err
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ── Progress indicator ─────────────────────────────────────────── */

function ScanProgress() {
  return (
    <div className="rounded-xl border border-blue-100 bg-blue-50/50 p-6">
      <div className="flex items-center gap-3">
        <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
        <div>
          <p className="text-sm font-medium text-blue-900">
            Running scan pipeline…
          </p>
          <p className="mt-0.5 text-xs text-blue-600">
            Screening fundamentals → conviction analysis → options chains →
            LLM reasoning. This may take a few minutes.
          </p>
        </div>
      </div>
      <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-blue-100">
        <div
          className="h-full animate-pulse rounded-full bg-blue-400"
          style={{ width: "60%" }}
        />
      </div>
    </div>
  );
}

/* ── Full results view ──────────────────────────────────────────── */

function ScanResults({ scan }: { scan: ScanResult }) {
  const hasResults =
    scan.csp_candidates.length > 0 ||
    scan.cc_candidates.length > 0 ||
    (scan.llm_analyses?.length ?? 0) > 0;

  return (
    <div className="space-y-5">
      {/* Scan summary header */}
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <span className="text-gray-500">
          Scanned{" "}
          <span className="font-semibold text-gray-900">
            {scan.symbols_scanned}
          </span>{" "}
          tickers
        </span>
        <span className="text-gray-300">·</span>
        <span className="text-gray-500">
          <span className="font-semibold text-emerald-600">
            {scan.csp_candidates.length}
          </span>{" "}
          CSP candidates
        </span>
        <span className="text-gray-300">·</span>
        <span className="text-gray-500">
          <span className="font-semibold text-blue-600">
            {scan.cc_candidates.length}
          </span>{" "}
          CC candidates
        </span>
        <span className="text-gray-300">·</span>
        <span className="text-gray-500">
          <span className="font-semibold text-purple-600">
            {scan.llm_analyses?.length ?? 0}
          </span>{" "}
          AI analyses
        </span>
        {(scan.intents_created ?? 0) > 0 && (
          <>
            <span className="text-gray-300">·</span>
            <span className="text-gray-500">
              <span className="font-semibold text-amber-600">
                {scan.intents_created}
              </span>{" "}
              intents created
            </span>
          </>
        )}
      </div>

      {/* Pipeline funnel */}
      {scan.pipeline_stages?.length > 0 && (
        <PipelineFunnel stages={scan.pipeline_stages} />
      )}

      {/* Errors / Warnings */}
      {scan.errors?.length > 0 && (
        <div className="space-y-2">
          {scan.errors.map((err, i) => (
            <div
              key={i}
              className="flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />
              <p className="text-sm text-amber-800">{err}</p>
            </div>
          ))}
        </div>
      )}

      {/* CSP Candidates — grouped by ticker */}
      <CspCandidatesGrouped candidates={scan.csp_candidates} />

      {/* CC Candidates */}
      {scan.cc_candidates.length > 0 && (
        <CcCandidatesCard candidates={scan.cc_candidates} />
      )}

      {/* LLM Analyses */}
      {(scan.llm_analyses?.length ?? 0) > 0 && (
        <LlmAnalysesCard analyses={scan.llm_analyses} />
      )}

      {/* No results after pipeline */}
      {!hasResults && scan.errors.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50/50 py-12 text-center">
          <Filter className="mx-auto h-8 w-8 text-gray-300" />
          <p className="mt-3 text-sm font-medium text-gray-500">
            All tickers were filtered out
          </p>
          <p className="mx-auto mt-1 max-w-md text-xs text-gray-400">
            Every ticker was eliminated by the pipeline filters above. Check
            conviction eligibility on the Conviction page, or adjust market cap
            / institutional ownership thresholds in Settings.
          </p>
        </div>
      )}
    </div>
  );
}

/* ── Pipeline Funnel ────────────────────────────────────────────── */

function PipelineFunnel({ stages }: { stages: PipelineStage[] }) {
  const [expanded, setExpanded] = useState(false);
  const allPassed = stages.every((s) => s.dropped === 0);

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-5 py-3 text-left transition-colors hover:bg-gray-50"
      >
        <div className="flex items-center gap-2.5">
          <Filter className="h-4 w-4 text-gray-400" />
          <span className="text-sm font-medium text-gray-700">
            Pipeline Filter Stages
          </span>
          {allPassed ? (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-600">
              ALL PASSED
            </span>
          ) : (
            <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-600">
              {stages.reduce((sum, s) => sum + s.dropped, 0)} DROPPED
            </span>
          )}
        </div>
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-gray-400" />
        ) : (
          <ChevronRight className="h-4 w-4 text-gray-400" />
        )}
      </button>

      {expanded && (
        <div className="border-t border-gray-100 px-5 py-4">
          <div className="flex items-center gap-2">
            {stages.map((stage, i) => (
              <div key={stage.name} className="flex items-center gap-2">
                <StageChip stage={stage} />
                {i < stages.length - 1 && (
                  <ArrowRight className="h-3.5 w-3.5 flex-shrink-0 text-gray-300" />
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StageChip({ stage }: { stage: PipelineStage }) {
  const passed = stage.dropped === 0;
  return (
    <div
      className={`rounded-lg border px-3 py-2 text-xs ${
        passed
          ? "border-emerald-200 bg-emerald-50"
          : "border-amber-200 bg-amber-50"
      }`}
    >
      <div className="flex items-center gap-1.5">
        {passed ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
        ) : (
          <XCircle className="h-3.5 w-3.5 text-amber-500" />
        )}
        <span className="font-semibold text-gray-700">{stage.name}</span>
      </div>
      <div className="mt-1 text-gray-500">
        {stage.input} → {stage.output}
        {stage.dropped > 0 && (
          <span className="ml-1 text-amber-600">(−{stage.dropped})</span>
        )}
      </div>
      {stage.detail && (
        <div className="mt-0.5 text-[10px] text-gray-400">{stage.detail}</div>
      )}
    </div>
  );
}

/* ── CSP Candidates grouped by ticker ───────────────────────────── */

interface TickerGroup {
  symbol: string;
  candidates: CSPCandidate[];
  bestScore: number;
  bestReturn: number;
  hasEarnings: boolean;
}

function CspCandidatesGrouped({
  candidates,
}: {
  candidates: CSPCandidate[];
}) {
  const groups = useMemo(() => {
    const map = new Map<string, CSPCandidate[]>();
    for (const c of candidates) {
      const list = map.get(c.symbol) ?? [];
      list.push(c);
      map.set(c.symbol, list);
    }

    const result: TickerGroup[] = [];
    for (const [symbol, cands] of map) {
      cands.sort((a, b) => b.score - a.score);
      result.push({
        symbol,
        candidates: cands,
        bestScore: cands[0].score,
        bestReturn: cands[0].annualized_return_pct,
        hasEarnings: cands.some((c) => c.earnings_within_dte),
      });
    }
    result.sort((a, b) => b.bestScore - a.bestScore);
    return result;
  }, [candidates]);

  const tickerCount = groups.length;

  return (
    <Card
      title="Cash-Secured Put Candidates"
      subtitle={
        candidates.length > 0
          ? `${candidates.length} contracts across ${tickerCount} ticker${tickerCount !== 1 ? "s" : ""}`
          : "No candidates survived the pipeline"
      }
    >
      {candidates.length > 0 ? (
        <div className="space-y-1">
          {groups.map((g) => (
            <TickerGroupRow key={g.symbol} group={g} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-gray-400">
          No CSP candidates matched. Expand the pipeline filter above to see
          where tickers were dropped.
        </p>
      )}
    </Card>
  );
}

function TickerGroupRow({ group }: { group: TickerGroup }) {
  const [expanded, setExpanded] = useState(false);
  const best = group.candidates[0];

  return (
    <div className="rounded-lg border border-gray-100">
      {/* Summary row — always visible */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-gray-50"
      >
        <div className="flex items-center gap-3">
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-gray-400" />
          ) : (
            <ChevronRight className="h-4 w-4 text-gray-400" />
          )}
          <span className="text-sm font-bold text-gray-900">
            {group.symbol}
          </span>
          {group.hasEarnings && (
            <StatusBadge label="EARNINGS" variant="warning" />
          )}
          <span className="text-xs text-gray-400">
            {group.candidates.length} contract
            {group.candidates.length !== 1 ? "s" : ""}
          </span>
        </div>

        <div className="flex items-center gap-6 text-xs">
          <div className="text-right">
            <span className="text-gray-400">Best strike </span>
            <span className="font-mono text-gray-700">
              ${best.strike.toFixed(2)}
            </span>
          </div>
          <div className="text-right">
            <span className="text-gray-400">Exp </span>
            <span className="text-gray-700">{best.expiration}</span>
          </div>
          <div className="text-right">
            <span className="text-gray-400">Premium </span>
            <span className="font-mono text-gray-700">
              ${best.premium_per_contract.toFixed(0)}
            </span>
          </div>
          <div className="text-right">
            <PLValue value={best.annualized_return_pct} format="percent" />
          </div>
          <div className="w-14 text-right">
            <span className="font-mono font-semibold text-gray-900">
              {best.score.toFixed(1)}
            </span>
          </div>
        </div>
      </button>

      {/* Expanded detail table */}
      {expanded && (
        <div className="border-t border-gray-100 bg-gray-50/50 px-4 py-3">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-gray-400">
                <th className="pb-1.5 pr-3">Strike</th>
                <th className="pb-1.5 pr-3">Exp</th>
                <th className="pb-1.5 pr-3 text-right">DTE</th>
                <th className="pb-1.5 pr-3 text-right">Bid</th>
                <th className="pb-1.5 pr-3 text-right">Prem/C</th>
                <th className="pb-1.5 pr-3 text-right">Ann. Ret</th>
                <th className="pb-1.5 pr-3 text-right">Delta</th>
                <th className="pb-1.5 pr-3 text-right">OI</th>
                <th className="pb-1.5 pr-3 text-right">Vol</th>
                <th className="pb-1.5 text-right">Score</th>
                <th className="pb-1.5 pl-2"></th>
              </tr>
            </thead>
            <tbody>
              {group.candidates.map((c) => (
                <tr
                  key={c.option_symbol}
                  className="border-t border-gray-100 text-gray-600"
                >
                  <td className="py-1.5 pr-3 font-mono font-medium text-gray-800">
                    ${c.strike.toFixed(2)}
                  </td>
                  <td className="py-1.5 pr-3">{c.expiration}</td>
                  <td className="py-1.5 pr-3 text-right">{c.dte}d</td>
                  <td className="py-1.5 pr-3 text-right font-mono">
                    ${c.bid.toFixed(2)}
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono">
                    ${c.premium_per_contract.toFixed(0)}
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <PLValue
                      value={c.annualized_return_pct}
                      format="percent"
                    />
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono">
                    {c.delta.toFixed(3)}
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    {c.open_interest.toLocaleString()}
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    {c.volume.toLocaleString()}
                  </td>
                  <td className="py-1.5 text-right font-mono font-semibold text-gray-800">
                    {c.score.toFixed(1)}
                  </td>
                  <td className="py-1.5 pl-2">
                    {c.earnings_within_dte && (
                      <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[9px] font-medium text-amber-600">
                        EARN
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ── CC Candidates Card ─────────────────────────────────────────── */

function CcCandidatesCard({ candidates }: { candidates: CSPCandidate[] }) {
  return (
    <Card
      title="Covered Call Candidates"
      subtitle={`${candidates.length} candidates on held shares`}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-xs text-gray-400">
              <th className="pb-2 pr-4">Symbol</th>
              <th className="pb-2 pr-4 text-right">Strike</th>
              <th className="pb-2 pr-4 text-right">Exp</th>
              <th className="pb-2 pr-4 text-right">DTE</th>
              <th className="pb-2 pr-4 text-right">Bid</th>
              <th className="pb-2 pr-4 text-right">Premium/C</th>
              <th className="pb-2 text-right">Ann. Return</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => (
              <tr
                key={c.option_symbol}
                className="border-b border-gray-200 text-gray-700 hover:bg-gray-50"
              >
                <td className="py-2.5 pr-4 font-semibold text-gray-900">
                  {c.symbol}
                </td>
                <td className="py-2.5 pr-4 text-right font-mono">
                  ${c.strike.toFixed(2)}
                </td>
                <td className="py-2.5 pr-4 text-right text-xs">
                  {c.expiration}
                </td>
                <td className="py-2.5 pr-4 text-right">{c.dte}d</td>
                <td className="py-2.5 pr-4 text-right font-mono">
                  ${c.bid.toFixed(2)}
                </td>
                <td className="py-2.5 pr-4 text-right font-mono">
                  ${c.premium_per_contract.toFixed(0)}
                </td>
                <td className="py-2.5 text-right">
                  <PLValue value={c.annualized_return_pct} format="percent" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/* ── LLM Analyses Card ──────────────────────────────────────────── */

function LlmAnalysesCard({ analyses }: { analyses: CSPAnalysis[] }) {
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Brain className="h-4 w-4 text-purple-500" />
          AI Analysis
        </span>
      }
      subtitle="Gemini-powered trade reasoning"
    >
      <div className="space-y-4">
        {analyses.map((a) => (
          <div
            key={a.ticker}
            className="rounded-lg border border-gray-200 bg-gray-50 p-4"
          >
            <div className="flex items-center justify-between">
              <span className="text-lg font-bold text-gray-900">
                {a.ticker}
              </span>
              <div className="flex gap-2">
                <StatusBadge
                  label={`Comfort: ${a.assignment_comfort}`}
                  variant={
                    a.assignment_comfort === "high"
                      ? "success"
                      : a.assignment_comfort === "medium"
                        ? "warning"
                        : "danger"
                  }
                />
                <StatusBadge
                  label={a.confidence}
                  variant={
                    a.confidence === "high"
                      ? "success"
                      : a.confidence === "medium"
                        ? "warning"
                        : "danger"
                  }
                />
              </div>
            </div>
            <p className="mt-2 text-sm text-gray-500">{a.thesis}</p>
            <div className="mt-3 grid grid-cols-3 gap-3 text-xs">
              <div>
                <span className="text-gray-400">Strike: </span>
                <span className="text-gray-900">
                  ${a.recommended_strike}
                </span>
              </div>
              <div>
                <span className="text-gray-400">Contracts: </span>
                <span className="text-gray-900">{a.suggested_contracts}</span>
              </div>
              <div>
                <span className="text-gray-400">Ann. Return: </span>
                <PLValue value={a.annualized_return_pct} format="percent" />
              </div>
            </div>
            {a.risks.length > 0 && (
              <div className="mt-2">
                <span className="text-xs text-gray-400">Risks: </span>
                <span className="text-xs text-amber-600">
                  {a.risks.join(" · ")}
                </span>
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}
