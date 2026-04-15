import { useMemo } from "react";
import {
  TrendingDown,
  ArrowUpRight,
  Shield,
  AlertTriangle,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Activity,
} from "lucide-react";
import { Card } from "@/components/Card";
import { DataTable, type DataTableColumn } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { useDeepDips } from "@/hooks/useApi";
import { formatMarketCap } from "@/lib/format";
import type { DeepDipAlert, MarketContext } from "@/types";

function riskBadgeVariant(
  level: string,
): "success" | "warning" | "danger" | "info" {
  switch (level) {
    case "low":
      return "success";
    case "medium":
      return "warning";
    case "high":
    case "extreme":
      return "danger";
    default:
      return "info";
  }
}

function catalystLabel(catalyst: string): string {
  const map: Record<string, string> = {
    market_fear: "Market Fear",
    sector_rotation: "Sector Rotation",
    earnings_reaction: "Earnings Reaction",
    news_driven: "News Driven",
    insider_selling: "Insider Selling",
    regulatory: "Regulatory",
    unknown: "Unknown",
  };
  return map[catalyst] ?? catalyst;
}

function buildColumns(): DataTableColumn<DeepDipAlert>[] {
  return [
    {
      key: "actionable",
      header: "Signal",
      accessor: (r) => (r.recovery_signal?.actionable ? 1 : 0),
      render: (r) => {
        const sig = r.recovery_signal;
        if (!sig) return <span className="text-xs text-gray-400">—</span>;
        if (sig.meets_all_thresholds) {
          return (
            <div className="flex items-center gap-1">
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              <span className="text-xs font-semibold text-emerald-700">BUY</span>
            </div>
          );
        }
        if (sig.actionable) {
          return (
            <div className="flex items-center gap-1">
              <Activity className="h-4 w-4 text-amber-500" />
              <span className="text-xs font-medium text-amber-700">Watch</span>
            </div>
          );
        }
        return (
          <div className="flex items-center gap-1">
            <XCircle className="h-3.5 w-3.5 text-gray-300" />
            <span className="text-xs text-gray-400">Skip</span>
          </div>
        );
      },
      sortable: true,
      filter: {
        type: "select",
        options: [
          { value: "1", label: "Actionable" },
          { value: "0", label: "All" },
        ],
      },
    },
    {
      key: "ticker",
      header: "Ticker",
      accessor: (r) => r.ticker,
      render: (r) => (
        <div>
          <span className="font-semibold text-gray-900">{r.ticker}</span>
          {r.name && (
            <span className="ml-1.5 text-xs text-gray-400">{r.name}</span>
          )}
        </div>
      ),
      sortable: true,
      width: "180px",
    },
    {
      key: "alert_type",
      header: "Dip Type",
      accessor: (r) => r.alert_type,
      render: (r) => (
        <StatusBadge
          label={r.alert_type === "oversold_50ema" ? "Below 50-EMA" : "Below 21-EMA"}
          variant={r.alert_type === "oversold_50ema" ? "danger" : "warning"}
        />
      ),
      sortable: true,
      filter: {
        type: "select",
        options: [
          { value: "oversold_50ema", label: "Below 50-EMA" },
          { value: "oversold_21ema", label: "Below 21-EMA" },
        ],
      },
    },
    {
      key: "dip_pct",
      header: "Dip %",
      accessor: (r) => r.dip_pct,
      render: (r) => (
        <span className="font-mono text-sm text-red-600">
          -{r.dip_pct.toFixed(1)}%
        </span>
      ),
      sortable: true,
      align: "right",
    },
    {
      key: "last_close",
      header: "Price",
      accessor: (r) => r.last_close,
      render: (r) => (
        <span className="font-mono text-sm">${r.last_close.toFixed(2)}</span>
      ),
      sortable: true,
      align: "right",
    },
    {
      key: "rsi_14",
      header: "RSI",
      accessor: (r) => r.rsi_14,
      render: (r) => {
        const inSweet = r.rsi_14 >= 30 && r.rsi_14 <= 50;
        const color = inSweet ? "text-emerald-600" : r.rsi_14 < 30 ? "text-amber-600" : "text-gray-500";
        return (
          <span className={`font-mono text-sm ${color}`}>
            {r.rsi_14.toFixed(0)}
            {inSweet && <span className="ml-0.5 text-xs">&#10003;</span>}
          </span>
        );
      },
      sortable: true,
      align: "right",
    },
    {
      key: "recovery_est",
      header: "R20d / R40d",
      accessor: (r) => (r.recovery_signal?.meets_all_thresholds ? 2 : r.recovery_signal?.actionable ? 1 : 0),
      render: (r) => {
        const sig = r.recovery_signal;
        if (!sig) return <span className="text-xs text-gray-400">—</span>;
        return (
          <div className="text-xs">
            <span className={sig.actionable ? "font-medium text-gray-800" : "text-gray-400"}>
              {sig.recovery_20d_est}
            </span>
            <span className="mx-0.5 text-gray-300">/</span>
            <span className={sig.actionable ? "font-medium text-gray-800" : "text-gray-400"}>
              {sig.recovery_40d_est}
            </span>
          </div>
        );
      },
      sortable: true,
    },
    {
      key: "market_cap",
      header: "Market Cap",
      accessor: (r) => r.market_cap ?? 0,
      render: (r) =>
        r.market_cap ? (
          <span className="text-sm text-gray-600">
            {formatMarketCap(r.market_cap)}
          </span>
        ) : (
          <span className="text-xs text-gray-400">—</span>
        ),
      sortable: true,
      align: "right",
    },
    {
      key: "risk_level",
      header: "Risk",
      accessor: (r) => r.dip_classification?.risk_level ?? "unknown",
      render: (r) => {
        const dc = r.dip_classification;
        if (!dc) return <span className="text-xs text-gray-400">N/A</span>;
        return (
          <StatusBadge
            label={dc.risk_level.toUpperCase()}
            variant={riskBadgeVariant(dc.risk_level)}
          />
        );
      },
      sortable: true,
      filter: {
        type: "select",
        options: [
          { value: "low", label: "Low" },
          { value: "medium", label: "Medium" },
        ],
      },
    },
    {
      key: "sector",
      header: "Sector",
      accessor: (r) => r.sector ?? "",
      render: (r) => (
        <span className="text-xs text-gray-500">{r.sector ?? "—"}</span>
      ),
      sortable: true,
    },
  ];
}

function MarketContextBanner({ ctx }: { ctx: MarketContext }) {
  return (
    <div
      className={`rounded-lg border p-4 ${
        ctx.is_broad_selloff
          ? "border-amber-200 bg-amber-50"
          : "border-gray-200 bg-gray-50"
      }`}
    >
      <div className="flex items-center gap-2 mb-2">
        <Activity className={`h-4 w-4 ${ctx.is_broad_selloff ? "text-amber-600" : "text-gray-500"}`} />
        <span className="text-sm font-semibold text-gray-800">
          {ctx.is_broad_selloff ? "Broad Selloff Detected" : "Normal Market Conditions"}
        </span>
        {ctx.is_broad_selloff && (
          <StatusBadge label="Recovery Window Open" variant="success" />
        )}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-gray-600">
        <span>
          <strong>{ctx.concurrent_dips}</strong> of {ctx.total_universe} stocks dipping (
          {(ctx.market_dip_breadth * 100).toFixed(1)}%)
        </span>
        {ctx.spy_return_5d != null && (
          <span>
            SPY 5d:{" "}
            <span className={ctx.spy_return_5d < 0 ? "text-red-600 font-medium" : "text-emerald-600"}>
              {ctx.spy_return_5d > 0 ? "+" : ""}
              {ctx.spy_return_5d.toFixed(1)}%
            </span>
          </span>
        )}
        {ctx.spy_drawdown_from_high != null && (
          <span>
            SPY drawdown:{" "}
            <span className={ctx.spy_drawdown_from_high < -5 ? "text-red-600 font-medium" : "text-gray-700"}>
              {ctx.spy_drawdown_from_high.toFixed(1)}%
            </span>{" "}
            from 50d high
          </span>
        )}
        {ctx.spy_rsi_14 != null && (
          <span>
            SPY RSI: <strong>{ctx.spy_rsi_14.toFixed(0)}</strong>
          </span>
        )}
      </div>
      {!ctx.is_broad_selloff && (
        <p className="mt-1.5 text-xs text-gray-400">
          Fewer than 100 stocks dipping — individual dips are more likely stock-specific than market-driven.
          Recovery rates are lower (~42% at 20d). Wait for a broader selloff for higher-probability entries.
        </p>
      )}
    </div>
  );
}

function DipDetail({ alert }: { alert: DeepDipAlert }) {
  const dc = alert.dip_classification;
  const sig = alert.recovery_signal;

  return (
    <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-3">
      {/* Recovery Signal (NEW — most important) */}
      <div
        className={`rounded-lg border p-4 ${
          sig?.meets_all_thresholds
            ? "border-emerald-200 bg-emerald-50"
            : sig?.actionable
              ? "border-amber-200 bg-amber-50"
              : "border-gray-100 bg-gray-50"
        }`}
      >
        <h4 className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase text-gray-500">
          <ArrowUpRight className="h-3.5 w-3.5" /> Recovery Assessment
        </h4>
        {sig ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              {sig.meets_all_thresholds ? (
                <StatusBadge label="ALL THRESHOLDS MET" variant="success" />
              ) : sig.actionable ? (
                <StatusBadge label="PARTIALLY MET" variant="warning" />
              ) : (
                <StatusBadge label="NOT ACTIONABLE" variant="danger" />
              )}
            </div>
            <div className="space-y-1.5">
              {sig.threshold_checks.map((check, i) => {
                const pass = check.startsWith("PASS");
                return (
                  <div key={i} className="flex items-start gap-1.5 text-xs">
                    {pass ? (
                      <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
                    ) : (
                      <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-red-400" />
                    )}
                    <span className={pass ? "text-gray-700" : "text-gray-400"}>
                      {check.slice(6)}
                    </span>
                  </div>
                );
              })}
            </div>
            {sig.actionable && (
              <div className="mt-2 space-y-1 rounded border border-blue-100 bg-blue-50 p-2 text-xs text-blue-700">
                <div>Recovery 20d: <strong>{sig.recovery_20d_est}</strong></div>
                <div>Recovery 40d: <strong>{sig.recovery_40d_est}</strong></div>
                {sig.peak_recovery_est && (
                  <div>Peak: {sig.peak_recovery_est}</div>
                )}
                {sig.suggested_cc_dte && (
                  <div className="mt-1 font-medium">CC: {sig.suggested_cc_dte}</div>
                )}
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-gray-400">No recovery assessment available.</p>
        )}
      </div>

      {/* Technical Context */}
      <div className="rounded-lg border border-gray-100 bg-gray-50 p-4">
        <h4 className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase text-gray-500">
          <TrendingDown className="h-3.5 w-3.5" /> Technical Context
        </h4>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-500">Price</span>
            <span className="font-mono">${alert.last_close.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">21-EMA</span>
            <span className="font-mono">${alert.ema_21.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">50-EMA</span>
            <span className="font-mono">${alert.ema_50.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">RSI(14)</span>
            <span className={`font-mono ${alert.rsi_14 >= 30 && alert.rsi_14 <= 50 ? "text-emerald-600 font-semibold" : ""}`}>
              {alert.rsi_14.toFixed(1)}
              {alert.rsi_14 >= 30 && alert.rsi_14 <= 50 && " (sweet spot)"}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">21-EMA Slope</span>
            <span className={`font-mono ${alert.ema_21_slope > -0.5 ? "text-emerald-600" : "text-red-500"}`}>
              {alert.ema_21_slope.toFixed(2)}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Prior Uptrend</span>
            <span className="font-mono">{alert.prior_streak}d</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Stop Loss</span>
            <span className="font-mono text-red-500">${alert.stop_loss_level.toFixed(2)}</span>
          </div>
        </div>
      </div>

      {/* Dip Classification */}
      <div className="rounded-lg border border-gray-100 bg-gray-50 p-4">
        <h4 className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase text-gray-500">
          <Shield className="h-3.5 w-3.5" /> Dip Classification
        </h4>
        {dc ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <StatusBadge
                label={catalystLabel(dc.catalyst)}
                variant={riskBadgeVariant(dc.risk_level)}
              />
              <StatusBadge
                label={`Risk: ${dc.risk_level.toUpperCase()}`}
                variant={riskBadgeVariant(dc.risk_level)}
              />
            </div>
            <div className="space-y-1">
              {dc.reasons.map((reason, i) => (
                <div key={i} className="flex items-start gap-1.5 text-xs text-gray-600">
                  <span className="mt-0.5 text-gray-400">&bull;</span>
                  <span>{reason}</span>
                </div>
              ))}
            </div>
            {dc.news_impact_score != null && (
              <div className="mt-2 text-xs text-gray-500">
                News Impact: {dc.news_impact_score.toFixed(2)} | Neg Articles:{" "}
                {dc.negative_news_count}
              </div>
            )}
            {dc.insider_cluster_sell && (
              <div className="mt-1 flex items-center gap-1 text-xs text-red-600">
                <AlertTriangle className="h-3 w-3" />
                Insider cluster selling detected
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-gray-400">
            No news/filing data — technical-only assessment.
          </p>
        )}
      </div>
    </div>
  );
}

export function DeepDipDashboard() {
  const { data, isLoading, error, refetch, isFetching } = useDeepDips();

  const columns = useMemo(() => buildColumns(), []);

  const alerts = data?.alerts ?? [];
  const totalAnalyzed = data?.total_analyzed ?? 0;
  const totalOversold = data?.total_oversold ?? 0;
  const totalActionable = data?.total_actionable ?? 0;
  const asOfDate = data?.as_of_date ?? "";
  const marketCtx = data?.market_context ?? null;

  const meetsAll = alerts.filter(
    (a) => a.recovery_signal?.meets_all_thresholds,
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">
            Deep Dip Recovery Candidates
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Backtest-validated: broad selloff + RSI 30-50 + intact trend = 55%+ recovery in 20d.
            {asOfDate && (
              <span className="ml-1 text-gray-400">
                As of {asOfDate}
              </span>
            )}
          </p>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Market Context Banner */}
      {!isLoading && !error && marketCtx && (
        <MarketContextBanner ctx={marketCtx} />
      )}

      {/* Summary Pills */}
      {!isLoading && !error && (
        <div className="flex flex-wrap gap-3">
          <div className="rounded-lg border border-gray-200 bg-white px-4 py-2.5 shadow-sm">
            <div className="text-xs text-gray-400">Scanned</div>
            <div className="text-lg font-bold text-gray-900">{totalAnalyzed}</div>
          </div>
          <div className="rounded-lg border border-gray-200 bg-white px-4 py-2.5 shadow-sm">
            <div className="text-xs text-gray-400">Oversold</div>
            <div className="text-lg font-bold text-blue-600">{totalOversold}</div>
          </div>
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 shadow-sm">
            <div className="text-xs text-emerald-500">Actionable</div>
            <div className="text-lg font-bold text-emerald-700">{totalActionable}</div>
          </div>
          {meetsAll.length > 0 && (
            <div className="rounded-lg border border-emerald-300 bg-emerald-100 px-4 py-2.5 shadow-sm">
              <div className="text-xs text-emerald-600">All Thresholds</div>
              <div className="text-lg font-bold text-emerald-800">{meetsAll.length}</div>
            </div>
          )}
          <div className="rounded-lg border border-gray-200 bg-white px-4 py-2.5 shadow-sm">
            <div className="text-xs text-gray-400">Alerts Shown</div>
            <div className="text-lg font-bold text-gray-700">{alerts.length}</div>
          </div>
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <Card>
          <div className="flex items-center justify-center py-12">
            <RefreshCw className="mr-2 h-5 w-5 animate-spin text-gray-400" />
            <span className="text-sm text-gray-500">
              Scanning universe for oversold opportunities...
            </span>
          </div>
        </Card>
      )}

      {/* Error */}
      {error && (
        <Card>
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            Failed to load deep dip data. {(error as Error).message}
          </div>
        </Card>
      )}

      {/* Empty state */}
      {!isLoading && !error && alerts.length === 0 && (
        <Card>
          <div className="py-12 text-center">
            <TrendingDown className="mx-auto h-10 w-10 text-gray-300" />
            <h3 className="mt-3 text-sm font-medium text-gray-700">
              No Oversold Candidates Found
            </h3>
            <p className="mt-1 text-xs text-gray-400">
              No stocks are currently dipping significantly below their EMAs
              with a prior uptrend. Check back during market pullbacks.
            </p>
          </div>
        </Card>
      )}

      {/* Main Table */}
      {!isLoading && !error && alerts.length > 0 && (
        <Card
          title={`Deep Dip Candidates (${alerts.length})`}
          subtitle="Sorted by actionability — green checkmark = all backtest thresholds met"
        >
          <DataTable
            data={alerts}
            columns={columns}
            searchField={(r) => r.ticker}
            rowKey={(r) => r.ticker}
            defaultSortKey="actionable"
            defaultSortDir="desc"
            defaultPageSize={15}
            expandedRow={(r) => <DipDetail alert={r} />}
          />
        </Card>
      )}
    </div>
  );
}
