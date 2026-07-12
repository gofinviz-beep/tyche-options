import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_COLORS, type ChartTone } from "./theme";
import { ChartTooltip } from "./ChartTooltip";

interface BarChartCardProps {
  data: Record<string, unknown>[];
  xKey: string;
  barKey: string;
  name: string;
  height?: number;
  tone?: ChartTone;
  colorFn?: (row: Record<string, unknown>, i: number) => string;
  valuePrefix?: string;
  valueSuffix?: string;
  decimals?: number;
  referenceZero?: boolean;
}

function formatValue(v: number, prefix = "", suffix = "", decimals = 1): string {
  return `${prefix}${v.toFixed(decimals)}${suffix}`;
}

export function BarChartCard({
  data,
  xKey,
  barKey,
  name,
  height = 200,
  tone = "blue",
  colorFn,
  valuePrefix = "",
  valueSuffix = "",
  decimals = 1,
  referenceZero = false,
}: BarChartCardProps) {
  if (!data || data.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-xs text-gray-400 italic border border-dashed border-gray-200 rounded-lg"
        style={{ height }}
      >
        No data available
      </div>
    );
  }

  const tickFormatter = (v: number) => formatValue(v, valuePrefix, valueSuffix, decimals);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke={CHART_COLORS.grid} />
        <XAxis
          dataKey={xKey}
          tick={{ fontSize: 11, fill: CHART_COLORS.axis }}
          tickLine={false}
          axisLine={false}
          interval={data.length > 14 ? Math.floor(data.length / 6) : 0}
          minTickGap={16}
        />
        <YAxis
          width={44}
          tick={{ fontSize: 11, fill: CHART_COLORS.axis }}
          tickLine={false}
          axisLine={false}
          tickFormatter={tickFormatter}
        />
        <Tooltip
          content={<ChartTooltip formatValue={(v) => formatValue(v, valuePrefix, valueSuffix, decimals)} />}
        />
        {referenceZero && <ReferenceLine y={0} stroke={CHART_COLORS.gray} />}
        <Bar dataKey={barKey} name={name} radius={[3, 3, 0, 0]} fill={CHART_COLORS[tone]}>
          {colorFn &&
            data.map((row, i) => <Cell key={i} fill={colorFn(row, i)} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
