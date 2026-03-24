import { useState } from "react";
import { Card } from "@/components/Card";
import { PLValue } from "@/components/PLValue";
import { StatusBadge } from "@/components/StatusBadge";
import { useLatestScan, useTriggerScan } from "@/hooks/useApi";
import type { CSPCandidate } from "@/types";

export function Scanner() {
  const { data: scan } = useLatestScan();
  const triggerScan = useTriggerScan();
  const [symbols, setSymbols] = useState("");

  const handleScan = () => {
    triggerScan.mutate({ symbols: symbols || undefined, topN: 10 });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Scanner</h1>
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="AAPL,PL,MSFT..."
            value={symbols}
            onChange={(e) => setSymbols(e.target.value)}
            className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:border-blue-500 focus:outline-none"
          />
          <button
            onClick={handleScan}
            disabled={triggerScan.isPending}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {triggerScan.isPending ? "Scanning..." : "Run Scan"}
          </button>
        </div>
      </div>

      {triggerScan.isError && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
          Scan failed: {triggerScan.error.message}
        </div>
      )}

      {/* CSP Candidates */}
      <Card
        title="Cash-Secured Put Candidates"
        subtitle={
          scan
            ? `${scan.csp_candidates.length} candidates · Scanned ${scan.symbols_scanned} symbols`
            : "No scan results yet"
        }
      >
        {scan?.csp_candidates.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-xs text-gray-500">
                  <th className="pb-2 pr-4">Symbol</th>
                  <th className="pb-2 pr-4 text-right">Strike</th>
                  <th className="pb-2 pr-4 text-right">Exp</th>
                  <th className="pb-2 pr-4 text-right">DTE</th>
                  <th className="pb-2 pr-4 text-right">Bid</th>
                  <th className="pb-2 pr-4 text-right">Premium/C</th>
                  <th className="pb-2 pr-4 text-right">Ann. Return</th>
                  <th className="pb-2 pr-4 text-right">Delta</th>
                  <th className="pb-2 pr-4 text-right">OI</th>
                  <th className="pb-2 pr-4 text-right">Vol</th>
                  <th className="pb-2 text-right">Score</th>
                </tr>
              </thead>
              <tbody>
                {scan.csp_candidates.map((c: CSPCandidate) => (
                  <CandidateRow key={c.option_symbol} candidate={c} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-500">
            Run a scan to see candidates. Enter symbols above or configure your
            watchlist in Settings.
          </p>
        )}
      </Card>

      {/* CC Candidates */}
      {scan?.cc_candidates.length ? (
        <Card
          title="Covered Call Candidates"
          subtitle={`${scan.cc_candidates.length} candidates on held shares`}
        >
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-xs text-gray-500">
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
                {scan.cc_candidates.map((c: CSPCandidate) => (
                  <tr
                    key={c.option_symbol}
                    className="border-b border-gray-800/50 text-gray-300"
                  >
                    <td className="py-2.5 pr-4 font-semibold text-white">
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
                      <PLValue
                        value={c.annualized_return_pct}
                        format="percent"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {/* LLM Analyses */}
      {scan?.llm_analyses.length ? (
        <Card title="AI Analysis" subtitle="Gemini-powered trade reasoning">
          <div className="space-y-4">
            {scan.llm_analyses.map((a) => (
              <div
                key={a.ticker}
                className="rounded-lg border border-gray-800 bg-gray-800/40 p-4"
              >
                <div className="flex items-center justify-between">
                  <span className="text-lg font-bold text-white">
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
                <p className="mt-2 text-sm text-gray-400">{a.thesis}</p>
                <div className="mt-3 grid grid-cols-3 gap-3 text-xs">
                  <div>
                    <span className="text-gray-500">Strike: </span>
                    <span className="text-white">${a.recommended_strike}</span>
                  </div>
                  <div>
                    <span className="text-gray-500">Contracts: </span>
                    <span className="text-white">{a.suggested_contracts}</span>
                  </div>
                  <div>
                    <span className="text-gray-500">Ann. Return: </span>
                    <PLValue value={a.annualized_return_pct} format="percent" />
                  </div>
                </div>
                {a.risks.length > 0 && (
                  <div className="mt-2">
                    <span className="text-xs text-gray-500">Risks: </span>
                    <span className="text-xs text-amber-400">
                      {a.risks.join(" · ")}
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function CandidateRow({ candidate: c }: { candidate: CSPCandidate }) {
  return (
    <tr className="border-b border-gray-800/50 text-gray-300 hover:bg-gray-800/30">
      <td className="py-2.5 pr-4">
        <span className="font-semibold text-white">{c.symbol}</span>
        {c.earnings_within_dte && (
          <StatusBadge label="EARNINGS" variant="warning" />
        )}
      </td>
      <td className="py-2.5 pr-4 text-right font-mono">
        ${c.strike.toFixed(2)}
      </td>
      <td className="py-2.5 pr-4 text-right text-xs">{c.expiration}</td>
      <td className="py-2.5 pr-4 text-right">{c.dte}d</td>
      <td className="py-2.5 pr-4 text-right font-mono">${c.bid.toFixed(2)}</td>
      <td className="py-2.5 pr-4 text-right font-mono">
        ${c.premium_per_contract.toFixed(0)}
      </td>
      <td className="py-2.5 pr-4 text-right">
        <PLValue value={c.annualized_return_pct} format="percent" />
      </td>
      <td className="py-2.5 pr-4 text-right font-mono text-xs">
        {c.delta.toFixed(3)}
      </td>
      <td className="py-2.5 pr-4 text-right">
        {c.open_interest.toLocaleString()}
      </td>
      <td className="py-2.5 pr-4 text-right">
        {c.volume.toLocaleString()}
      </td>
      <td className="py-2.5 text-right font-mono font-semibold text-white">
        {c.score.toFixed(1)}
      </td>
    </tr>
  );
}
