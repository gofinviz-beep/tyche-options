interface PLValueProps {
  value: number;
  format?: "currency" | "percent";
  className?: string;
}

export function PLValue({ value, format = "currency", className = "" }: PLValueProps) {
  const isPositive = value >= 0;
  const color = value === 0 ? "text-gray-500" : isPositive ? "text-emerald-600" : "text-red-600";
  const prefix = isPositive && value !== 0 ? "+" : "";

  const formatted =
    format === "percent"
      ? `${prefix}${value.toFixed(2)}%`
      : `${prefix}$${Math.abs(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  return <span className={`font-mono ${color} ${className}`}>{formatted}</span>;
}
