import { useState, useMemo, useCallback } from "react";
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
  DollarSign,
  TrendingUp,
  SlidersHorizontal,
  RotateCcw,
  Plus,
  Minus,
  CalendarCheck,
  CalendarX,
} from "lucide-react";
import { Card } from "@/components/Card";
import { PLValue } from "@/components/PLValue";
import { StatusBadge } from "@/components/StatusBadge";
import {
  useLatestScan,
  useTriggerScan,
  useScanHistory,
  useScanById,
  useSystemConfig,
} from "@/hooks/useApi";
import type {
  CSPCandidate,
  ScanResult,
  ScanHistoryEntry,
  PipelineStage,
  CSPAnalysis,
  AllocatedTrade,
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

      {/* Entry timing guidance */}
      <EntryTimingBanner />

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

      {/* Allocation Summary — optimizer-selected trades */}
      {(scan.allocated_trades?.length > 0 || scan.allocation) && (
        <AllocationSummaryCard
          allocation={scan.allocation}
          trades={scan.allocated_trades ?? []}
          cspCandidates={scan.csp_candidates}
        />
      )}

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

/* ── Entry Timing Banner ───────────────────────────────────────── */

const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"] as const;
const OPTIMAL_DAYS = new Set([2, 3]); // Tuesday, Wednesday
const AVOID_DAYS = new Set([4, 5]); // Thursday, Friday

function EntryTimingBanner() {
  const now = new Date();
  const day = now.getDay();
  const hour = now.getHours();
  const dayName = DAY_NAMES[day];

  const isOptimal = OPTIMAL_DAYS.has(day);
  const isAvoid = AVOID_DAYS.has(day);
  const isWeekend = day === 0 || day === 6;
  const inTradingWindow = hour >= 9 && hour < 13;
  const isPrimeSlot = hour >= 10 && hour < 11;

  if (isWeekend) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-xs text-gray-500">
        <CalendarX className="h-4 w-4 text-gray-400" />
        <span>
          <span className="font-semibold text-gray-700">{dayName}</span> — Markets closed.
          Best entry days are <span className="font-semibold text-emerald-600">Tuesday</span> and{" "}
          <span className="font-semibold text-emerald-600">Wednesday</span> for same-week Friday expiration.
        </span>
      </div>
    );
  }

  if (isOptimal) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs">
        <CalendarCheck className="h-4 w-4 text-emerald-600" />
        <span className="text-emerald-800">
          <span className="font-bold">{dayName}</span> — Optimal CSP entry day per backtest data.
          {inTradingWindow ? (
            isPrimeSlot ? (
              <span className="ml-1 font-semibold">10:30 AM is the prime slot ($76.64 avg P&L, 77.4% win rate).</span>
            ) : (
              <span className="ml-1">9:30 AM–1:00 PM window is active.</span>
            )
          ) : hour < 9 ? (
            <span className="ml-1 text-emerald-600">Market opens at 9:30 AM ET.</span>
          ) : (
            <span className="ml-1 text-amber-600">After 1:00 PM — win rate drops, consider waiting until tomorrow.</span>
          )}
          {" "}Target this Friday's expiration for fast capital recycling.
        </span>
      </div>
    );
  }

  if (isAvoid) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs">
        <CalendarX className="h-4 w-4 text-amber-500" />
        <span className="text-amber-800">
          <span className="font-bold">{dayName}</span> — Backtest shows weaker CSP entry performance on {dayName}s.
          {day === 4 ? (
            <span className="ml-1">Consider scanning for next week's expiration, or wait until next Tuesday.</span>
          ) : (
            <span className="ml-1">Options expire today — new CSP entries carry higher gamma risk.</span>
          )}
        </span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5 text-xs text-gray-600">
      <Clock className="h-4 w-4 text-gray-400" />
      <span>
        <span className="font-semibold text-gray-700">{dayName}</span> — Acceptable entry day.
        Best days are <span className="font-semibold text-emerald-600">Tuesday</span> and{" "}
        <span className="font-semibold text-emerald-600">Wednesday</span> targeting Friday expiration.
      </span>
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

/* ── Allocation Summary Card ───────────────────────────────────── */

const COMMISSION_PER_CONTRACT = 0.65;

interface PlaygroundTrade {
  key: string;
  symbol: string;
  strike: number;
  expiration: string;
  dte: number;
  bid: number;
  premiumPerContract: number;
  collateralPerContract: number;
  contracts: number;
  enabled: boolean;
  isRecommended: boolean;
  recommendedContracts: number;
  conviction: string;
  annualizedReturnPct: number;
  strategy: string;
}

function tradeKey(symbol: string, strike: number, expiration: string) {
  return `${symbol}-${strike}-${expiration}`;
}

function buildPlaygroundTrades(
  allocatedTrades: AllocatedTrade[],
  cspCandidates: CSPCandidate[],
): PlaygroundTrade[] {
  const recommended = new Set(
    allocatedTrades.map((t) => tradeKey(t.symbol, t.strike, t.expiration)),
  );

  const trades: PlaygroundTrade[] = allocatedTrades.map((t) => ({
    key: tradeKey(t.symbol, t.strike, t.expiration),
    symbol: t.symbol,
    strike: t.strike,
    expiration: t.expiration,
    dte: t.dte,
    bid: t.bid,
    premiumPerContract: t.bid * 100,
    collateralPerContract: t.strike * 100,
    contracts: t.contracts,
    enabled: true,
    isRecommended: true,
    recommendedContracts: t.contracts,
    conviction: t.conviction,
    annualizedReturnPct: t.annualized_return_pct,
    strategy: t.strategy || t.option_type,
  }));

  for (const c of cspCandidates) {
    const k = tradeKey(c.symbol, c.strike, c.expiration);
    if (!recommended.has(k)) {
      trades.push({
        key: k,
        symbol: c.symbol,
        strike: c.strike,
        expiration: c.expiration,
        dte: c.dte,
        bid: c.bid,
        premiumPerContract: c.premium_per_contract,
        collateralPerContract: c.strike * 100,
        contracts: 0,
        enabled: false,
        isRecommended: false,
        recommendedContracts: 0,
        conviction: "",
        annualizedReturnPct: c.annualized_return_pct,
        strategy: "csp",
      });
    }
  }

  return trades;
}

function computeTotals(trades: PlaygroundTrade[]) {
  let premium = 0;
  let contracts = 0;
  let collateral = 0;
  for (const t of trades) {
    if (!t.enabled || t.contracts === 0) continue;
    premium += t.premiumPerContract * t.contracts;
    contracts += t.contracts;
    collateral += t.collateralPerContract * t.contracts;
  }
  const commission = contracts * COMMISSION_PER_CONTRACT;
  return { premium, contracts, collateral, commission, net: premium - commission };
}

function MetricBox({
  label,
  value,
  sub,
  borderColor,
  bgColor,
  labelColor,
  valueColor,
  icon,
}: {
  label: string;
  value: string;
  sub?: string;
  borderColor: string;
  bgColor: string;
  labelColor: string;
  valueColor: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className={`rounded-lg border ${borderColor} ${bgColor} p-3`}>
      <div className={`flex items-center gap-1.5 text-[10px] uppercase tracking-wide ${labelColor}`}>
        {icon}
        {label}
      </div>
      <div className={`mt-1 font-mono text-lg font-bold ${valueColor}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[10px] text-gray-400">{sub}</div>}
    </div>
  );
}

function AllocationSummaryCard({
  allocation,
  trades,
  cspCandidates,
}: {
  allocation: ScanResult["allocation"];
  trades: AllocatedTrade[];
  cspCandidates: CSPCandidate[];
}) {
  const { data: config } = useSystemConfig();
  const capitalCeiling = config?.available_capital ?? 0;
  const [expanded, setExpanded] = useState(false);
  const [playgroundOpen, setPlaygroundOpen] = useState(false);
  const [pgTrades, setPgTrades] = useState<PlaygroundTrade[]>([]);
  const [addMenuOpen, setAddMenuOpen] = useState(false);

  const recTotals = useMemo(() => {
    const totalPremium = allocation?.total_premium ?? trades.reduce((s, t) => s + t.total_premium, 0);
    const totalContracts = trades.reduce((s, t) => s + t.contracts, 0);
    const totalCollateral = allocation?.total_collateral ?? trades.reduce((s, t) => s + t.collateral, 0);
    const totalCommission = totalContracts * COMMISSION_PER_CONTRACT;
    return {
      premium: totalPremium,
      contracts: totalContracts,
      collateral: totalCollateral,
      commission: totalCommission,
      net: totalPremium - totalCommission,
    };
  }, [allocation, trades]);

  const utilization = allocation?.capital_utilization_pct ?? 0;
  const solverStatus = allocation?.solver_status ?? "unknown";

  const expirations = useMemo(
    () => [...new Set(trades.map((t) => t.expiration))].sort(),
    [trades],
  );
  const expirationLabel =
    expirations.length === 1
      ? expirations[0]
      : expirations.length > 1
        ? `${expirations[0]} → ${expirations[expirations.length - 1]}`
        : null;

  const convictionColor = (c: string) => {
    if (c === "high") return "text-emerald-600 bg-emerald-50";
    if (c === "medium") return "text-amber-600 bg-amber-50";
    return "text-red-600 bg-red-50";
  };

  const openPlayground = useCallback(() => {
    setPgTrades(buildPlaygroundTrades(trades, cspCandidates));
    setPlaygroundOpen(true);
  }, [trades, cspCandidates]);

  const resetPlayground = useCallback(() => {
    setPgTrades(buildPlaygroundTrades(trades, cspCandidates));
  }, [trades, cspCandidates]);

  const toggleTrade = useCallback((key: string) => {
    setPgTrades((prev) =>
      prev.map((t) =>
        t.key === key
          ? {
              ...t,
              enabled: !t.enabled,
              contracts: !t.enabled ? Math.max(t.contracts, 1) : t.contracts,
            }
          : t,
      ),
    );
  }, []);

  const adjustContracts = useCallback(
    (key: string, delta: number) => {
      setPgTrades((prev) => {
        const updated = prev.map((t) => {
          if (t.key !== key) return t;
          const newQty = Math.max(0, t.contracts + delta);
          return { ...t, contracts: newQty, enabled: newQty > 0 };
        });
        if (capitalCeiling > 0 && delta > 0) {
          const totals = computeTotals(updated);
          if (totals.collateral > capitalCeiling) return prev;
        }
        return updated;
      });
    },
    [capitalCeiling],
  );

  const setContracts = useCallback(
    (key: string, value: number) => {
      const qty = Math.max(0, value);
      setPgTrades((prev) => {
        const updated = prev.map((t) =>
          t.key === key ? { ...t, contracts: qty, enabled: qty > 0 } : t,
        );
        if (capitalCeiling > 0) {
          const totals = computeTotals(updated);
          if (totals.collateral > capitalCeiling) return prev;
        }
        return updated;
      });
    },
    [capitalCeiling],
  );

  const pgTotals = useMemo(() => computeTotals(pgTrades), [pgTrades]);
  const pgRemaining = capitalCeiling > 0 ? capitalCeiling - pgTotals.collateral : 0;
  const pgChanged = useMemo(() => {
    return pgTrades.some(
      (t) => t.enabled !== t.isRecommended || t.contracts !== t.recommendedContracts,
    );
  }, [pgTrades]);

  const availableCandidates = useMemo(
    () => pgTrades.filter((t) => !t.enabled && t.contracts === 0),
    [pgTrades],
  );
  const activePgTrades = useMemo(
    () => pgTrades.filter((t) => t.enabled || t.isRecommended),
    [pgTrades],
  );

  if (trades.length === 0 && !allocation) return null;

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-blue-500" />
          Portfolio Allocation
        </span>
      }
      subtitle={
        trades.length > 0
          ? `Optimizer selected ${trades.length} trade${trades.length !== 1 ? "s" : ""} across ${new Set(trades.map((t) => t.symbol)).size} ticker${new Set(trades.map((t) => t.symbol)).size !== 1 ? "s" : ""}${expirationLabel ? ` · Exp ${expirationLabel}` : ""}`
          : "No trades allocated"
      }
    >
      {/* Recommended summary metrics */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <MetricBox
          label="Gross Premium"
          value={`$${recTotals.premium.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          borderColor="border-emerald-100"
          bgColor="bg-emerald-50/50"
          labelColor="text-emerald-600"
          valueColor="text-emerald-700"
          icon={<DollarSign className="h-3 w-3" />}
        />
        <MetricBox
          label="Est. Commission"
          value={`-$${recTotals.commission.toFixed(2)}`}
          sub={`${recTotals.contracts} contract${recTotals.contracts !== 1 ? "s" : ""} x $${COMMISSION_PER_CONTRACT}`}
          borderColor="border-red-100"
          bgColor="bg-red-50/50"
          labelColor="text-red-500"
          valueColor="text-red-600"
        />
        <MetricBox
          label="Net Premium"
          value={`$${recTotals.net.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          borderColor="border-blue-100"
          bgColor="bg-blue-50/50"
          labelColor="text-blue-600"
          valueColor="text-blue-700"
        />
        <MetricBox
          label="Capital Deployed"
          value={`$${recTotals.collateral.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          sub={`${utilization.toFixed(1)}% utilized · ${solverStatus}`}
          borderColor="border-gray-200"
          bgColor="bg-gray-50/50"
          labelColor="text-gray-500"
          valueColor="text-gray-800"
        />
      </div>

      {/* Trade details toggle + Customize button */}
      <div className="mt-4 flex items-center justify-between">
        {trades.length > 0 && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 text-xs font-medium text-gray-500 transition-colors hover:text-gray-700"
          >
            {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            {expanded ? "Hide" : "Show"} trade details
          </button>
        )}
        {trades.length > 0 && (
          <button
            onClick={playgroundOpen ? () => setPlaygroundOpen(false) : openPlayground}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              playgroundOpen
                ? "bg-purple-100 text-purple-700 hover:bg-purple-200"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
            {playgroundOpen ? "Close Playground" : "Customize Allocation"}
          </button>
        )}
      </div>

      {/* Static trade details (recommended) */}
      {expanded && !playgroundOpen && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-gray-200 text-[10px] uppercase tracking-wide text-gray-400">
                <th className="pb-2 pr-3">Symbol</th>
                <th className="pb-2 pr-3">Type</th>
                <th className="pb-2 pr-3 text-right">Strike</th>
                <th className="pb-2 pr-3 text-right">Exp</th>
                <th className="pb-2 pr-3 text-right">DTE</th>
                <th className="pb-2 pr-3 text-right">Contracts</th>
                <th className="pb-2 pr-3 text-right">Premium</th>
                <th className="pb-2 pr-3 text-right">Commission</th>
                <th className="pb-2 pr-3 text-right">Net</th>
                <th className="pb-2 pr-3 text-right">Collateral</th>
                <th className="pb-2 pr-3 text-right">Ann. Ret</th>
                <th className="pb-2 text-right">Conviction</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => {
                const comm = t.contracts * COMMISSION_PER_CONTRACT;
                const net = t.total_premium - comm;
                return (
                  <tr key={`${t.symbol}-${t.strike}-${i}`} className="border-b border-gray-100 text-gray-600">
                    <td className="py-2 pr-3 font-semibold text-gray-900">{t.symbol}</td>
                    <td className="py-2 pr-3 uppercase">{t.strategy || t.option_type}</td>
                    <td className="py-2 pr-3 text-right font-mono">${t.strike.toFixed(2)}</td>
                    <td className="py-2 pr-3 text-right">{t.expiration}</td>
                    <td className="py-2 pr-3 text-right">{t.dte}d</td>
                    <td className="py-2 pr-3 text-right font-mono">{t.contracts}</td>
                    <td className="py-2 pr-3 text-right font-mono text-emerald-600">${t.total_premium.toFixed(0)}</td>
                    <td className="py-2 pr-3 text-right font-mono text-red-500">-${comm.toFixed(2)}</td>
                    <td className="py-2 pr-3 text-right font-mono font-medium text-blue-600">${net.toFixed(0)}</td>
                    <td className="py-2 pr-3 text-right font-mono">${t.collateral.toLocaleString()}</td>
                    <td className="py-2 pr-3 text-right">
                      <PLValue value={t.annualized_return_pct} format="percent" />
                    </td>
                    <td className="py-2 text-right">
                      {t.conviction && (
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${convictionColor(t.conviction)}`}>
                          {t.conviction.toUpperCase()}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-gray-300 font-semibold text-gray-900">
                <td className="pt-2 pr-3" colSpan={5}>Totals</td>
                <td className="pt-2 pr-3 text-right font-mono">{recTotals.contracts}</td>
                <td className="pt-2 pr-3 text-right font-mono text-emerald-600">${recTotals.premium.toFixed(0)}</td>
                <td className="pt-2 pr-3 text-right font-mono text-red-500">-${recTotals.commission.toFixed(2)}</td>
                <td className="pt-2 pr-3 text-right font-mono text-blue-600">${recTotals.net.toFixed(0)}</td>
                <td className="pt-2 pr-3" colSpan={3}></td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {/* ── Playground ────────────────────────────────────────────── */}
      {playgroundOpen && (
        <div className="mt-4 rounded-xl border-2 border-dashed border-purple-200 bg-purple-50/30 p-4">
          {/* Playground header */}
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="flex items-center gap-2 text-sm font-bold text-purple-900">
                <SlidersHorizontal className="h-4 w-4" />
                Allocation Playground
              </h3>
              {capitalCeiling > 0 && (
                <div className="mt-1 flex items-center gap-3 text-xs">
                  <span className="text-gray-500">
                    Capital limit:{" "}
                    <span className="font-semibold text-gray-800">
                      ${capitalCeiling.toLocaleString()}
                    </span>
                  </span>
                  <span className="text-gray-300">|</span>
                  <span className={pgRemaining < 0 ? "font-semibold text-red-600" : "text-gray-500"}>
                    Remaining:{" "}
                    <span className={`font-semibold ${pgRemaining < 0 ? "text-red-600" : "text-emerald-600"}`}>
                      ${pgRemaining.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </span>
                  </span>
                </div>
              )}
            </div>
            <button
              onClick={resetPlayground}
              disabled={!pgChanged}
              className="flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-gray-600 shadow-sm transition-colors hover:bg-gray-50 disabled:opacity-40"
            >
              <RotateCcw className="h-3 w-3" />
              Reset to Recommended
            </button>
          </div>

          {/* Capital usage bar */}
          {capitalCeiling > 0 && (
            <div className="mb-4">
              <div className="h-2 overflow-hidden rounded-full bg-gray-200">
                <div
                  className={`h-full rounded-full transition-all ${
                    pgTotals.collateral / capitalCeiling > 0.95
                      ? "bg-red-500"
                      : pgTotals.collateral / capitalCeiling > 0.8
                        ? "bg-amber-500"
                        : "bg-emerald-500"
                  }`}
                  style={{ width: `${Math.min(100, (pgTotals.collateral / capitalCeiling) * 100)}%` }}
                />
              </div>
              <div className="mt-1 flex justify-between text-[10px] text-gray-400">
                <span>${pgTotals.collateral.toLocaleString(undefined, { maximumFractionDigits: 0 })} deployed</span>
                <span>{((pgTotals.collateral / capitalCeiling) * 100).toFixed(1)}%</span>
              </div>
            </div>
          )}

          {/* Editable trade rows */}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-purple-200 text-[10px] uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-2 text-center" style={{ width: 32 }}></th>
                  <th className="pb-2 pr-3">Symbol</th>
                  <th className="pb-2 pr-3 text-right">Strike</th>
                  <th className="pb-2 pr-3 text-right">Exp</th>
                  <th className="pb-2 pr-3 text-right">DTE</th>
                  <th className="pb-2 pr-3 text-center">Contracts</th>
                  <th className="pb-2 pr-3 text-right">Prem/C</th>
                  <th className="pb-2 pr-3 text-right">Premium</th>
                  <th className="pb-2 pr-3 text-right">Collateral</th>
                  <th className="pb-2 pr-3 text-right">Ann. Ret</th>
                  <th className="pb-2 text-right">Rec</th>
                </tr>
              </thead>
              <tbody>
                {activePgTrades.map((t) => {
                  const rowPremium = t.premiumPerContract * t.contracts;
                  const rowCollateral = t.collateralPerContract * t.contracts;
                  const changed = t.contracts !== t.recommendedContracts || t.enabled !== t.isRecommended;
                  return (
                    <tr
                      key={t.key}
                      className={`border-b border-purple-100 transition-colors ${
                        !t.enabled ? "bg-gray-50 opacity-50" : changed ? "bg-purple-50/50" : ""
                      }`}
                    >
                      <td className="py-2 pr-2 text-center">
                        <input
                          type="checkbox"
                          checked={t.enabled}
                          onChange={() => toggleTrade(t.key)}
                          className="h-3.5 w-3.5 rounded border-gray-300 text-purple-600 focus:ring-purple-500"
                        />
                      </td>
                      <td className="py-2 pr-3 font-semibold text-gray-900">{t.symbol}</td>
                      <td className="py-2 pr-3 text-right font-mono">${t.strike.toFixed(2)}</td>
                      <td className="py-2 pr-3 text-right">{t.expiration}</td>
                      <td className="py-2 pr-3 text-right">{t.dte}d</td>
                      <td className="py-2 pr-3">
                        <div className="flex items-center justify-center gap-1">
                          <button
                            onClick={() => adjustContracts(t.key, -1)}
                            disabled={t.contracts === 0}
                            className="rounded p-0.5 text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-600 disabled:opacity-30"
                          >
                            <Minus className="h-3 w-3" />
                          </button>
                          <input
                            type="number"
                            min={0}
                            value={t.contracts}
                            onChange={(e) => setContracts(t.key, parseInt(e.target.value) || 0)}
                            className="w-12 rounded border border-gray-300 bg-white px-1 py-0.5 text-center font-mono text-xs focus:border-purple-500 focus:outline-none focus:ring-1 focus:ring-purple-500"
                          />
                          <button
                            onClick={() => adjustContracts(t.key, 1)}
                            className="rounded p-0.5 text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-600"
                          >
                            <Plus className="h-3 w-3" />
                          </button>
                        </div>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono">${t.premiumPerContract.toFixed(0)}</td>
                      <td className="py-2 pr-3 text-right font-mono text-emerald-600">
                        {t.enabled && t.contracts > 0 ? `$${rowPremium.toFixed(0)}` : "—"}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono">
                        {t.enabled && t.contracts > 0 ? `$${rowCollateral.toLocaleString()}` : "—"}
                      </td>
                      <td className="py-2 pr-3 text-right">
                        <PLValue value={t.annualizedReturnPct} format="percent" />
                      </td>
                      <td className="py-2 text-right">
                        {t.isRecommended ? (
                          <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-600">
                            {t.recommendedContracts}
                          </span>
                        ) : (
                          <span className="text-[10px] text-gray-300">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Add candidate button */}
          {availableCandidates.length > 0 && (
            <div className="relative mt-3">
              <button
                onClick={() => setAddMenuOpen(!addMenuOpen)}
                className="flex items-center gap-1.5 text-xs font-medium text-purple-600 transition-colors hover:text-purple-800"
              >
                <Plus className="h-3.5 w-3.5" />
                Add from {availableCandidates.length} other candidate{availableCandidates.length !== 1 ? "s" : ""}
              </button>
              {addMenuOpen && (
                <div className="absolute left-0 top-7 z-10 max-h-48 w-80 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg">
                  {availableCandidates.map((c) => (
                    <button
                      key={c.key}
                      onClick={() => {
                        adjustContracts(c.key, 1);
                        setAddMenuOpen(false);
                      }}
                      className="flex w-full items-center justify-between px-3 py-2 text-left text-xs transition-colors hover:bg-purple-50"
                    >
                      <span className="font-semibold text-gray-900">{c.symbol}</span>
                      <span className="text-gray-500">
                        ${c.strike.toFixed(2)} · {c.expiration} · Prem ${c.premiumPerContract.toFixed(0)}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ── Comparison: Recommended vs Custom ──────────────── */}
          <div className="mt-5 rounded-lg border border-purple-200 bg-white p-4">
            <h4 className="mb-3 text-xs font-bold uppercase tracking-wide text-gray-500">
              Recommended vs Your Selection
            </h4>
            <div className="grid grid-cols-5 gap-3 text-center text-xs">
              <div />
              <div className="font-semibold text-gray-400">Contracts</div>
              <div className="font-semibold text-gray-400">Premium</div>
              <div className="font-semibold text-gray-400">Commission</div>
              <div className="font-semibold text-gray-400">Net</div>

              <div className="text-left font-medium text-gray-500">Recommended</div>
              <div className="font-mono text-gray-700">{recTotals.contracts}</div>
              <div className="font-mono text-emerald-600">${recTotals.premium.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
              <div className="font-mono text-red-500">-${recTotals.commission.toFixed(2)}</div>
              <div className="font-mono font-semibold text-blue-600">${recTotals.net.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>

              <div className="text-left font-medium text-purple-700">Your Selection</div>
              <div className="font-mono text-gray-700">{pgTotals.contracts}</div>
              <div className="font-mono text-emerald-600">${pgTotals.premium.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
              <div className="font-mono text-red-500">-${pgTotals.commission.toFixed(2)}</div>
              <div className="font-mono font-semibold text-purple-700">${pgTotals.net.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>

              {pgChanged && (
                <>
                  <div className="text-left font-medium text-gray-400">Difference</div>
                  <div className={`font-mono ${pgTotals.contracts - recTotals.contracts >= 0 ? "text-emerald-600" : "text-red-500"}`}>
                    {pgTotals.contracts - recTotals.contracts >= 0 ? "+" : ""}
                    {pgTotals.contracts - recTotals.contracts}
                  </div>
                  <div className={`font-mono ${pgTotals.premium - recTotals.premium >= 0 ? "text-emerald-600" : "text-red-500"}`}>
                    {pgTotals.premium - recTotals.premium >= 0 ? "+" : ""}${(pgTotals.premium - recTotals.premium).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </div>
                  <div className={`font-mono ${pgTotals.commission - recTotals.commission <= 0 ? "text-emerald-600" : "text-red-500"}`}>
                    {pgTotals.commission - recTotals.commission > 0 ? "-" : "+"}${Math.abs(pgTotals.commission - recTotals.commission).toFixed(2)}
                  </div>
                  <div className={`font-mono font-semibold ${pgTotals.net - recTotals.net >= 0 ? "text-emerald-600" : "text-red-500"}`}>
                    {pgTotals.net - recTotals.net >= 0 ? "+" : ""}${(pgTotals.net - recTotals.net).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </div>
                </>
              )}
            </div>

            {/* Collateral comparison */}
            <div className="mt-3 flex items-center justify-between border-t border-gray-100 pt-3 text-xs">
              <div className="text-gray-500">
                Capital: <span className="font-mono font-semibold text-gray-700">${recTotals.collateral.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                <span className="mx-1.5 text-gray-300">→</span>
                <span className="font-mono font-semibold text-purple-700">${pgTotals.collateral.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                {capitalCeiling > 0 && (
                  <span className="ml-1.5 text-gray-400">
                    of ${capitalCeiling.toLocaleString()}
                  </span>
                )}
              </div>
              {pgChanged && (
                <span className={`font-mono font-semibold ${pgTotals.collateral - recTotals.collateral > 0 ? "text-amber-600" : "text-emerald-600"}`}>
                  {pgTotals.collateral - recTotals.collateral >= 0 ? "+" : ""}
                  ${(pgTotals.collateral - recTotals.collateral).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </span>
              )}
            </div>
          </div>
        </div>
      )}
    </Card>
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

const COMFORT_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };
const CONFIDENCE_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };

function LlmAnalysesCard({ analyses }: { analyses: CSPAnalysis[] }) {
  const sorted = useMemo(() => {
    return [...analyses].sort((a, b) => {
      const comfortDiff = (COMFORT_RANK[b.assignment_comfort] ?? 0) - (COMFORT_RANK[a.assignment_comfort] ?? 0);
      if (comfortDiff !== 0) return comfortDiff;
      return (CONFIDENCE_RANK[b.confidence] ?? 0) - (CONFIDENCE_RANK[a.confidence] ?? 0);
    });
  }, [analyses]);

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Brain className="h-4 w-4 text-purple-500" />
          AI Analysis
        </span>
      }
      subtitle="Sorted by assignment comfort & confidence — safest trades first"
    >
      <div className="space-y-4">
        {sorted.map((a) => (
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
