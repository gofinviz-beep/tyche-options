import { Link } from "react-router-dom";
import { Card } from "@/components/Card";
import { PLValue } from "@/components/PLValue";
import { useAccountSummary } from "@/hooks/useApi";

export function Dashboard() {
  const { data: summary, isLoading } = useAccountSummary();

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
      </div>
    );
  }

  const balance = summary?.balance;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="mt-1 text-sm text-gray-500">
            Options wheel strategy command center — sell CSPs on high-conviction
            uptrends, manage positions, and track performance.
          </p>
        </div>
        <p className="text-sm text-gray-400">
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
                className="flex items-center justify-between rounded-lg bg-gray-50 px-4 py-3"
              >
                <div>
                  <span className="font-semibold text-gray-900">{pos.symbol}</span>
                  <span className="ml-2 text-sm text-gray-400">
                    {pos.option_symbol ? `${pos.contracts} contracts` : `${pos.quantity} shares`}
                  </span>
                </div>
                <div className="text-right">
                  <PLValue value={pos.unrealized_pl} className="text-sm" />
                  <p className="text-xs text-gray-400">
                    <PLValue value={pos.unrealized_pl_pct} format="percent" />
                  </p>
                </div>
              </div>
            ))}
            <div className="flex justify-between border-t border-gray-200 pt-3">
              <span className="text-sm text-gray-500">Total Unrealized</span>
              <PLValue value={summary.total_unrealized_pl} className="text-sm font-semibold" />
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-400">No open positions</p>
        )}
      </Card>

      {/* Quick links */}
      <Card title="Get Started">
        <p className="text-sm text-gray-500">
          Run a{" "}
          <Link to="/options/conviction" className="font-medium text-blue-600 hover:text-blue-700">
            conviction scan
          </Link>{" "}
          to find high-conviction tickers, then use the{" "}
          <Link to="/options/scanner" className="font-medium text-blue-600 hover:text-blue-700">
            scanner
          </Link>{" "}
          to find option contracts with live pricing. Track filled positions in the{" "}
          <Link to="/options/monitor" className="font-medium text-blue-600 hover:text-blue-700">
            monitor
          </Link>.
        </p>
      </Card>
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
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-medium text-gray-400">{label}</p>
      <p className="mt-1 text-xl font-bold text-gray-900">{value}</p>
    </div>
  );
}
