import type { ReactNode } from "react";

export type CalloutTone = "info" | "warning" | "danger" | "success";

interface CalloutProps {
  tone: CalloutTone;
  title: string;
  children: ReactNode;
}

const TONE_CLASSES: Record<CalloutTone, string> = {
  info: "bg-blue-50 border-blue-200 text-blue-900",
  warning: "bg-amber-50 border-amber-200 text-amber-900",
  danger: "bg-red-50 border-red-200 text-red-900",
  success: "bg-emerald-50 border-emerald-200 text-emerald-900",
};

export function Callout({ tone, title, children }: CalloutProps) {
  return (
    <div className={`rounded-lg border-l-4 border rounded-l-none p-3 ${TONE_CLASSES[tone]}`}>
      <div className="text-sm font-semibold">{title}</div>
      <div className="text-xs mt-1 leading-relaxed">{children}</div>
    </div>
  );
}
