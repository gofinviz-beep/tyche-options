import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  subtitle?: string;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}

export function Card({ title, subtitle, children, className = "", action }: CardProps) {
  return (
    <div
      className={`rounded-xl border border-gray-800 bg-gray-900/80 backdrop-blur-sm ${className}`}
    >
      {(title || action) && (
        <div className="flex items-center justify-between border-b border-gray-800 px-5 py-4">
          <div>
            {title && (
              <h3 className="text-sm font-semibold text-gray-200">{title}</h3>
            )}
            {subtitle && (
              <p className="mt-0.5 text-xs text-gray-500">{subtitle}</p>
            )}
          </div>
          {action}
        </div>
      )}
      <div className="p-5">{children}</div>
    </div>
  );
}
