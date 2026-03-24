import { Card } from "@/components/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { useOpenOrders, useCancelOrder } from "@/hooks/useApi";

export function Orders() {
  const { data: orders, isLoading } = useOpenOrders();
  const cancelOrder = useCancelOrder();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Orders</h1>

      <Card
        title="Open Orders"
        subtitle={`${orders?.length ?? 0} active orders`}
      >
        {isLoading ? (
          <div className="flex h-32 items-center justify-center">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-600 border-t-white" />
          </div>
        ) : orders?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-xs text-gray-500">
                  <th className="pb-2 pr-4">Symbol</th>
                  <th className="pb-2 pr-4">Side</th>
                  <th className="pb-2 pr-4 text-right">Qty</th>
                  <th className="pb-2 pr-4 text-right">Price</th>
                  <th className="pb-2 pr-4">Type</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2 pr-4">Strategy</th>
                  <th className="pb-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr
                    key={order.id}
                    className="border-b border-gray-800/50 text-gray-300"
                  >
                    <td className="py-3 pr-4 font-semibold text-white">
                      {order.symbol}
                      {order.option_symbol && (
                        <p className="text-xs font-normal text-gray-500">
                          {order.option_symbol}
                        </p>
                      )}
                    </td>
                    <td className="py-3 pr-4">
                      <span
                        className={
                          order.side.includes("sell")
                            ? "text-red-400"
                            : "text-emerald-400"
                        }
                      >
                        {order.side.replace(/_/g, " ")}
                      </span>
                    </td>
                    <td className="py-3 pr-4 text-right font-mono">
                      {order.quantity}
                    </td>
                    <td className="py-3 pr-4 text-right font-mono">
                      {order.limit_price
                        ? `$${order.limit_price.toFixed(2)}`
                        : "MKT"}
                    </td>
                    <td className="py-3 pr-4">{order.order_type}</td>
                    <td className="py-3 pr-4">
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
                    </td>
                    <td className="py-3 pr-4">
                      <StatusBadge
                        label={order.strategy}
                        variant="info"
                      />
                    </td>
                    <td className="py-3 text-right">
                      <button
                        onClick={() =>
                          cancelOrder.mutate(order.broker_order_id)
                        }
                        disabled={cancelOrder.isPending}
                        className="rounded px-3 py-1 text-xs text-red-400 transition-colors hover:bg-red-500/20"
                      >
                        Cancel
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-500">No open orders</p>
        )}
      </Card>
    </div>
  );
}
