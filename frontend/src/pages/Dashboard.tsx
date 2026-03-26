import { Link } from "react-router-dom";
import { Card } from "@/components/Card";
import { PLValue } from "@/components/PLValue";
import { StatusBadge } from "@/components/StatusBadge";
import { useAccountSummary, useOpenOrders, useLatestScan, useOrderIntents } from "@/hooks/useApi";

export function Dashboard() {
  const { data: summary, isLoading } = useAccountSummary();
  const { data: orders } = useOpenOrders();
  const { data: scan } = useLatestScan();
  const { data: intents } = useOrderIntents("pending");

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-600 border-t-white" />
      </div>
    );
  }

  const balance = summary?.balance;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Dashboard</h1>
        <p className="text-sm text-gray-500">
          {new Date().toLocaleDateString("en-US", {
            weekday: "long",
            year: "numeric",
            month: "long",
            day: "numeric",
          })}
        </p>
      </div>

      {/* Account metrics */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <MetricCard
          label="Net Liquidation"
          value={`$${(balance?.net_liquidation_value ?? 0).toLocaleString()}`}
        />
        <MetricCard
          label="Buying Power"
          value={`$${(balance?.buying_power ?? 0).toLocaleString()}`}
        />
        <MetricCard
          label="Cash Available for CSP"
          value={`$${(summary?.cash_available_for_csp ?? 0).toLocaleString()}`}
        />
        <MetricCard
          label="Today's P&L"
          value={<PLValue value={balance?.close_pl ?? 0} />}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Positions */}
        <Card
          title="Positions"
          subtitle={`${summary?.position_count ?? 0} open`}
        >
          {summary?.positions.length ? (
            <div className="space-y-3">
              {summary.positions.map((pos) => (
                <div
                  key={pos.id}
                  className="flex items-center justify-between rounded-lg bg-gray-800/50 px-4 py-3"
                >
                  <div>
                    <span className="font-semibold text-white">{pos.symbol}</span>
                    <span className="ml-2 text-sm text-gray-500">
                      {pos.option_symbol ? `${pos.contracts} contracts` : `${pos.quantity} shares`}
                    </span>
                  </div>
                  <div className="text-right">
                    <PLValue value={pos.unrealized_pl} className="text-sm" />
                    <p className="text-xs text-gray-500">
                      <PLValue value={pos.unrealized_pl_pct} format="percent" />
                    </p>
                  </div>
                </div>
              ))}
              <div className="flex justify-between border-t border-gray-800 pt-3">
                <span className="text-sm text-gray-400">Total Unrealized</span>
                <PLValue value={summary.total_unrealized_pl} className="text-sm font-semibold" />
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-500">No open positions</p>
          )}
        </Card>

        {/* Open orders */}
        <Card
          title="Open Orders"
          subtitle={`${orders?.length ?? 0} pending`}
        >
          {orders?.length ? (
            <div className="space-y-3">
              {orders.map((order) => (
                <div
                  key={order.id}
                  className="flex items-center justify-between rounded-lg bg-gray-800/50 px-4 py-3"
                >
                  <div>
                    <span className="font-semibold text-white">{order.symbol}</span>
                    <span className="ml-2 text-sm text-gray-500">
                      {order.side.replace(/_/g, " ")} x{order.quantity}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-sm text-gray-300">
                      ${order.limit_price?.toFixed(2) ?? "MKT"}
                    </span>
                    <StatusBadge
                      label={order.status}
                      variant={
                        order.status === "filled"
                          ? "success"
                          : order.status === "pending"
                          ? "warning"
                          : "neutral"
                      }
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-500">No open orders</p>
          )}
        </Card>
      </div>

      {/* Pending intents */}
      {intents && intents.pending > 0 && (
        <Card
          title="Pending Trade Intents"
          subtitle={`${intents.pending} awaiting your review`}
        >
          <div className="space-y-3">
            {intents.intents.slice(0, 5).map((intent) => (
              <div
                key={intent.id}
                className="flex items-center justify-between rounded-lg bg-gray-800/50 px-4 py-3"
              >
                <div>
                  <span className="font-semibold text-white">{intent.symbol}</span>
                  <span className="ml-2 text-sm text-gray-500">
                    {intent.strategy.toUpperCase()} · {intent.quantity} contracts
                  </span>
                  {intent.strike && (
                    <span className="ml-2 text-sm text-gray-500">
                      @ ${intent.strike.toFixed(2)}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm text-emerald-400">
                    ${intent.estimated_premium.toLocaleString()}
                  </span>
                  <StatusBadge
                    label={intent.conviction_level}
                    variant={
                      intent.conviction_level === "high"
                        ? "success"
                        : intent.conviction_level === "medium"
                        ? "warning"
                        : "neutral"
                    }
                  />
                </div>
              </div>
            ))}
            <Link
              to="/intents"
              className="inline-block text-sm font-medium text-blue-400 hover:text-blue-300"
            >
              Review all intents &rarr;
            </Link>
          </div>
        </Card>
      )}

      {/* Latest scan summary */}
      {scan && (
        <Card
          title="Latest Scan"
          subtitle={`${scan.symbols_scanned} symbols · ${new Date(scan.scanned_at).toLocaleTimeString()}`}
        >
          <div className="flex gap-6 text-sm">
            <div>
              <span className="text-gray-500">CSP Candidates: </span>
              <span className="font-semibold text-white">{scan.csp_candidates.length}</span>
            </div>
            <div>
              <span className="text-gray-500">CC Candidates: </span>
              <span className="font-semibold text-white">{scan.cc_candidates.length}</span>
            </div>
            {scan.errors.length > 0 && (
              <div>
                <span className="text-red-400">Errors: {scan.errors.length}</span>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}

function MetricCard({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/80 p-4">
      <p className="text-xs font-medium text-gray-500">{label}</p>
      <p className="mt-1 text-xl font-bold text-white">{value}</p>
    </div>
  );
}
