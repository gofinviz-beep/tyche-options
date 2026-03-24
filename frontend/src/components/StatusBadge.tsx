interface StatusBadgeProps {
  label: string;
  variant: "success" | "warning" | "danger" | "info" | "neutral";
}

const variantStyles: Record<StatusBadgeProps["variant"], string> = {
  success: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  warning: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  danger: "bg-red-500/20 text-red-400 border-red-500/30",
  info: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  neutral: "bg-gray-500/20 text-gray-400 border-gray-500/30",
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
