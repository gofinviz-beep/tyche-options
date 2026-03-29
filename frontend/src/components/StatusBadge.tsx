interface StatusBadgeProps {
  label: string;
  variant: "success" | "warning" | "danger" | "info" | "neutral";
}

const variantStyles: Record<StatusBadgeProps["variant"], string> = {
  success: "bg-emerald-50 text-emerald-600 border-emerald-200",
  warning: "bg-amber-50 text-amber-600 border-amber-200",
  danger: "bg-red-50 text-red-600 border-red-200",
  info: "bg-blue-50 text-blue-600 border-blue-200",
  neutral: "bg-gray-100 text-gray-500 border-gray-200",
};

export function StatusBadge({ label, variant }: StatusBadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${variantStyles[variant]}`}
    >
      {label}
    </span>
  );
}
