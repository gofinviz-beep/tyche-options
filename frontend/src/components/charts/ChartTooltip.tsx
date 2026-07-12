import type { TooltipContentProps } from "recharts";

interface ChartTooltipProps
  extends Partial<TooltipContentProps<number | string, string>> {
  formatValue?: (v: number, key: string) => string;
}

export function ChartTooltip({ active, payload, label, formatValue }: ChartTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;

  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-sm px-3 py-2 text-xs">
      {label != null && <div className="font-bold text-gray-700 mb-1">{label}</div>}
      {payload.map((entry, i) => {
        const key = String(entry.dataKey ?? entry.name ?? i);
        const raw = entry.value;
        const numeric = typeof raw === "number" ? raw : Number(raw);
        const formatted =
          formatValue && !Number.isNaN(numeric)
            ? formatValue(numeric, key)
            : String(raw);
        return (
          <div key={key} className="flex items-center gap-1.5 py-0.5">
            <span
              className="inline-block w-2 h-2 rounded-full shrink-0"
              style={{ backgroundColor: entry.color }}
            />
            <span className="text-gray-500">{entry.name}:</span>
            <span className="font-medium text-gray-800">{formatted}</span>
          </div>
        );
      })}
    </div>
  );
}
