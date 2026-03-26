import { useState } from "react";
import { Card } from "@/components/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { PLValue } from "@/components/PLValue";
import {
  useOrderIntents,
  useApproveIntent,
  useRejectIntent,
  useRecordExecution,
  useCreateIntent,
} from "@/hooks/useApi";
import type { OrderIntent } from "@/types";

const STATUS_FILTERS = ["all", "pending", "approved", "rejected", "executed"] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number];

export function Intents() {
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [showCreate, setShowCreate] = useState(false);
  const { data, isLoading } = useOrderIntents(
    filter === "all" ? undefined : filter,
  );
  const approveIntent = useApproveIntent();
  const rejectIntent = useRejectIntent();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Trade Intents</h1>
        <div className="flex items-center gap-4">
          {data && (
            <div className="flex gap-3 text-sm">
              <StatChip label="Pending" value={data.pending} color="amber" />
              <StatChip label="Approved" value={data.approved} color="blue" />
              <StatChip label="Executed" value={data.executed} color="emerald" />
            </div>
          )}
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
          >
            {showCreate ? "Cancel" : "Create Intent"}
          </button>
        </div>
      </div>

      {showCreate && (
        <CreateIntentForm onClose={() => setShowCreate(false)} />
      )}

      <div className="flex gap-2">
        {STATUS_FILTERS.map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              filter === s
                ? "bg-blue-600 text-white"
                : "bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200"
            }`}
          >
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex h-32 items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-600 border-t-white" />
        </div>
      ) : data?.intents.length ? (
        <div className="space-y-4">
          {data.intents.map((intent) => (
            <IntentCard
              key={intent.id}
              intent={intent}
              onApprove={(note) =>
                approveIntent.mutate({ id: intent.id, note })
              }
              onReject={(reason) =>
                rejectIntent.mutate({ id: intent.id, reason })
              }
              isApproving={approveIntent.isPending}
              isRejecting={rejectIntent.isPending}
            />
          ))}
        </div>
      ) : (
        <Card title="No Intents">
          <p className="text-sm text-gray-500">
            No trade intents match this filter. Run a scan to generate
            recommendations.
          </p>
        </Card>
      )}
    </div>
  );
}

function CreateIntentForm({ onClose }: { onClose: () => void }) {
  const createIntent = useCreateIntent();
  const [symbol, setSymbol] = useState("");
  const [strike, setStrike] = useState("");
  const [expiration, setExpiration] = useState("");
  const [quantity, setQuantity] = useState("");
  const [limitPrice, setLimitPrice] = useState("");
  const [thesis, setThesis] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const s = parseFloat(strike);
    const q = parseInt(quantity, 10);
    if (!symbol || isNaN(s) || isNaN(q) || !expiration) return;

    createIntent.mutate(
      {
        symbol: symbol.toUpperCase(),
        strike: s,
        expiration,
        quantity: q,
        limit_price: limitPrice ? parseFloat(limitPrice) : undefined,
        thesis: thesis || undefined,
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Card title="Create Manual Intent">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <InputField label="Symbol" value={symbol} onChange={setSymbol} placeholder="PL" required />
          <InputField label="Strike" value={strike} onChange={setStrike} placeholder="23.00" required />
          <InputField label="Expiration" value={expiration} onChange={setExpiration} placeholder="2026-04-03" required />
          <InputField label="Contracts" value={quantity} onChange={setQuantity} placeholder="40" required />
          <InputField label="Limit Price" value={limitPrice} onChange={setLimitPrice} placeholder="1.80" />
        </div>
        <div>
          <label className="text-xs text-gray-500">Thesis (optional)</label>
          <textarea
            value={thesis}
            onChange={(e) => setThesis(e.target.value)}
            placeholder="Why this trade?"
            rows={2}
            className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div className="flex gap-3">
          <button
            type="submit"
            disabled={createIntent.isPending}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
          >
            {createIntent.isPending ? "Creating..." : "Create Intent"}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-gray-800 px-4 py-2 text-sm font-medium text-gray-300 transition-colors hover:bg-gray-700"
          >
            Cancel
          </button>
        </div>
        {createIntent.isError && (
          <p className="text-sm text-red-400">Error: {createIntent.error.message}</p>
        )}
      </form>
    </Card>
  );
}

function IntentCard({
  intent,
  onApprove,
  onReject,
  isApproving,
  isRejecting,
}: {
  intent: OrderIntent;
  onApprove: (note?: string) => void;
  onReject: (reason?: string) => void;
  isApproving: boolean;
  isRejecting: boolean;
}) {
  const [showExecute, setShowExecute] = useState(false);

  const statusVariant = {
    pending: "warning" as const,
    approved: "info" as const,
    rejected: "danger" as const,
    executed: "success" as const,
    expired: "neutral" as const,
    closed: "neutral" as const,
  };

  const convictionColor = {
    high: "text-emerald-400",
    medium: "text-amber-400",
    low: "text-red-400",
    none: "text-gray-500",
  };

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/80 p-5">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xl font-bold text-white">{intent.symbol}</span>
          <StatusBadge
            label={intent.status}
            variant={statusVariant[intent.status as keyof typeof statusVariant] ?? "neutral"}
          />
          <StatusBadge
            label={intent.strategy.toUpperCase()}
            variant="info"
          />
        </div>
        <span className="text-xs text-gray-500">
          {new Date(intent.created_at).toLocaleString()}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
        <Detail label="Strike" value={intent.strike ? `$${intent.strike.toFixed(2)}` : "—"} />
        <Detail label="Expiration" value={intent.expiration ?? "—"} />
        <Detail label="Quantity" value={String(intent.quantity)} />
        <Detail
          label="Limit Price"
          value={intent.limit_price ? `$${intent.limit_price.toFixed(2)}` : "—"}
        />
        <Detail
          label="Est. Premium"
          value={`$${intent.estimated_premium.toLocaleString()}`}
        />
        <Detail
          label="Collateral"
          value={`$${intent.collateral_required.toLocaleString()}`}
        />
        <Detail
          label="Ann. Return"
          value={<PLValue value={intent.annualized_return_pct} format="percent" />}
        />
        <Detail
          label="Conviction"
          value={
            <span className={convictionColor[intent.conviction_level as keyof typeof convictionColor] ?? "text-gray-500"}>
              {intent.conviction_level} ({intent.trend_state?.replace(/_/g, " ")})
            </span>
          }
        />
      </div>

      {intent.thesis && (
        <div className="mt-3 rounded-lg bg-gray-800/50 p-3">
          <p className="text-xs font-medium text-gray-500">Thesis</p>
          <p className="mt-1 text-sm text-gray-300">{intent.thesis}</p>
        </div>
      )}

      {intent.risks && (
        <div className="mt-2">
          <span className="text-xs text-gray-500">Risks: </span>
          <span className="text-xs text-amber-400">{intent.risks}</span>
        </div>
      )}

      {intent.risk_summary && (
        <div className="mt-2 flex items-center gap-2">
          <StatusBadge
            label={intent.risk_passed ? "Risk Passed" : "Risk Failed"}
            variant={intent.risk_passed ? "success" : "danger"}
          />
          <span className="text-xs text-gray-500">{intent.risk_summary}</span>
        </div>
      )}

      {intent.status === "pending" && (
        <div className="mt-4 flex gap-3 border-t border-gray-800 pt-4">
          <button
            onClick={() => onApprove()}
            disabled={isApproving}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
          >
            {isApproving ? "Approving..." : "Approve"}
          </button>
          <button
            onClick={() => onReject()}
            disabled={isRejecting}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
          >
            {isRejecting ? "Rejecting..." : "Reject"}
          </button>
        </div>
      )}

      {intent.status === "approved" && (
        <div className="mt-4 border-t border-gray-800 pt-4">
          {showExecute ? (
            <ExecutionForm
              intent={intent}
              onClose={() => setShowExecute(false)}
            />
          ) : (
            <button
              onClick={() => setShowExecute(true)}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
            >
              Record Manual Execution
            </button>
          )}
        </div>
      )}

      {intent.status === "executed" && intent.actual_fill_price != null && (
        <div className="mt-4 grid grid-cols-2 gap-4 border-t border-gray-800 pt-4 text-sm sm:grid-cols-4">
          <Detail label="Fill Price" value={`$${intent.actual_fill_price.toFixed(2)}`} />
          <Detail label="Filled Qty" value={String(intent.actual_quantity ?? intent.quantity)} />
          <Detail
            label="Actual Premium"
            value={intent.actual_premium != null ? `$${intent.actual_premium.toLocaleString()}` : "—"}
          />
          <Detail
            label="Broker Conf."
            value={intent.broker_confirmation ?? "—"}
          />
        </div>
      )}
    </div>
  );
}

function ExecutionForm({
  intent,
  onClose,
}: {
  intent: OrderIntent;
  onClose: () => void;
}) {
  const recordExec = useRecordExecution();
  const [fillPrice, setFillPrice] = useState(
    intent.limit_price?.toString() ?? "",
  );
  const [quantity, setQuantity] = useState(intent.quantity.toString());
  const [premium, setPremium] = useState("");
  const [confirmation, setConfirmation] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const fp = parseFloat(fillPrice);
    const qty = parseInt(quantity, 10);
    if (isNaN(fp) || isNaN(qty)) return;

    recordExec.mutate(
      {
        id: intent.id,
        data: {
          fill_price: fp,
          quantity: qty,
          premium_received: premium ? parseFloat(premium) : undefined,
          broker_confirmation: confirmation || undefined,
        },
      },
      { onSuccess: onClose },
    );
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <p className="text-sm font-medium text-gray-300">
        Record execution details from Fidelity:
      </p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <InputField
          label="Fill Price"
          value={fillPrice}
          onChange={setFillPrice}
          placeholder="1.80"
          required
        />
        <InputField
          label="Quantity"
          value={quantity}
          onChange={setQuantity}
          placeholder="40"
          required
        />
        <InputField
          label="Premium Received"
          value={premium}
          onChange={setPremium}
          placeholder="7173.08"
        />
        <InputField
          label="Broker Confirmation"
          value={confirmation}
          onChange={setConfirmation}
          placeholder="FID-12345"
        />
      </div>
      <div className="flex gap-3">
        <button
          type="submit"
          disabled={recordExec.isPending}
          className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
        >
          {recordExec.isPending ? "Recording..." : "Confirm Execution"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg bg-gray-800 px-4 py-2 text-sm font-medium text-gray-300 transition-colors hover:bg-gray-700"
        >
          Cancel
        </button>
      </div>
      {recordExec.isError && (
        <p className="text-sm text-red-400">
          Error: {recordExec.error.message}
        </p>
      )}
    </form>
  );
}

function InputField({
  label,
  value,
  onChange,
  placeholder,
  required,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  required?: boolean;
}) {
  return (
    <div>
      <label className="text-xs text-gray-500">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-1.5 text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 focus:outline-none"
      />
    </div>
  );
}

function Detail({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-xs text-gray-500">{label}</p>
      <p className="mt-0.5 font-mono text-sm text-white">{value}</p>
    </div>
  );
}

function StatChip({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="flex items-center gap-1.5 rounded-full bg-gray-800 px-3 py-1 text-xs">
      <span className={`h-2 w-2 rounded-full bg-${color}-500`} />
      <span className="text-gray-400">{label}:</span>
      <span className="font-semibold text-white">{value}</span>
    </div>
  );
}
