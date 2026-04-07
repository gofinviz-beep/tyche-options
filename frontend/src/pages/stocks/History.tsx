import { useState } from "react";
import { Card } from "@/components/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { useConvictionHistory, useConvictionTransitions } from "@/hooks/useApi";

const TREND_VARIANTS: Record<string, "success" | "warning" | "danger" | "info" | "neutral"> = {
  strong_uptrend: "success",
  uptrend: "success",
  pullback_to_8ema: "warning",
  pullback_to_21ema: "danger",
  consolidation: "neutral",
  downtrend: "danger",
  insufficient_data: "neutral",
};

export function ConvictionHistory() {
  const [ticker, setTicker] = useState("");
  const [searchTicker, setSearchTicker] = useState("");
  const [days, setDays] = useState(30);

  const { data: historyData, isLoading: histLoading } = useConvictionHistory(
    searchTicker,
    days,
  );
  const { data: transitionsData } = useConvictionTransitions(
    7,
    "pullback_to_8ema,pullback_to_21ema",
  );

  const handleSearch = () => {
    if (ticker.trim()) {
      setSearchTicker(ticker.trim().toUpperCase());
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-900">
          Stocks — Conviction History
        </h1>
        <p className="text-sm text-gray-400">
          Historical trend states and state transitions
        </p>
      </div>

      <Card title="Recent Pullback Transitions" subtitle="Last 7 days">
        {transitionsData && transitionsData.transitions.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-left text-xs text-gray-400">
                  <th className="pb-2 pr-4">Date</th>
                  <th className="pb-2 pr-4">Ticker</th>
                  <th className="pb-2 pr-4">From</th>
                  <th className="pb-2 pr-4">To</th>
                  <th className="pb-2 pr-4">Price</th>
                  <th className="pb-2">Conviction</th>
                </tr>
              </thead>
              <tbody>
                {transitionsData.transitions.map((t) => (
                  <tr
                    key={t.id}
                    className="border-b border-gray-100 last:border-0"
                  >
                    <td className="py-2 pr-4 text-gray-500">
                      {t.transition_date}
                    </td>
                    <td className="py-2 pr-4">
                      <button
                        onClick={() => {
                          setTicker(t.ticker);
                          setSearchTicker(t.ticker);
                        }}
                        className="font-mono font-semibold text-blue-600 hover:underline"
                      >
                        {t.ticker}
                      </button>
                    </td>
                    <td className="py-2 pr-4">
                      <StatusBadge
                        label={t.from_state.replace(/_/g, " ")}
                        variant={TREND_VARIANTS[t.from_state] ?? "neutral"}
                      />
                    </td>
                    <td className="py-2 pr-4">
                      <StatusBadge
                        label={t.to_state.replace(/_/g, " ")}
                        variant={TREND_VARIANTS[t.to_state] ?? "neutral"}
                      />
                    </td>
                    <td className="py-2 pr-4">${t.last_close.toFixed(2)}</td>
                    <td className="py-2">
                      {(() => {
                        const conv = t.raw_conviction ?? t.conviction_level;
                        return (
                          <StatusBadge
                            label={conv}
                            variant={
                              conv === "high"
                                ? "success"
                                : conv === "medium"
                                  ? "warning"
                                  : "neutral"
                            }
                          />
                        );
                      })()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-400">
            No pullback transitions in the last 7 days.
          </p>
        )}
      </Card>

      <Card title="Ticker Conviction History">
        <div className="mb-4 flex gap-2">
          <input
            type="text"
            placeholder="Ticker (e.g. AAPL)"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm font-mono"
          />
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm"
          >
            <option value={7}>7 days</option>
            <option value={14}>14 days</option>
            <option value={30}>30 days</option>
            <option value={60}>60 days</option>
            <option value={90}>90 days</option>
          </select>
          <button
            onClick={handleSearch}
            className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          >
            Search
          </button>
        </div>

        {!searchTicker ? (
          <p className="text-sm text-gray-400">
            Enter a ticker symbol to view its conviction history.
          </p>
        ) : histLoading ? (
          <div className="flex justify-center py-8">
            <div className="h-6 w-6 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
          </div>
        ) : historyData?.snapshots.length === 0 ? (
          <p className="text-sm text-gray-400">
            No conviction data found for {searchTicker} in the last {days}{" "}
            days.
          </p>
        ) : (
          <div className="space-y-4">
            {historyData?.transitions && historyData.transitions.length > 0 && (
              <div>
                <h4 className="mb-2 text-xs font-semibold text-gray-500 uppercase">
                  State Transitions
                </h4>
                <div className="space-y-1">
                  {historyData.transitions.map((t) => (
                    <div
                      key={t.id}
                      className="flex items-center gap-2 text-sm"
                    >
                      <span className="text-gray-500">
                        {t.transition_date}
                      </span>
                      <StatusBadge
                        label={t.from_state.replace(/_/g, " ")}
                        variant={TREND_VARIANTS[t.from_state] ?? "neutral"}
                      />
                      <span className="text-gray-400">&rarr;</span>
                      <StatusBadge
                        label={t.to_state.replace(/_/g, " ")}
                        variant={TREND_VARIANTS[t.to_state] ?? "neutral"}
                      />
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <h4 className="mb-2 text-xs font-semibold text-gray-500 uppercase">
                Daily Snapshots
              </h4>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-gray-200 text-left text-gray-400">
                      <th className="pb-2 pr-3">Date</th>
                      <th className="pb-2 pr-3">State</th>
                      <th className="pb-2 pr-3">Close</th>
                      <th className="pb-2 pr-3">8-EMA</th>
                      <th className="pb-2 pr-3">21-EMA</th>
                      <th className="pb-2 pr-3">% to 8</th>
                      <th className="pb-2 pr-3">% to 21</th>
                      <th className="pb-2 pr-3">RSI</th>
                      <th className="pb-2 pr-3">IV Rank</th>
                      <th className="pb-2 pr-3">VRP</th>
                      <th className="pb-2 pr-3">50-EMA</th>
                      <th className="pb-2 pr-3">Days Above</th>
                      <th className="pb-2">Conviction</th>
                    </tr>
                  </thead>
                  <tbody>
                    {historyData?.snapshots.map((s) => (
                      <tr
                        key={`${s.ticker}-${s.as_of_date}`}
                        className="border-b border-gray-50 last:border-0"
                      >
                        <td className="py-1.5 pr-3 text-gray-500">
                          {s.as_of_date}
                        </td>
                        <td className="py-1.5 pr-3">
                          <StatusBadge
                            label={s.trend_state.replace(/_/g, " ")}
                            variant={
                              TREND_VARIANTS[s.trend_state] ?? "neutral"
                            }
                          />
                        </td>
                        <td className="py-1.5 pr-3">
                          ${s.last_close.toFixed(2)}
                        </td>
                        <td className="py-1.5 pr-3">
                          ${s.ema_8.toFixed(2)}
                        </td>
                        <td className="py-1.5 pr-3">
                          ${s.ema_21.toFixed(2)}
                        </td>
                        <td className="py-1.5 pr-3">
                          {s.price_to_8ema_pct.toFixed(2)}%
                        </td>
                        <td className="py-1.5 pr-3">
                          {s.price_to_21ema_pct.toFixed(2)}%
                        </td>
                        <td className="py-1.5 pr-3">
                          <span className={s.rsi_14 < 30 ? "text-red-600" : s.rsi_14 < 40 ? "text-amber-600" : s.rsi_14 > 70 ? "text-purple-600" : ""}>
                            {s.rsi_14.toFixed(0)}
                          </span>
                        </td>
                        <td className="py-1.5 pr-3">
                          {s.iv_rank != null ? (
                            <span className={s.iv_rank < 20 ? "text-emerald-600" : s.iv_rank > 80 ? "text-red-600" : ""}>
                              {s.iv_rank.toFixed(0)}
                            </span>
                          ) : (
                            <span className="text-gray-300">—</span>
                          )}
                        </td>
                        <td className="py-1.5 pr-3">
                          {s.vrp != null ? (
                            <span className={s.vrp > 0 ? "text-emerald-600" : s.vrp < 0 ? "text-red-600" : ""}>
                              {(s.vrp * 100).toFixed(1)}%
                            </span>
                          ) : (
                            <span className="text-gray-300">—</span>
                          )}
                        </td>
                        <td className="py-1.5 pr-3">
                          <span className={s.ema_50_slope > 0 ? "text-emerald-600" : "text-red-500"}>
                            {s.ema_50_slope > 0 ? "▲" : "▼"}
                          </span>
                        </td>
                        <td className="py-1.5 pr-3">
                          {s.days_above_both_emas}
                        </td>
                        <td className="py-1.5">
                          {(() => {
                            const conv = s.raw_conviction ?? s.conviction_level;
                            return (
                              <StatusBadge
                                label={conv}
                                variant={
                                  conv === "high"
                                    ? "success"
                                    : conv === "medium"
                                      ? "warning"
                                      : "neutral"
                                }
                              />
                            );
                          })()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
