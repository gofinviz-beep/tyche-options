import { Card } from "@/components/Card";
import { PLValue } from "@/components/PLValue";
import { StatusBadge } from "@/components/StatusBadge";
import { useOrderMonitor } from "@/hooks/useApi";

export function Monitor() {
  const { data, isLoading, refetch, isFetching } = useOrderMonitor();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Order Monitor</h1>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="rounded-lg bg-gray-800 px-4 py-2 text-sm font-medium text-gray-300 transition-colors hover:bg-gray-700 disabled:opacity-50"
        >
          {isFetching ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {isLoading ? (
        <div className="flex h-32 items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-600 border-t-white" />
        </div>
      ) : data ? (
        <>
          <div className="flex gap-4 text-sm">
            <div className="rounded-lg border border-gray-800 bg-gray-900/80 px-4 py-3">
              <span className="text-gray-500">Orders Checked: </span>
              <span className="font-semibold text-white">
                {data.orders_checked}
              </span>
            </div>
            <div className="rounded-lg border border-gray-800 bg-gray-900/80 px-4 py-3">
              <span className="text-gray-500">Last Updated: </span>
              <span className="font-semibold text-white">
                {new Date(data.monitored_at).toLocaleTimeString()}
              </span>
            </div>
          </div>

          <Card
            title="Order Alerts"
            subtitle={`${data.alerts.length} orders monitored`}
          >
            {data.alerts.length ? (
              <div className="space-y-3">
                {data.alerts.map((alert) => (
                  <div
                    key={alert.order_id}
                    className={`rounded-lg border p-4 ${
                      alert.attention === "far_from_fill"
                        ? "border-amber-500/30 bg-amber-500/5"
                        : alert.attention === "no_volume"
                        ? "border-red-500/30 bg-red-500/5"
                        : "border-gray-800 bg-gray-800/40"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <span className="font-semibold text-white">
                          {alert.symbol}
                        </span>
                        <span className="ml-2 text-sm text-gray-500">
                          Order #{alert.order_id}
                        </span>
                      </div>
                      {alert.attention && (
                        <StatusBadge
                          label={alert.attention.replace(/_/g, " ")}
                          variant={
                            alert.attention === "no_volume"
                              ? "danger"
                              : "warning"
                          }
                        />
                      )}
                    </div>

                    <div className="mt-3 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
                      <div>
                        <span className="text-xs text-gray-500">
                          Limit Price
                        </span>
                        <p className="font-mono text-white">
                          ${alert.limit_price.toFixed(2)}
                        </p>
                      </div>
                      <div>
                        <span className="text-xs text-gray-500">
                          Underlying
                        </span>
                        <p className="font-mono text-white">
                          ${alert.underlying_price.toFixed(2)}
                        </p>
                      </div>
                      {alert.option_bid !== undefined && (
                        <div>
                          <span className="text-xs text-gray-500">
                            Option Bid/Ask
                          </span>
                          <p className="font-mono text-white">
                            ${alert.option_bid?.toFixed(2)} / $
                            {alert.option_ask?.toFixed(2)}
                          </p>
                        </div>
                      )}
                      {alert.distance_to_fill_pct !== undefined && (
                        <div>
                          <span className="text-xs text-gray-500">
                            Distance to Fill
                          </span>
                          <p>
                            <PLValue
                              value={alert.distance_to_fill_pct}
                              format="percent"
                            />
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500">
                No orders requiring attention
              </p>
            )}
          </Card>
        </>
      ) : (
        <p className="text-sm text-gray-500">
          Monitor data will appear once orders are being tracked.
        </p>
      )}
    </div>
  );
}
