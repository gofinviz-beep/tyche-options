import { useCallback, useMemo, useState } from "react";
import { Card } from "@/components/Card";
import {
  useCCAnalysis,
  useStockPositions,
  useCreatePosition,
  useDeletePosition,
} from "@/hooks/useApi";
import { api } from "@/api/client";
import type { CCPosition, CCDeepDive, CCPortfolioAnalysis, StockPosition } from "@/types";
import {
  ChevronDown,
  ChevronRight,
  Plus,
  Trash2,
  TrendingUp,
  AlertTriangle,
  Clock,
  CalendarDays,
  XCircle,
  Upload,
  RefreshCw,
} from "lucide-react";

const LEGACY_STORAGE_KEY = "tyche_cc_positions";

function stockPositionToCCPosition(sp: StockPosition): CCPosition {
  return {
    ticker: sp.ticker,
    shares: sp.quantity,
    cost_basis: sp.purchase_price,
  };
}

const SIGNAL_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  SELL: { bg: "bg-emerald-100", text: "text-emerald-800", border: "border-emerald-300" },
  GO: { bg: "bg-emerald-50", text: "text-emerald-700", border: "border-emerald-200" },
  CONSIDER: { bg: "bg-amber-50", text: "text-amber-700", border: "border-amber-200" },
  CAUTION: { bg: "bg-amber-50", text: "text-amber-700", border: "border-amber-200" },
  WAIT: { bg: "bg-gray-50", text: "text-gray-600", border: "border-gray-200" },
  SKIP: { bg: "bg-red-50", text: "text-red-700", border: "border-red-200" },
};

function SignalBadge({ signal }: { signal: string }) {
  const s = SIGNAL_STYLES[signal] ?? SIGNAL_STYLES.WAIT;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${s.bg} ${s.text} ${s.border}`}
    >
      {signal === "SELL" && <TrendingUp className="h-3 w-3" />}
      {signal === "GO" && <TrendingUp className="h-3 w-3" />}
      {signal === "CONSIDER" && <AlertTriangle className="h-3 w-3" />}
      {signal === "CAUTION" && <AlertTriangle className="h-3 w-3" />}
      {signal === "WAIT" && <Clock className="h-3 w-3" />}
      {signal === "SKIP" && <XCircle className="h-3 w-3" />}
      {signal}
    </span>
  );
}

function PositionForm({
  onAdd,
}: {
  onAdd: (pos: CCPosition) => void;
}) {
  const [ticker, setTicker] = useState("");
  const [shares, setShares] = useState("100");
  const [costBasis, setCostBasis] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticker.trim()) return;
    onAdd({
      ticker: ticker.trim().toUpperCase(),
      shares: parseInt(shares) || 100,
      cost_basis: parseFloat(costBasis) || 0,
    });
    setTicker("");
    setShares("100");
    setCostBasis("");
  };

  return (
    <form onSubmit={handleSubmit} className="flex items-end gap-3">
      <div className="flex-1">
        <label className="block text-xs font-medium text-gray-500 mb-1">
          Ticker
        </label>
        <input
          type="text"
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          placeholder="PL"
          className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
        />
      </div>
      <div className="w-24">
        <label className="block text-xs font-medium text-gray-500 mb-1">
          Shares
        </label>
        <input
          type="number"
          value={shares}
          onChange={(e) => setShares(e.target.value)}
          min={1}
          className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
        />
      </div>
      <div className="w-28">
        <label className="block text-xs font-medium text-gray-500 mb-1">
          Cost Basis
        </label>
        <input
          type="number"
          step="0.01"
          value={costBasis}
          onChange={(e) => setCostBasis(e.target.value)}
          placeholder="0.00"
          className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
        />
      </div>
      <button
        type="submit"
        className="flex items-center gap-1 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
      >
        <Plus className="h-4 w-4" /> Add
      </button>
    </form>
  );
}

function PortfolioSummary({
  summary,
}: {
  summary: CCPortfolioAnalysis["portfolio_summary"];
}) {
  return (
    <div className="flex gap-4 text-sm">
      <div className="flex items-center gap-2 rounded-lg bg-emerald-50 border border-emerald-200 px-3 py-1.5">
        <TrendingUp className="h-4 w-4 text-emerald-600" />
        <span className="font-semibold text-emerald-700">
          {summary.positions_go} GO
        </span>
      </div>
      <div className="flex items-center gap-2 rounded-lg bg-amber-50 border border-amber-200 px-3 py-1.5">
        <AlertTriangle className="h-4 w-4 text-amber-600" />
        <span className="font-semibold text-amber-700">
          {summary.positions_caution} Caution
        </span>
      </div>
      <div className="flex items-center gap-2 rounded-lg bg-gray-50 border border-gray-200 px-3 py-1.5">
        <Clock className="h-4 w-4 text-gray-500" />
        <span className="font-semibold text-gray-600">
          {summary.positions_wait} Wait
        </span>
      </div>
      {summary.total_premium_est > 0 && (
        <div className="flex items-center gap-1 rounded-lg bg-blue-50 border border-blue-200 px-3 py-1.5">
          <span className="text-blue-700 font-semibold">
            Est. Premium: ${summary.total_premium_est.toLocaleString()}
          </span>
        </div>
      )}
    </div>
  );
}

function EMAReversionCard({
  label,
  data,
}: {
  label: string;
  data: Record<string, number>;
}) {
  if (!data || Object.keys(data).length === 0) return null;
  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
        {label}
      </h4>
      <div className="grid grid-cols-5 gap-2 text-center text-sm">
        {[
          ["Mean", data.mean],
          ["Median", data.median],
          ["P25", data.p25],
          ["P75", data.p75],
          ["P90", data.p90],
        ]
          .filter(([, v]) => v != null)
          .map(([k, v]) => (
            <div key={String(k)}>
              <div className="text-xs text-gray-400">{String(k)}</div>
              <div className="font-mono font-semibold">{v}d</div>
            </div>
          ))}
      </div>
      {data.count != null && (
        <div className="text-xs text-gray-400 mt-1 text-right">
          ({data.count} episodes)
        </div>
      )}
    </div>
  );
}

function ForwardReturnsChart({
  data,
}: {
  data: Record<string, unknown>[];
}) {
  if (!data || data.length === 0) return null;
  const maxPct = Math.max(
    ...data.map((d) => Math.abs(Number(d.pct_above_entry) || 0)),
  );
  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
        Forward Returns After Extension (mapped to upcoming dates)
      </h4>
      <div className="space-y-1">
        {data.map((d) => {
          const pct = Number(d.pct_above_entry) || 0;
          const width = maxPct > 0 ? (pct / 100) * 100 : 0;
          const dayLabel = d.day_label as string | undefined;
          const hasDayLabel = !!dayLabel;
          const isTueWed =
            hasDayLabel && (dayLabel.includes("Tue") || dayLabel.includes("Wed"));
          return (
            <div key={Number(d.day)} className="flex items-center gap-2 text-xs">
              <span
                className={`w-28 text-right font-mono ${
                  isTueWed
                    ? "text-emerald-700 font-semibold"
                    : "text-gray-500"
                }`}
              >
                {hasDayLabel ? dayLabel : `D${Number(d.day)}`}
              </span>
              <div className="flex-1 h-4 bg-gray-100 rounded overflow-hidden">
                <div
                  className={`h-full rounded ${pct >= 60 ? "bg-emerald-400" : pct >= 50 ? "bg-emerald-300" : "bg-amber-300"}`}
                  style={{ width: `${Math.min(width, 100)}%` }}
                />
              </div>
              <span className="w-16 text-right font-mono">
                {pct.toFixed(0)}% up
              </span>
              <span className="w-16 text-right font-mono text-gray-400">
                avg {Number(d.avg_ret).toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DOWTable({ data }: { data: Record<string, unknown>[] }) {
  if (!data || data.length === 0) return null;
  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
        Day-of-Week Entry Analysis
      </h4>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-gray-400 border-b">
            <th className="text-left py-1">Day</th>
            <th className="text-right py-1">Trades</th>
            <th className="text-right py-1">Win %</th>
            <th className="text-right py-1">Called %</th>
            <th className="text-right py-1">Avg Ret</th>
            <th className="text-right py-1">Ret/CalDay</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={String(d.day)} className="border-b border-gray-100">
              <td className="py-1 font-medium">{String(d.day)}</td>
              <td className="text-right py-1 font-mono">{Number(d.trades)}</td>
              <td className="text-right py-1 font-mono">
                {Number(d.win_pct).toFixed(1)}%
              </td>
              <td className="text-right py-1 font-mono">
                {Number(d.called_pct).toFixed(1)}%
              </td>
              <td className="text-right py-1 font-mono">
                {Number(d.avg_return).toFixed(2)}%
              </td>
              <td className="text-right py-1 font-mono font-semibold">
                {Number(d.ret_per_calday).toFixed(4)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CallCandidatesTable({
  data,
}: {
  data: Record<string, unknown>[] | null;
}) {
  if (!data || data.length === 0) return null;
  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
        Call Option Chain (from history)
      </h4>
      <div className="max-h-64 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-white">
            <tr className="text-xs text-gray-400 border-b">
              <th className="text-left py-1">Strike</th>
              <th className="text-right py-1">OTM%</th>
              <th className="text-left py-1">Expiry</th>
              <th className="text-right py-1">DTE</th>
              <th className="text-right py-1">Prem</th>
              <th className="text-right py-1">Per 100</th>
              <th className="text-right py-1">Vol</th>
              <th className="text-right py-1">Ann%</th>
            </tr>
          </thead>
          <tbody>
            {data.map((c, i) => (
              <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="py-1 font-mono font-semibold">
                  ${Number(c.strike).toFixed(1)}
                </td>
                <td className="text-right py-1 font-mono">
                  {Number(c.otm_pct).toFixed(1)}%
                </td>
                <td className="py-1">{String(c.expiration)}</td>
                <td className="text-right py-1 font-mono">{Number(c.dte)}</td>
                <td className="text-right py-1 font-mono">
                  ${Number(c.premium).toFixed(2)}
                </td>
                <td className="text-right py-1 font-mono text-emerald-600">
                  ${Number(c.per_100_shares).toFixed(0)}
                </td>
                <td className="text-right py-1 font-mono">
                  {Number(c.volume)}
                </td>
                <td className="text-right py-1 font-mono font-semibold">
                  {Number(c.annualized_return).toFixed(0)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PnlScenarios({
  data,
  shares,
}: {
  data: Record<string, unknown>;
  shares: number;
}) {
  if (!data || Object.keys(data).length === 0) return null;
  const notCalled = data.if_not_called as Record<string, unknown> | undefined;
  const called = data.if_called as Record<string, unknown> | undefined;
  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
        P&L Scenarios ({shares} shares, {Number(data.contracts)} contracts)
      </h4>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-xs text-emerald-600 font-semibold mb-1">
            If NOT Called (Ideal)
          </div>
          {notCalled && (
            <div className="text-sm space-y-0.5">
              <div>
                Premium:{" "}
                <span className="font-mono text-emerald-700">
                  ${Number(notCalled.premium_income).toLocaleString()}
                </span>
              </div>
              <div>
                Shares kept:{" "}
                <span className="font-mono">{Number(notCalled.shares_kept)}</span>
              </div>
              {notCalled.unrealized_gain != null && (
                <div>
                  Unrealized:{" "}
                  <span className="font-mono">
                    ${Number(notCalled.unrealized_gain).toLocaleString()}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
        <div>
          <div className="text-xs text-amber-600 font-semibold mb-1">
            If Called Away
          </div>
          {called && (
            <div className="text-sm space-y-0.5">
              <div>
                Stock gain:{" "}
                <span className="font-mono">
                  ${Number(called.stock_gain).toLocaleString()}
                </span>
              </div>
              <div>
                + Premium:{" "}
                <span className="font-mono text-emerald-700">
                  ${Number(called.premium_income).toLocaleString()}
                </span>
              </div>
              <div>
                Total:{" "}
                <span className="font-mono font-semibold">
                  ${Number(called.total_gain).toLocaleString()}
                </span>
              </div>
              {called.total_return_pct != null && (
                <div>
                  Return:{" "}
                  <span className="font-mono font-semibold">
                    {Number(called.total_return_pct).toFixed(1)}%
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      {data.commission != null && (
        <div className="text-xs text-gray-400 mt-1">
          Commission: ${Number(data.commission).toFixed(2)}
        </div>
      )}
    </div>
  );
}

function RecommendedActionCard({
  rec,
}: {
  rec: CCDeepDive["recommended_action"];
}) {
  if (!rec || !rec.action) return null;

  const isSell = rec.action === "SELL";
  const isConsider = rec.action === "CONSIDER";
  const isWait = rec.action === "WAIT";
  const isSkip = rec.action === "SKIP";
  const isLive = rec.premium_source === "live_tradier";

  const borderColor = isSell
    ? "border-emerald-300"
    : isConsider
      ? "border-amber-300"
      : isSkip
        ? "border-red-200"
        : "border-gray-300";
  const bgColor = isSell
    ? "bg-emerald-50"
    : isConsider
      ? "bg-amber-50"
      : isSkip
        ? "bg-red-50"
        : "bg-gray-50";

  return (
    <div className={`rounded-lg border-2 ${borderColor} ${bgColor} p-4`}>
      <div className="flex items-start justify-between gap-4 mb-3">
        <div>
          <div className="text-xs font-semibold text-gray-500 uppercase mb-1">
            Recommended Action
          </div>
          <div className="text-lg font-bold text-gray-900 font-mono">
            {rec.instruction}
          </div>
        </div>
        {!isSkip && !isWait && rec.net_premium_est != null && rec.net_premium_est > 0 && (
          <div className="text-right shrink-0">
            <div className="text-xs text-gray-500">Net Premium</div>
            <div className="text-xl font-bold text-emerald-700 font-mono">
              ${rec.net_premium_est.toLocaleString()}
            </div>
            <div className={`text-[10px] mt-0.5 ${isLive ? "text-emerald-600" : "text-amber-600"}`}>
              {isLive ? "● Live" : "⚠ Estimated"}
            </div>
          </div>
        )}
      </div>

      {rec.price_source === "live_tradier" && rec.live_price != null && rec.prev_close != null && (
        <div className="flex items-center gap-2 text-xs mb-3 px-2 py-1.5 rounded bg-blue-50 border border-blue-200">
          <span className="inline-flex items-center gap-1 text-blue-600 font-semibold">
            ● Live Price: ${rec.live_price.toFixed(2)}
          </span>
          <span className={rec.live_price >= rec.prev_close ? "text-emerald-600" : "text-red-600"}>
            {rec.live_price >= rec.prev_close ? "+" : ""}
            {((rec.live_price / rec.prev_close - 1) * 100).toFixed(1)}% from prev close (${rec.prev_close.toFixed(2)})
          </span>
        </div>
      )}

      {!isSkip && !isWait && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm mb-3">
          <div>
            <span className="text-gray-500">Strike:</span>{" "}
            <span className="font-mono font-semibold">${rec.strike.toFixed(1)}</span>
            <span className="text-gray-400 ml-1">({rec.otm_pct}% OTM)</span>
          </div>
          <div>
            <span className="text-gray-500">Expiry:</span>{" "}
            <span className="font-mono font-semibold">{rec.expiration_label ?? rec.expiration_date}</span>
            <span className="text-gray-400 ml-1">({rec.actual_dte}d)</span>
          </div>
          <div>
            <span className="text-gray-500">Enter:</span>{" "}
            <span className="font-semibold">{rec.entry_timing}</span>
          </div>
          <div>
            <span className="text-gray-500">Contracts:</span>{" "}
            <span className="font-mono font-semibold">{rec.contracts}</span>
          </div>
        </div>
      )}

      {!isSkip && !isWait && isLive && rec.live_bid != null && (
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2 text-xs bg-white/60 rounded px-3 py-2 mb-3 border border-gray-200">
          <div>
            <span className="text-gray-500">Bid:</span>{" "}
            <span className="font-mono font-semibold">${rec.live_bid.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-gray-500">Ask:</span>{" "}
            <span className="font-mono font-semibold">${rec.live_ask?.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-gray-500">Mid:</span>{" "}
            <span className="font-mono font-semibold">${rec.live_mid?.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-gray-500">IV:</span>{" "}
            <span className="font-mono font-semibold">{rec.live_iv ? `${(rec.live_iv * 100).toFixed(0)}%` : "—"}</span>
          </div>
          <div>
            <span className="text-gray-500">Δ:</span>{" "}
            <span className="font-mono font-semibold">{rec.live_delta?.toFixed(3) ?? "—"}</span>
          </div>
          <div>
            <span className="text-gray-500">Θ:</span>{" "}
            <span className="font-mono font-semibold">{rec.live_theta?.toFixed(3) ?? "—"}</span>
          </div>
        </div>
      )}

      {!isSkip && !isWait && (
        <div className="grid grid-cols-2 gap-3 text-sm mb-3">
          <div className="flex items-center gap-2">
            <div className="h-2 w-2 rounded-full bg-emerald-500" />
            <span className="text-gray-600">
              Pullback prob by expiry:{" "}
              <span className="font-mono font-semibold">{rec.pullback_prob_by_expiry}%</span>
            </span>
          </div>
          <div className="flex items-center gap-2">
            <div
              className={`h-2 w-2 rounded-full ${
                rec.assignment_prob <= 5 ? "bg-emerald-500" : "bg-amber-500"
              }`}
            />
            <span className="text-gray-600">
              Assignment risk:{" "}
              <span className="font-mono font-semibold">{rec.assignment_prob}%</span>
            </span>
          </div>
        </div>
      )}

      {rec.safety_reasons.length > 0 && (
        <div className="space-y-1 mb-2">
          {rec.safety_reasons.map((r, i) => (
            <div key={i} className="flex items-start gap-1.5 text-xs text-emerald-700">
              <span className="mt-0.5">✓</span>
              <span>{r}</span>
            </div>
          ))}
        </div>
      )}

      {rec.warnings.length > 0 && (
        <div className="space-y-1">
          {rec.warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-1.5 text-xs text-amber-700">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DeepDivePanel({
  dd,
  pos,
}: {
  dd: CCDeepDive;
  pos: CCPosition;
}) {
  return (
    <div className="bg-gray-50 border-t border-gray-200 px-6 py-4 space-y-4">
      {/* Recommended Action — the actionable punchline */}
      <RecommendedActionCard rec={dd.recommended_action} />

      {/* EMA Reversion Timing */}
      <div className="grid grid-cols-3 gap-3">
        <EMAReversionCard label="Days to 8-EMA" data={dd.days_to_8ema} />
        <EMAReversionCard label="Days to 21-EMA" data={dd.days_to_21ema} />
        <EMAReversionCard label="Days to 50-EMA" data={dd.days_to_50ema} />
      </div>

      {/* Forward Returns */}
      <ForwardReturnsChart data={dd.forward_returns} />

      {/* DOW Analysis */}
      <DOWTable data={dd.dow_analysis} />

      {/* Rally Peak Distribution */}
      {dd.rally_peak_day_distribution &&
        dd.rally_peak_day_distribution.total > 0 && (
          <div className="rounded-lg border border-gray-200 p-3">
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
              Rally Peak Timing
            </h4>
            <div className="flex gap-4 text-sm">
              <div>
                Days 1–3:{" "}
                <span className="font-mono font-semibold">
                  {dd.rally_peak_day_distribution.days_1_3}
                </span>
              </div>
              <div>
                Days 4–6:{" "}
                <span className="font-mono font-semibold">
                  {dd.rally_peak_day_distribution.days_4_6}
                </span>
              </div>
              <div>
                Days 7+:{" "}
                <span className="font-mono font-semibold">
                  {dd.rally_peak_day_distribution.days_7_plus}
                </span>
              </div>
            </div>
          </div>
        )}

      {/* Call Candidates */}
      <CallCandidatesTable data={dd.call_candidates} />

      {/* P&L Scenarios */}
      <PnlScenarios data={dd.pnl_scenarios} shares={pos.shares} />

      {/* Episode History */}
      {dd.episode_table.length > 0 && (
        <div className="rounded-lg border border-gray-200 p-3">
          <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
            Extension Episodes ({dd.total_episodes} total, 10%+ above 8-EMA)
          </h4>
          <div className="max-h-48 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-white">
                <tr className="text-gray-400 border-b">
                  <th className="text-left py-1">Peak Date</th>
                  <th className="text-right py-1">Price</th>
                  <th className="text-right py-1">Ext%</th>
                  <th className="text-right py-1">+Rally%</th>
                  <th className="text-right py-1">Rally Days</th>
                  <th className="text-right py-1">→8-EMA</th>
                  <th className="text-right py-1">→21-EMA</th>
                </tr>
              </thead>
              <tbody>
                {dd.episode_table.map((ep, i) => (
                  <tr key={i} className="border-b border-gray-100">
                    <td className="py-1">{String(ep.peak_date)}</td>
                    <td className="text-right py-1 font-mono">
                      ${Number(ep.peak_price).toFixed(2)}
                    </td>
                    <td className="text-right py-1 font-mono">
                      +{Number(ep.extension_pct).toFixed(1)}%
                    </td>
                    <td className="text-right py-1 font-mono text-emerald-600">
                      +{Number(ep.additional_rally_pct).toFixed(1)}%
                    </td>
                    <td className="text-right py-1 font-mono">
                      {Number(ep.rally_days)}d
                    </td>
                    <td className="text-right py-1 font-mono">
                      {ep.days_to_8ema != null
                        ? `${Number(ep.days_to_8ema)}d`
                        : "—"}
                    </td>
                    <td className="text-right py-1 font-mono">
                      {ep.days_to_21ema != null
                        ? `${Number(ep.days_to_21ema)}d`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export function CoveredCalls() {
  const { data: backendPositions, isLoading: positionsLoading } =
    useStockPositions(true);
  const createPosition = useCreatePosition();
  const deletePositionMutation = useDeletePosition();

  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);
  const [targetDte, setTargetDte] = useState(8);
  const [migrationStatus, setMigrationStatus] = useState<string | null>(null);
  const ccAnalysis = useCCAnalysis();

  const positions: CCPosition[] = useMemo(
    () => (backendPositions ?? []).map(stockPositionToCCPosition),
    [backendPositions],
  );

  const addPosition = useCallback(
    (pos: CCPosition) => {
      createPosition.mutate({
        ticker: pos.ticker.toUpperCase(),
        purchase_price: pos.cost_basis || 0,
        quantity: pos.shares,
        purchase_date: new Date().toISOString().split("T")[0],
        pullback_type: "manual",
      });
    },
    [createPosition],
  );

  const removePosition = useCallback(
    (ticker: string) => {
      const sp = backendPositions?.find((p) => p.ticker === ticker);
      if (sp) {
        deletePositionMutation.mutate(sp.id);
      }
    },
    [backendPositions, deletePositionMutation],
  );

  const migrateFromLocalStorage = useCallback(async () => {
    const raw = localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!raw) {
      setMigrationStatus("No localStorage positions found.");
      return;
    }
    try {
      const legacy: CCPosition[] = JSON.parse(raw);
      if (!legacy.length) {
        setMigrationStatus("No positions to migrate.");
        return;
      }
      setMigrationStatus(`Importing ${legacy.length} positions...`);
      const result = await api.stocks.bulkImportPositions(
        legacy.map((p) => ({
          ticker: p.ticker,
          quantity: p.shares,
          purchase_price: p.cost_basis || 0,
        })),
      );
      localStorage.removeItem(LEGACY_STORAGE_KEY);
      setMigrationStatus(
        `Done: ${result.created} created, ${result.skipped} skipped` +
          (result.errors.length ? `, ${result.errors.length} errors` : ""),
      );
    } catch (err) {
      setMigrationStatus(`Migration failed: ${(err as Error).message}`);
    }
  }, []);

  const handleAnalyze = () => {
    if (positions.length === 0) return;
    ccAnalysis.mutate({ positions, targetDte });
  };

  const data = ccAnalysis.data;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Covered Call Recommender
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Enter your positions and get Go/Wait/Caution signals for selling covered calls
          </p>
        </div>
      </div>

      {/* Migration Banner */}
      {typeof window !== "undefined" && localStorage.getItem(LEGACY_STORAGE_KEY) && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 flex items-center justify-between">
          <div className="text-sm text-amber-800">
            <Upload className="h-4 w-4 inline mr-1.5" />
            You have positions in localStorage from before. Import them to the backend?
          </div>
          <div className="flex items-center gap-2">
            {migrationStatus && (
              <span className="text-xs text-amber-700">{migrationStatus}</span>
            )}
            <button
              onClick={migrateFromLocalStorage}
              className="rounded-md bg-amber-600 px-3 py-1 text-xs font-medium text-white hover:bg-amber-700"
            >
              Import Now
            </button>
            <button
              onClick={() => {
                localStorage.removeItem(LEGACY_STORAGE_KEY);
                setMigrationStatus("Cleared.");
              }}
              className="rounded-md border border-amber-300 px-3 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100"
            >
              Discard
            </button>
          </div>
        </div>
      )}

      {/* Position Entry */}
      <Card title="Stock Holdings">
        <div className="space-y-4">
          <p className="text-xs text-gray-500">
            Positions are stored on the server (positions.db) and shared with the Stocks Dashboard.
          </p>
          <PositionForm onAdd={addPosition} />

          {positionsLoading && (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <RefreshCw className="h-4 w-4 animate-spin" /> Loading positions...
            </div>
          )}

          {positions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {positions.map((p) => (
                <div
                  key={p.ticker}
                  className="flex items-center gap-2 rounded-md bg-gray-100 px-3 py-1.5 text-sm"
                >
                  <span className="font-mono font-bold">{p.ticker}</span>
                  <span className="text-gray-500">
                    {p.shares}sh
                    {p.cost_basis > 0 && ` @ $${p.cost_basis.toFixed(2)}`}
                  </span>
                  <button
                    onClick={() => removePosition(p.ticker)}
                    className="text-gray-400 hover:text-red-500"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-gray-500">
                Target DTE
              </label>
              <select
                value={targetDte}
                onChange={(e) => setTargetDte(parseInt(e.target.value))}
                className="rounded-md border border-gray-300 px-2 py-1 text-sm"
              >
                <option value={5}>5 days</option>
                <option value={8}>8 days (1 week)</option>
                <option value={14}>14 days (2 weeks)</option>
                <option value={21}>21 days (3 weeks)</option>
                <option value={30}>30 days</option>
              </select>
            </div>
            <button
              onClick={handleAnalyze}
              disabled={positions.length === 0 || ccAnalysis.isPending}
              className="flex items-center gap-1.5 rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {ccAnalysis.isPending ? (
                <>
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                  Analyzing...
                </>
              ) : (
                <>
                  <TrendingUp className="h-4 w-4" /> Analyze All
                </>
              )}
            </button>
          </div>
        </div>
      </Card>

      {/* Error */}
      {ccAnalysis.isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Analysis failed: {(ccAnalysis.error as Error)?.message ?? "Unknown error"}
        </div>
      )}

      {/* Portfolio Summary */}
      {data && (
        <PortfolioSummary summary={data.portfolio_summary} />
      )}

      {/* Signal Dashboard */}
      {data && data.analyses.length > 0 && (
        <Card title="Signal Dashboard">
          <div className="divide-y divide-gray-100">
            {/* Header */}
            <div className="grid grid-cols-12 gap-2 px-4 py-2 text-xs font-semibold text-gray-400 uppercase">
              <div className="col-span-1" />
              <div className="col-span-1">Ticker</div>
              <div className="col-span-1">Signal</div>
              <div className="col-span-1 text-right">Price</div>
              <div className="col-span-1 text-right">Ext 8-EMA</div>
              <div className="col-span-1 text-right">RSI</div>
              <div className="col-span-1 text-right">Strike</div>
              <div className="col-span-1 text-right">OTM%</div>
              <div className="col-span-1 text-right">Assign 1w</div>
              <div className="col-span-1 text-right">Premium</div>
              <div className="col-span-1 text-center">Best Day</div>
              <div className="col-span-1 text-center">Earnings</div>
            </div>

            {/* Rows */}
            {data.analyses.map((a) => {
              const sig = a.signal;
              const pos = positions.find((p) => p.ticker === sig.ticker);
              const isExpanded = expandedTicker === sig.ticker;
              const contracts = (pos?.shares ?? 100) / 100;
              const displaySignal = a.recommended_action?.action || sig.signal;
              return (
                <div key={sig.ticker}>
                  <div
                    className="grid grid-cols-12 gap-2 px-4 py-2.5 text-sm hover:bg-gray-50 cursor-pointer items-center"
                    onClick={() =>
                      setExpandedTicker(isExpanded ? null : sig.ticker)
                    }
                  >
                    <div className="col-span-1">
                      {isExpanded ? (
                        <ChevronDown className="h-4 w-4 text-gray-400" />
                      ) : (
                        <ChevronRight className="h-4 w-4 text-gray-400" />
                      )}
                    </div>
                    <div className="col-span-1 font-mono font-bold text-gray-900">
                      {sig.ticker}
                    </div>
                    <div className="col-span-1">
                      <SignalBadge signal={displaySignal} />
                    </div>
                    <div className="col-span-1 text-right font-mono">
                      <span>${sig.last_close.toFixed(2)}</span>
                      {sig.price_source === "live_tradier" && sig.prev_close != null && (
                        <div className="text-[10px]">
                          <span className="text-emerald-500">● Live</span>
                          {" "}
                          <span className="text-gray-400">
                            {sig.last_close >= sig.prev_close ? "+" : ""}
                            {((sig.last_close / sig.prev_close - 1) * 100).toFixed(1)}%
                          </span>
                        </div>
                      )}
                    </div>
                    <div className="col-span-1 text-right font-mono">
                      <span
                        className={
                          sig.extension_pct_8 >= 10
                            ? "text-emerald-600 font-semibold"
                            : sig.extension_pct_8 >= 5
                              ? "text-amber-600"
                              : "text-gray-500"
                        }
                      >
                        +{sig.extension_pct_8.toFixed(1)}%
                      </span>
                    </div>
                    <div className="col-span-1 text-right font-mono">
                      {sig.rsi_14.toFixed(0)}
                    </div>
                    <div className="col-span-1 text-right font-mono font-semibold">
                      ${sig.suggested_strike.toFixed(1)}
                    </div>
                    <div className="col-span-1 text-right font-mono">
                      {sig.suggested_otm_pct.toFixed(1)}%
                    </div>
                    <div className="col-span-1 text-right font-mono">
                      {sig.assignment_prob_1w.toFixed(0)}%
                    </div>
                    <div className="col-span-1 text-right font-mono text-emerald-600">
                      {sig.suggested_premium_est != null
                        ? `$${(sig.suggested_premium_est * contracts * 100).toFixed(0)}`
                        : "—"}
                      {a.recommended_action?.premium_source === "live_tradier" && (
                        <span className="text-[9px] text-emerald-500 block">Live</span>
                      )}
                    </div>
                    <div className="col-span-1 text-center">
                      <span
                        className={`text-xs font-semibold px-1.5 py-0.5 rounded ${
                          sig.optimal_entry_day === "Tue" ||
                          sig.optimal_entry_day === "Wed"
                            ? "bg-emerald-100 text-emerald-700"
                            : "bg-gray-100 text-gray-600"
                        }`}
                      >
                        {sig.optimal_entry_day}
                      </span>
                    </div>
                    <div className="col-span-1 text-center">
                      {sig.earnings_in_window ? (
                        <span className="flex items-center justify-center text-amber-600">
                          <CalendarDays className="h-4 w-4" />
                        </span>
                      ) : (
                        <span className="text-gray-300">—</span>
                      )}
                    </div>
                  </div>
                  {isExpanded && pos && (
                    <DeepDivePanel dd={a} pos={pos} />
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* Empty State */}
      {!data && !ccAnalysis.isPending && (
        <Card title="Getting Started">
          <div className="text-center py-8 text-gray-500">
            <TrendingUp className="h-12 w-12 mx-auto mb-3 text-gray-300" />
            <p className="text-lg font-medium text-gray-700 mb-2">
              Add your positions above
            </p>
            <p className="text-sm max-w-md mx-auto">
              Enter the stocks you own (ticker, shares, and optionally cost basis),
              then click "Analyze All" to get covered call recommendations based on
              extension episodes, EMA reversion, and day-of-week analysis.
            </p>
          </div>
        </Card>
      )}
    </div>
  );
}
