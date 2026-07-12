import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_COLORS, type ChartTone } from "./theme";
import { ChartTooltip } from "./ChartTooltip";

export interface Series {
  key: string;
  name: string;
  tone?: ChartTone;
  yAxisId?: "left" | "right";
}

export interface RefLine {
  y: number;
  label: string;
  tone?: ChartTone;
}

interface LineChartCardProps {
  data: Record<string, unknown>[];
  xKey: string;
  series: Series[];
  height?: number;
  yDomain?: [number | "auto", number | "auto"];
  referenceLines?: RefLine[];
  valuePrefix?: string;
  valueSuffix?: string;
  rightAxis?: boolean;
  decimals?: number;
}

function formatValue(v: number, prefix = "", suffix = "", decimals = 1): string {
  return `${prefix}${v.toFixed(decimals)}${suffix}`;
}

export function LineChartCard({
  data,
  xKey,
  series,
  height = 220,
  yDomain,
  referenceLines,
  valuePrefix = "",
  valueSuffix = "",
  rightAxis = false,
  decimals = 1,
}: LineChartCardProps) {
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
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
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
          yAxisId="left"
          width={44}
          tick={{ fontSize: 11, fill: CHART_COLORS.axis }}
          tickLine={false}
          axisLine={false}
          domain={yDomain ?? ["auto", "auto"]}
          tickFormatter={tickFormatter}
        />
        {rightAxis && (
          <YAxis
            yAxisId="right"
            orientation="right"
            width={44}
            tick={{ fontSize: 11, fill: CHART_COLORS.axis }}
            tickLine={false}
            axisLine={false}
            domain={["auto", "auto"]}
          />
        )}
        <Tooltip
          content={<ChartTooltip formatValue={(v) => formatValue(v, valuePrefix, valueSuffix, decimals)} />}
        />
        {referenceLines?.map((ref, i) => {
          const color = CHART_COLORS[ref.tone ?? "gray"];
          return (
            <ReferenceLine
              key={i}
              yAxisId="left"
              y={ref.y}
              stroke={color}
              strokeDasharray="4 4"
              label={{
                value: ref.label,
                position: "insideTopRight",
                fill: color,
                fontSize: 10,
              }}
            />
          );
        })}
        {series.map((s) => (
          <Line
            key={s.key}
            type="monotone"
            dataKey={s.key}
            name={s.name}
            yAxisId={s.yAxisId ?? "left"}
            stroke={CHART_COLORS[s.tone ?? "blue"]}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
