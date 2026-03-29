import { Card } from "@/components/Card";
import { PLValue } from "@/components/PLValue";
import { StatusBadge } from "@/components/StatusBadge";
import { useOpenOrders, useCancelOrder, useOrderIntents } from "@/hooks/useApi";
import type { OrderIntent } from "@/types";

export function Orders() {
  const { data: orders, isLoading } = useOpenOrders();
  const cancelOrder = useCancelOrder();
  const { data: executedIntents, isLoading: executedLoading } =
    useOrderIntents("executed");

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Orders</h1>

      <Card
        title="Open Orders"
        subtitle={`${orders?.length ?? 0} active orders`}
      >
        {isLoading ? (
          <div className="flex h-32 items-center justify-center">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
          </div>
        ) : orders?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-xs text-gray-400">
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
                    className="border-b border-gray-200 text-gray-700"
                  >
                    <td className="py-3 pr-4 font-semibold text-gray-900">
                      {order.symbol}
                      {order.option_symbol && (
                        <p className="text-xs font-normal text-gray-400">
                          {order.option_symbol}
                        </p>
                      )}
                    </td>
                    <td className="py-3 pr-4">
                      <span
                        className={
                          order.side.includes("sell")
                            ? "text-red-600"
                            : "text-emerald-600"
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
                      <StatusBadge label={order.strategy} variant="info" />
                    </td>
                    <td className="py-3 text-right">
                      <button
                        onClick={() =>
                          cancelOrder.mutate(order.broker_order_id)
                        }
                        disabled={cancelOrder.isPending}
                        className="rounded px-3 py-1 text-xs text-red-600 transition-colors hover:bg-red-50"
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
          <p className="text-sm text-gray-400">No open orders</p>
        )}
      </Card>

      {/* Executed Trades (from Intents) */}
      <Card
        title="Executed Trades"
        subtitle={
          executedIntents
            ? `${executedIntents.executed} filled trades`
            : "Loading..."
        }
      >
        {executedLoading ? (
          <div className="flex h-32 items-center justify-center">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
          </div>
        ) : executedIntents?.intents.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-xs text-gray-400">
                  <th className="pb-2 pr-4">Symbol</th>
                  <th className="pb-2 pr-4">Strategy</th>
                  <th className="pb-2 pr-4 text-right">Strike</th>
                  <th className="pb-2 pr-4 text-right">Expiration</th>
                  <th className="pb-2 pr-4 text-right">Qty</th>
                  <th className="pb-2 pr-4 text-right">Fill Price</th>
                  <th className="pb-2 pr-4 text-right">Premium</th>
                  <th className="pb-2 pr-4 text-right">Ann. Return</th>
                  <th className="pb-2 pr-4">Conviction</th>
                  <th className="pb-2 text-right">Executed</th>
                </tr>
              </thead>
              <tbody>
                {executedIntents.intents.map((intent: OrderIntent) => (
                  <ExecutedRow key={intent.id} intent={intent} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-400">
            No executed trades recorded yet. Approve intents and record
            executions from the Intents page.
          </p>
        )}
      </Card>
    </div>
  );
}

function ExecutedRow({ intent }: { intent: OrderIntent }) {
  const convictionColor: Record<string, string> = {
    high: "text-emerald-600",
    medium: "text-amber-600",
    low: "text-red-600",
    none: "text-gray-400",
  };

  return (
    <tr className="border-b border-gray-200 text-gray-700">
      <td className="py-3 pr-4 font-semibold text-gray-900">{intent.symbol}</td>
      <td className="py-3 pr-4">
        <StatusBadge label={intent.strategy.toUpperCase()} variant="info" />
      </td>
      <td className="py-3 pr-4 text-right font-mono">
        {intent.strike ? `$${intent.strike.toFixed(2)}` : "—"}
      </td>
      <td className="py-3 pr-4 text-right text-xs">
        {intent.expiration ?? "—"}
      </td>
      <td className="py-3 pr-4 text-right font-mono">
        {intent.actual_quantity ?? intent.quantity}
      </td>
      <td className="py-3 pr-4 text-right font-mono">
        {intent.actual_fill_price != null
          ? `$${intent.actual_fill_price.toFixed(2)}`
          : "—"}
      </td>
      <td className="py-3 pr-4 text-right font-mono text-emerald-600">
        {intent.actual_premium != null
          ? `$${intent.actual_premium.toLocaleString()}`
          : intent.estimated_premium
            ? `$${intent.estimated_premium.toLocaleString()}`
            : "—"}
      </td>
      <td className="py-3 pr-4 text-right">
        <PLValue value={intent.annualized_return_pct} format="percent" />
      </td>
      <td className="py-3 pr-4">
        <span
          className={
            convictionColor[intent.conviction_level] ?? "text-gray-400"
          }
        >
          {intent.conviction_level}
        </span>
      </td>
      <td className="py-3 text-right text-xs text-gray-400">
        {intent.executed_at
          ? new Date(intent.executed_at).toLocaleDateString()
          : new Date(intent.updated_at).toLocaleDateString()}
      </td>
    </tr>
  );
}
