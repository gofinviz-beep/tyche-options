import { AlertTriangle, FileWarning } from "lucide-react";
import type { FilingSignal, NewsSignal } from "@/types";

interface NewsRiskBadgeProps {
  signal?: NewsSignal;
  filingSignal?: FilingSignal;
}

export function NewsRiskBadge({ signal, filingSignal }: NewsRiskBadgeProps) {
  const newsRisk = signal?.has_risk && signal.negative_count_24h > 0;
  const filingRisk = filingSignal?.has_risk;

  if (!newsRisk && !filingRisk) return null;

  return (
    <span className="inline-flex items-center gap-0.5">
      {newsRisk && signal && (
        <span title={buildNewsTooltip(signal)}>
          <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
        </span>
      )}
      {filingRisk && filingSignal && (
        <span title={buildFilingTooltip(filingSignal)}>
          <FileWarning className="h-3.5 w-3.5 text-red-500" />
        </span>
      )}
    </span>
  );
}

function buildNewsTooltip(signal: NewsSignal): string {
  return [
    `${signal.negative_count_24h} negative article${signal.negative_count_24h !== 1 ? "s" : ""} in 24h (${signal.total_count_24h} total)`,
    signal.dominant_event_type
      ? `Type: ${signal.dominant_event_type.replace(/_/g, " ")}`
      : null,
    `Weighted sentiment: ${signal.news_impact_score.toFixed(2)} (48h decay)`,
  ]
    .filter(Boolean)
    .join(". ");
}

function buildFilingTooltip(signal: FilingSignal): string {
  const parts: string[] = [];

  if (signal.insider_cluster_sell) {
    parts.push("Insider cluster sell detected (3+ insiders in 7 days)");
  }

  if (signal.insider_sell_count_30d > 0) {
    parts.push(
      `${signal.insider_sell_count_30d} insider sell${signal.insider_sell_count_30d !== 1 ? "s" : ""} in 30d`,
    );
  }

  if (signal.insider_buy_count_30d > 0) {
    parts.push(
      `${signal.insider_buy_count_30d} insider buy${signal.insider_buy_count_30d !== 1 ? "s" : ""} in 30d`,
    );
  }

  if (
    signal.last_8k_impact !== null &&
    signal.last_8k_impact !== undefined &&
    signal.last_8k_impact < -0.5
  ) {
    parts.push(
      `8-K filed: ${signal.last_8k_sentiment ?? "negative"} (impact ${signal.last_8k_impact.toFixed(2)})`,
    );
  }

  if (signal.eightk_count_30d > 0) {
    parts.push(`${signal.eightk_count_30d} 8-K filing${signal.eightk_count_30d !== 1 ? "s" : ""} in 30d`);
  }

  return parts.join(". ") || "Filing risk detected";
}
