import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Search,
  TrendingUp,
  BarChart3,
  Activity,
  DollarSign,
  Building2,
  Target,
  ArrowUp,
  ArrowDown,
  Minus,
  LineChart as LineChartIcon,
} from "lucide-react";
import { useTickerDeepDive } from "@/hooks/useApi";
import type {
  TickerDeepDive,
  FundamentalsPeriod,
  VolumeBar,
} from "@/types";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { Callout, type CalloutTone } from "@/components/charts/Callout";
import { CHART_COLORS } from "@/components/charts/theme";

function formatLargeNumber(n: number | null | undefined): string {
  if (n == null) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function formatPct(n: number | null | undefined, decimals = 1): string {
  if (n == null) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(decimals)}%`;
}

/** Margins/growth rates from the backend are already percent-scale (e.g. 27.8 means 27.8%). */
function formatPercentScale(v: number | null | undefined, decimals = 1): string {
  if (v == null) return "—";
  return `${v.toFixed(decimals)}%`;
}

/** Chart data props are typed as generic records; typed API arrays are structurally compatible. */
function toChartRows<T extends object>(rows: T[]): Record<string, unknown>[] {
  return rows as unknown as Record<string, unknown>[];
}

function rsiColor(val: number): string {
  if (val >= 70) return "text-red-600";
  if (val >= 60) return "text-amber-600";
  if (val <= 30) return "text-emerald-600";
  if (val <= 40) return "text-blue-600";
  return "text-gray-700";
}

function rsiBg(val: number): string {
  if (val >= 70) return "bg-red-50 border-red-200";
  if (val >= 60) return "bg-amber-50 border-amber-200";
  if (val <= 30) return "bg-emerald-50 border-emerald-200";
  if (val <= 40) return "bg-blue-50 border-blue-200";
  return "bg-gray-50 border-gray-200";
}

function rsiLabel(val: number): string {
  if (val >= 70) return "Overbought";
  if (val >= 60) return "Extended";
  if (val <= 30) return "Oversold";
  if (val <= 40) return "Stabilizing";
  return "Neutral";
}

function slopeIcon(slope: number) {
  if (slope > 0.3) return <ArrowUp className="w-3 h-3 text-emerald-600" />;
  if (slope < -0.3) return <ArrowDown className="w-3 h-3 text-red-600" />;
  return <Minus className="w-3 h-3 text-gray-400" />;
}

function RSICard({ label, value }: { label: string; value: number }) {
  return (
    <div className={`rounded-lg border p-3 text-center ${rsiBg(value)}`}>
      <div className="text-xs text-gray-500 uppercase tracking-wide mb-1">{label}</div>
      <div className={`text-2xl font-bold ${rsiColor(value)}`}>{value.toFixed(1)}</div>
      <div className="text-xs text-gray-500 mt-0.5">{rsiLabel(value)}</div>
    </div>
  );
}

function MetricRow({ label, value, suffix = "" }: { label: string; value: React.ReactNode; suffix?: string }) {
  if (value == null || value === "—") return null;
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-gray-50 last:border-0">
      <span className="text-sm text-gray-500">{label}</span>
      <span className="text-sm font-medium text-gray-800">{value}{suffix}</span>
    </div>
  );
}

function FundamentalsTable({ periods }: { periods: FundamentalsPeriod[] }) {
  if (!periods.length) return <div className="text-sm text-gray-400 italic">No fundamentals data available</div>;
  const fields: { key: keyof FundamentalsPeriod; label: string; format: (v: number) => string }[] = [
    { key: "revenue", label: "Revenue", format: formatLargeNumber },
    { key: "gross_margin", label: "Gross Margin", format: (v) => formatPercentScale(v) },
    { key: "operating_income", label: "Op Income", format: formatLargeNumber },
    { key: "operating_margin", label: "Op Margin", format: (v) => formatPercentScale(v) },
    { key: "net_income", label: "Net Income", format: formatLargeNumber },
    { key: "eps_diluted", label: "EPS", format: (v) => `$${v.toFixed(2)}` },
    { key: "cash", label: "Cash", format: formatLargeNumber },
    { key: "operating_cash_flow", label: "Op Cash Flow", format: formatLargeNumber },
    { key: "total_debt", label: "Total Debt", format: formatLargeNumber },
  ];
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200">
            <th className="text-left py-1.5 pr-4 font-medium text-gray-500">Metric</th>
            {periods.map((p) => (
              <th key={p.period} className="text-right py-1.5 px-2 font-medium text-gray-500 whitespace-nowrap">
                {p.period}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {fields.map(({ key, label, format }) => {
            const hasData = periods.some((p) => p[key] != null);
            if (!hasData) return null;
            return (
              <tr key={key} className="border-b border-gray-50">
                <td className="py-1.5 pr-4 text-gray-600">{label}</td>
                {periods.map((p) => (
                  <td key={p.period} className="text-right py-1.5 px-2 font-mono text-gray-800">
                    {p[key] != null ? format(p[key] as number) : "—"}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Section({ title, icon: Icon, children }: { title: string; icon: typeof TrendingUp; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
      <div className="flex items-center gap-2 px-5 py-3 border-b border-gray-100">
        <Icon className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-700">{title}</h3>
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

// --- Volume surge helpers ---

function median(nums: number[]): number {
  if (nums.length === 0) return 0;
  const sorted = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

interface VolumePeak {
  bar: VolumeBar;
  ratio: number;
  medianVol: number;
}

function findVolumePeak(bars: VolumeBar[]): VolumePeak | null {
  if (bars.length === 0) return null;
  const medianVol = median(bars.map((b) => b.volume));
  if (medianVol <= 0) return null;
  let best = bars[0];
  for (const b of bars) {
    if (b.volume > best.volume) best = b;
  }
  return { bar: best, ratio: best.volume / medianVol, medianVol };
}

// --- Data-driven callouts ---

interface CalloutItem {
  tone: CalloutTone;
  title: string;
  body: string;
}

function buildCallouts(data: TickerDeepDive): CalloutItem[] {
  const callouts: CalloutItem[] = [];
  const { rsi, estimates, last_close, volume_bars } = data;

  if (rsi.quarterly >= 60 && rsi.daily >= 70) {
    callouts.push({
      tone: "warning",
      title: "Structurally strong but short-term overbought",
      body: "Quarterly RSI confirms the longer-term trend, but daily RSI is hot — wait for a pullback to 40–50 before entering.",
    });
  } else if (rsi.quarterly >= 60 && rsi.daily <= 50) {
    callouts.push({
      tone: "success",
      title: "Trend intact, momentum cooled",
      body: "Quarterly RSI confirms the structural uptrend while daily RSI has cooled — this is the pullback entry window.",
    });
  }

  if (estimates.pt_high != null && last_close > estimates.pt_high) {
    callouts.push({
      tone: "danger",
      title: "Trading above all analyst targets",
      body: `Last close of $${last_close.toFixed(2)} exceeds the highest analyst price target of $${estimates.pt_high.toFixed(2)}.`,
    });
  } else if (estimates.pt_mean != null && last_close > 0) {
    const upside = (estimates.pt_mean / last_close - 1) * 100;
    if (upside > 15) {
      callouts.push({
        tone: "success",
        title: `${upside.toFixed(0)}% upside to mean target`,
        body: `Mean analyst price target of $${estimates.pt_mean.toFixed(2)} implies ${upside.toFixed(0)}% upside from the last close.`,
      });
    }
  }

  const peak = findVolumePeak(volume_bars);
  if (peak && peak.ratio >= 3) {
    callouts.push({
      tone: "info",
      title: "Recent volume surge",
      body: `Volume spiked to ${peak.ratio.toFixed(1)}× the median on ${peak.bar.date} — worth checking for a catalyst.`,
    });
  }

  return callouts;
}

function DeepDiveContent({ data }: { data: TickerDeepDive }) {
  const callouts = buildCallouts(data);
  const volumePeak = findVolumePeak(data.volume_bars);

  const revenueData = data.fundamentals.map((f) => ({
    period: f.period,
    value: f.revenue != null ? f.revenue / 1e6 : null,
  }));
  const cashData = data.fundamentals.map((f) => ({
    period: f.period,
    value: f.cash != null ? f.cash / 1e6 : null,
  }));
  const netIncomeData = data.fundamentals.map((f) => ({
    period: f.period,
    value: f.net_income != null ? f.net_income / 1e6 : null,
  }));
  const grossMarginData = data.fundamentals.map((f) => ({
    period: f.period,
    value: f.gross_margin,
  }));

  const emaReferenceLines = [
    ...(data.ema_stack.ema_8 > 0 ? [{ y: data.ema_stack.ema_8, label: "EMA-8", tone: "blue" as const }] : []),
    ...(data.ema_stack.ema_21 > 0 ? [{ y: data.ema_stack.ema_21, label: "EMA-21", tone: "amber" as const }] : []),
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h2 className="text-2xl font-bold text-gray-900">{data.ticker}</h2>
              {data.sector && (
                <span className="text-xs px-2 py-0.5 bg-blue-50 text-blue-600 rounded-full">{data.sector}</span>
              )}
            </div>
            {data.name && <div className="text-sm text-gray-500 mt-0.5">{data.name}</div>}
          </div>
          <div className="flex items-center gap-6">
            <div className="text-right">
              <div className="text-2xl font-bold text-gray-900">${data.last_close.toFixed(2)}</div>
              <div className="text-xs text-gray-400">As of {data.as_of_date}</div>
            </div>
            <div className="text-right">
              <div className="text-sm text-gray-500">52W Range</div>
              <div className="text-sm font-medium">${data.low_52w.toFixed(2)} — ${data.high_52w.toFixed(2)}</div>
              <div className={`text-xs ${data.pct_off_52w_high > 20 ? "text-red-500" : data.pct_off_52w_high > 10 ? "text-amber-500" : "text-emerald-500"}`}>
                {data.pct_off_52w_high.toFixed(1)}% off high
              </div>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-4 mt-3 pt-3 border-t border-gray-100 text-sm text-gray-600">
          <span>Mkt Cap: <strong>{formatLargeNumber(data.market_cap)}</strong></span>
          <span>Inst Own: <strong>{data.institutional_pct != null ? `${data.institutional_pct.toFixed(0)}%` : "—"}</strong></span>
          {Object.entries(data.returns).map(([label, val]) => (
            <span key={label}>
              {label}: <strong className={val >= 0 ? "text-emerald-600" : "text-red-600"}>{formatPct(val)}</strong>
            </span>
          ))}
        </div>
      </div>

      {/* Data-driven callouts */}
      {callouts.length > 0 && (
        <div className="space-y-2">
          {callouts.map((c, i) => (
            <Callout key={i} tone={c.tone} title={c.title}>
              {c.body}
            </Callout>
          ))}
        </div>
      )}

      {/* Multi-Timeframe RSI */}
      <Section title="Multi-Timeframe RSI" icon={Activity}>
        <div className="grid grid-cols-4 gap-3 mb-4">
          <RSICard label="Daily" value={data.rsi.daily} />
          <RSICard label="Weekly" value={data.rsi.weekly} />
          <RSICard label="Monthly" value={data.rsi.monthly} />
          <RSICard label="Quarterly" value={data.rsi.quarterly} />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <div className="text-xs font-medium text-gray-500 mb-1">Weekly RSI History</div>
            <LineChartCard
              data={toChartRows(data.rsi.weekly_history)}
              xKey="date"
              series={[{ key: "value", name: "Weekly RSI", tone: "blue" }]}
              yDomain={[30, 90]}
              referenceLines={[
                { y: 70, label: "Overbought", tone: "red" },
                { y: 50, label: "Neutral", tone: "gray" },
                { y: 30, label: "Oversold", tone: "emerald" },
              ]}
              height={200}
            />
          </div>
          <div>
            <div className="text-xs font-medium text-gray-500 mb-1">Monthly RSI History</div>
            <LineChartCard
              data={toChartRows(data.rsi.monthly_history)}
              xKey="date"
              series={[{ key: "value", name: "Monthly RSI", tone: "blue" }]}
              yDomain={[30, 90]}
              referenceLines={[
                { y: 70, label: "Overbought", tone: "red" },
                { y: 50, label: "Neutral", tone: "gray" },
                { y: 30, label: "Oversold", tone: "emerald" },
              ]}
              height={200}
            />
          </div>
        </div>
        <div className="mt-4">
          <div className="text-xs font-medium text-gray-500 mb-1">Quarterly RSI vs Price</div>
          <LineChartCard
            data={toChartRows(data.rsi.quarterly_history)}
            xKey="date"
            series={[
              { key: "value", name: "Quarterly RSI", tone: "violet", yAxisId: "left" },
              { key: "close", name: "Close ($)", tone: "gray", yAxisId: "right" },
            ]}
            rightAxis
            referenceLines={[
              { y: 60, label: "Breakout", tone: "emerald" },
              { y: 30, label: "Deep Oversold", tone: "red" },
            ]}
            height={240}
          />
        </div>
        <div className="mt-3 text-xs text-gray-400 bg-gray-50 rounded-lg p-3">
          <strong>Reading Guide:</strong> Daily RSI for entry timing. Weekly confirms trend momentum.
          Monthly identifies sector rotation. Quarterly RSI 60+ can indicate a structural breakout — wait for daily RSI pullback to 40-50 before entering.
        </div>
      </Section>

      {/* Price History & Volume */}
      {data.price_history.length > 0 && (
        <Section title="Price History & Volume" icon={LineChartIcon}>
          <div className="mb-4">
            <LineChartCard
              data={toChartRows(data.price_history)}
              xKey="date"
              series={[{ key: "close", name: "Weekly Close", tone: "blue" }]}
              valuePrefix="$"
              height={260}
              referenceLines={emaReferenceLines}
            />
            <div className="text-xs text-gray-400 mt-1">Weekly closes (up to 2 years).</div>
          </div>
          {data.volume_bars.length > 0 && (
            <div>
              <BarChartCard
                data={toChartRows(data.volume_bars)}
                xKey="date"
                barKey="volume"
                name="Volume (M)"
                valueSuffix="M"
                height={200}
                colorFn={(row) => {
                  const vol = Number(row.volume);
                  const isSurge = volumePeak != null && vol >= 3 * volumePeak.medianVol;
                  return isSurge ? CHART_COLORS.amber : CHART_COLORS.blue;
                }}
              />
              <div className="text-xs text-gray-400 mt-1">
                {volumePeak
                  ? `Peak: ${volumePeak.bar.volume.toFixed(1)}M on ${volumePeak.bar.date} — ${volumePeak.ratio.toFixed(1)}× median.`
                  : "Last 60 trading days."}
              </div>
            </div>
          )}
        </Section>
      )}

      {/* EMA Stack */}
      <Section title="EMA Stack" icon={TrendingUp}>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          {([
            { label: "8-EMA", value: data.ema_stack.ema_8, pct: data.ema_stack.pct_vs_ema_8, slope: data.ema_stack.slope_ema_8 },
            { label: "21-EMA", value: data.ema_stack.ema_21, pct: data.ema_stack.pct_vs_ema_21, slope: data.ema_stack.slope_ema_21 },
            { label: "50-EMA", value: data.ema_stack.ema_50, pct: data.ema_stack.pct_vs_ema_50, slope: data.ema_stack.slope_ema_50 },
            { label: "200-SMA", value: data.ema_stack.sma_200, pct: data.ema_stack.pct_vs_sma_200, slope: 0 },
          ] as const).map(({ label, value, pct, slope }) => (
            <div key={label} className="rounded-lg border border-gray-200 p-3">
              <div className="text-xs text-gray-500 mb-1 flex items-center gap-1">
                {label} {slope !== 0 && slopeIcon(slope)}
              </div>
              <div className="text-lg font-bold text-gray-800">${value.toFixed(2)}</div>
              <div className={`text-xs ${pct >= 0 ? "text-emerald-500" : "text-red-500"}`}>
                {formatPct(pct)}
              </div>
            </div>
          ))}
        </div>
        <div className="grid grid-cols-3 gap-4 text-sm">
          <MetricRow label="Stack Score" value={`${data.ema_stack.stack_score}/3`} />
          <MetricRow label="Days Above 8-EMA" value={data.ema_stack.days_above_ema_8} />
          <MetricRow label="Days Above 21-EMA" value={data.ema_stack.days_above_ema_21} />
        </div>
      </Section>

      {/* Technical Indicators */}
      <div className="grid grid-cols-2 gap-6">
        <Section title="MACD" icon={BarChart3}>
          <div className="space-y-2">
            <MetricRow label="MACD Line" value={data.macd.macd_line.toFixed(4)} />
            <MetricRow label="Signal Line" value={data.macd.signal_line.toFixed(4)} />
            <MetricRow
              label="Histogram"
              value={
                <span className={data.macd.histogram >= 0 ? "text-emerald-600" : "text-red-600"}>
                  {data.macd.histogram >= 0 ? "+" : ""}{data.macd.histogram.toFixed(4)}
                </span>
              }
            />
            <div className="text-xs text-gray-400 mt-2">
              {data.macd.histogram > 0 && data.macd.macd_line > data.macd.signal_line
                ? "Bullish momentum — MACD above signal"
                : data.macd.histogram < 0
                  ? "Bearish momentum — MACD below signal"
                  : "Neutral — MACD at signal line"}
            </div>
          </div>
        </Section>

        <Section title="Bollinger Bands" icon={Target}>
          <div className="space-y-2">
            <MetricRow label="Upper Band" value={`$${data.bollinger.upper.toFixed(2)}`} />
            <MetricRow label="Middle (SMA 20)" value={`$${data.bollinger.middle.toFixed(2)}`} />
            <MetricRow label="Lower Band" value={`$${data.bollinger.lower.toFixed(2)}`} />
            <MetricRow label="Band Width" value={`${data.bollinger.width_pct.toFixed(1)}%`} />
            <MetricRow label="%B" value={`${data.bollinger.pct_b.toFixed(1)}%`} />
            <div className="text-xs text-gray-400 mt-2">
              {data.bollinger.pct_b > 80
                ? "Near upper band — potential pullback zone"
                : data.bollinger.pct_b < 20
                  ? "Near lower band — potential support zone"
                  : "Within normal range"}
            </div>
          </div>
        </Section>
      </div>

      {/* Fundamentals */}
      <Section title="Quarterly Fundamentals" icon={DollarSign}>
        {data.fundamentals.length > 0 ? (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-5">
              <div>
                <div className="text-xs font-medium text-gray-500 mb-1">Revenue</div>
                <BarChartCard
                  data={revenueData}
                  xKey="period"
                  barKey="value"
                  name="Revenue"
                  tone="blue"
                  valuePrefix="$"
                  valueSuffix="M"
                />
              </div>
              <div>
                <div className="text-xs font-medium text-gray-500 mb-1">Cash</div>
                <BarChartCard
                  data={cashData}
                  xKey="period"
                  barKey="value"
                  name="Cash"
                  tone="emerald"
                  valuePrefix="$"
                  valueSuffix="M"
                />
              </div>
              <div>
                <div className="text-xs font-medium text-gray-500 mb-1">Net Income</div>
                <LineChartCard
                  data={netIncomeData}
                  xKey="period"
                  series={[{ key: "value", name: "Net Income", tone: "violet" }]}
                  valuePrefix="$"
                  valueSuffix="M"
                  referenceLines={[{ y: 0, label: "", tone: "gray" }]}
                />
              </div>
              <div>
                <div className="text-xs font-medium text-gray-500 mb-1">Gross Margin</div>
                <LineChartCard
                  data={grossMarginData}
                  xKey="period"
                  series={[{ key: "value", name: "Gross Margin", tone: "emerald" }]}
                  valueSuffix="%"
                />
              </div>
            </div>
            <FundamentalsTable periods={data.fundamentals} />
          </>
        ) : (
          <div className="text-sm text-gray-400 italic">No fundamentals data available</div>
        )}
      </Section>

      {/* Estimates */}
      <Section title="Analyst Estimates" icon={Building2}>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          {data.estimates.pt_mean != null && (
            <div className="rounded-lg border border-gray-200 p-3 text-center">
              <div className="text-xs text-gray-500 mb-1">PT Mean</div>
              <div className="text-lg font-bold text-gray-800">${data.estimates.pt_mean.toFixed(2)}</div>
              {data.last_close > 0 && (
                <div className={`text-xs ${data.estimates.pt_mean > data.last_close ? "text-emerald-500" : "text-red-500"}`}>
                  {formatPct((data.estimates.pt_mean / data.last_close - 1) * 100)} upside
                </div>
              )}
            </div>
          )}
          {data.estimates.pt_high != null && (
            <div className="rounded-lg border border-gray-200 p-3 text-center">
              <div className="text-xs text-gray-500 mb-1">PT High</div>
              <div className="text-lg font-bold text-emerald-600">${data.estimates.pt_high.toFixed(2)}</div>
            </div>
          )}
          {data.estimates.pt_low != null && (
            <div className="rounded-lg border border-gray-200 p-3 text-center">
              <div className="text-xs text-gray-500 mb-1">PT Low</div>
              <div className="text-lg font-bold text-red-600">${data.estimates.pt_low.toFixed(2)}</div>
            </div>
          )}
          {data.estimates.analyst_count != null && (
            <div className="rounded-lg border border-gray-200 p-3 text-center">
              <div className="text-xs text-gray-500 mb-1">Analysts</div>
              <div className="text-lg font-bold text-gray-800">{data.estimates.analyst_count}</div>
            </div>
          )}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
          <MetricRow label="Rev Growth Q/Q YoY" value={formatPercentScale(data.estimates.rev_growth_q_yoy)} />
          <MetricRow label="Rev Growth TTM YoY" value={formatPercentScale(data.estimates.rev_growth_ttm_yoy)} />
          <MetricRow label="Gross Margin TTM" value={formatPercentScale(data.estimates.gross_margin_ttm)} />
          <MetricRow label="Op Margin TTM" value={formatPercentScale(data.estimates.op_margin_ttm)} />
          <MetricRow label="Current Ratio" value={data.estimates.current_ratio?.toFixed(2)} />
          <MetricRow label="D/E Ratio" value={data.estimates.debt_to_equity?.toFixed(2)} />
        </div>
        {(data.estimates.forward_eps.length > 0 || data.estimates.forward_rev.length > 0) && (
          <div className="grid grid-cols-2 gap-4 mt-4">
            {data.estimates.forward_eps.length > 0 && (
              <div>
                <div className="text-xs font-medium text-gray-500 mb-1">Forward EPS Estimates</div>
                {data.estimates.forward_eps.map((e) => (
                  <div key={e.period} className="flex justify-between text-sm py-0.5">
                    <span className="text-gray-500">{e.period}</span>
                    <span className="font-mono">${e.value.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            )}
            {data.estimates.forward_rev.length > 0 && (
              <div>
                <div className="text-xs font-medium text-gray-500 mb-1">Forward Revenue Estimates</div>
                {data.estimates.forward_rev.map((e) => (
                  <div key={e.period} className="flex justify-between text-sm py-0.5">
                    <span className="text-gray-500">{e.period}</span>
                    <span className="font-mono">{formatLargeNumber(e.value)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Section>

      {/* Catalysts */}
      {data.catalysts.length > 0 && (
        <Section title="Recent Catalysts" icon={Activity}>
          <div className="space-y-1.5">
            {data.catalysts.map((c, i) => (
              <div key={i} className="flex items-center gap-3 py-1.5 border-b border-gray-50 last:border-0">
                <span className="text-xs text-gray-400 w-20 shrink-0">{c.date}</span>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${
                    c.impact > 0
                      ? "bg-emerald-50 text-emerald-600"
                      : c.impact < 0
                        ? "bg-red-50 text-red-600"
                        : "bg-gray-50 text-gray-600"
                  }`}
                >
                  {c.impact > 0 ? "+" : ""}{c.impact.toFixed(2)}
                </span>
                <span className="text-sm text-gray-700">{c.tag.replace(/_/g, " ")}</span>
                <span className="text-xs text-gray-400 ml-auto">{c.source}</span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

export function StockDeepDive() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tickerParam = searchParams.get("ticker");
  const [input, setInput] = useState(tickerParam ?? "");
  const [activeTicker, setActiveTicker] = useState<string | null>(tickerParam);

  const { data, isLoading, error } = useTickerDeepDive(activeTicker);

  const handleSearch = () => {
    const t = input.trim().toUpperCase();
    if (t) {
      setActiveTicker(t);
      setSearchParams({ ticker: t });
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-bold text-gray-900">Stock Deep Dive</h1>
          <p className="text-sm text-gray-500">Multi-timeframe technical + fundamental analysis</p>
        </div>
      </div>

      {/* Search bar */}
      <div className="flex gap-2 mb-6">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="Enter ticker (e.g. BFLY)"
            className="w-full pl-9 pr-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
          />
        </div>
        <button
          onClick={handleSearch}
          disabled={!input.trim()}
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          Analyze
        </button>
      </div>

      {/* States */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
          <span className="ml-3 text-sm text-gray-500">Computing deep dive for {activeTicker}...</span>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
          {(error as Error).message || `Failed to load data for ${activeTicker}`}
        </div>
      )}

      {!activeTicker && !isLoading && (
        <div className="bg-gray-50 border border-gray-200 rounded-xl p-8 text-center text-gray-400">
          <Activity className="w-10 h-10 mx-auto mb-3 opacity-50" />
          <div className="text-sm">Enter a ticker to see multi-timeframe RSI, EMA stack, MACD, Bollinger Bands, fundamentals, estimates, and catalysts.</div>
        </div>
      )}

      {data && <DeepDiveContent data={data} />}
    </div>
  );
}
