import { useState } from "react";
import { Card } from "@/components/Card";
import { PLValue } from "@/components/PLValue";
import { StatusBadge } from "@/components/StatusBadge";
import {
  useTrackedPositions,
  useTrackPosition,
  useUntrackPosition,
} from "@/hooks/useApi";
import type { TrackedPositionStatus } from "@/types";

export function Monitor() {
  const { data, isLoading, refetch, isFetching } = useTrackedPositions();
  const trackPosition = useTrackPosition();
  const untrack = useUntrackPosition();
  const [showTrackForm, setShowTrackForm] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Position Monitor</h1>
          <p className="mt-1 text-sm text-gray-500">
            Track active positions for real-time alerts — profit targets, strike
            proximity, and adverse moves.
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="rounded-lg bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-200 disabled:opacity-50"
          >
            {isFetching ? "Refreshing..." : "Refresh"}
          </button>
          <button
            onClick={() => setShowTrackForm(!showTrackForm)}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
          >
            {showTrackForm ? "Cancel" : "Track Position"}
          </button>
        </div>
      </div>

      {showTrackForm && (
        <TrackPositionForm
          onSubmit={(data) => {
            trackPosition.mutate(data, {
              onSuccess: () => setShowTrackForm(false),
            });
          }}
          onCancel={() => setShowTrackForm(false)}
          isPending={trackPosition.isPending}
          error={trackPosition.isError ? trackPosition.error.message : null}
        />
      )}

      {isLoading ? (
        <div className="flex h-32 items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
        </div>
      ) : data && data.tracked_count > 0 ? (
        <>
          <div className="flex gap-4 text-sm">
            <div className="rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-sm">
              <span className="text-gray-400">Tracked: </span>
              <span className="font-semibold text-gray-900">
                {data.tracked_count}
              </span>
            </div>
            {data.alerts.length > 0 && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
                <span className="text-amber-600">
                  {data.alerts.length} alert(s)
                </span>
              </div>
            )}
          </div>

          <div className="space-y-4">
            {data.positions.map((pos: TrackedPositionStatus) => (
              <PositionCard
                key={pos.option_symbol}
                position={pos}
                onUntrack={() => untrack.mutate(pos.option_symbol)}
                isUntracking={untrack.isPending}
              />
            ))}
          </div>
        </>
      ) : (
        <Card title="No Tracked Positions">
          <p className="text-sm text-gray-500">
            Track filled positions to monitor them in real time. Click{" "}
            <span className="font-medium text-blue-600">"Track Position"</span>{" "}
            above to add a short put (CSP) or short call (covered call) you've executed.
          </p>
          <p className="mt-2 text-sm text-gray-400">
            The monitor will alert you when profit targets are hit, when the
            stock approaches your strike, or if adverse intraday moves occur.
          </p>
        </Card>
      )}
    </div>
  );
}

function TrackPositionForm({
  onSubmit,
  onCancel,
  isPending,
  error,
}: {
  onSubmit: (data: import("@/types").TrackPositionRequest) => void;
  onCancel: () => void;
  isPending: boolean;
  error: string | null;
}) {
  const [symbol, setSymbol] = useState("");
  const [optionSymbol, setOptionSymbol] = useState("");
  const [strike, setStrike] = useState("");
  const [expiration, setExpiration] = useState("");
  const [entryPrice, setEntryPrice] = useState("");
  const [contracts, setContracts] = useState("");
  const [underlyingPrice, setUnderlyingPrice] = useState("");
  const [positionType, setPositionType] = useState<"short_put" | "short_call">("short_put");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const s = parseFloat(strike);
    const ep = parseFloat(entryPrice);
    const c = parseInt(contracts, 10);
    const up = parseFloat(underlyingPrice);
    if (!symbol || isNaN(s) || isNaN(ep) || isNaN(c) || !expiration || isNaN(up))
      return;

    const typeChar = positionType === "short_put" ? "P" : "C";
    const oSym =
      optionSymbol ||
      `${symbol.toUpperCase()}${expiration.replace(/-/g, "")}${typeChar}${strike.replace(".", "")}`;

    onSubmit({
      symbol: symbol.toUpperCase(),
      option_symbol: oSym,
      position_type: positionType,
      strike: s,
      expiration,
      entry_price: ep,
      contracts: c,
      underlying_at_entry: up,
    });
  };

  return (
    <Card title="Track a Position">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setPositionType("short_put")}
            className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              positionType === "short_put"
                ? "bg-blue-600 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            Short Put (CSP)
          </button>
          <button
            type="button"
            onClick={() => setPositionType("short_call")}
            className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              positionType === "short_call"
                ? "bg-blue-600 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            Short Call (CC)
          </button>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <FormField
            label="Symbol"
            value={symbol}
            onChange={setSymbol}
            placeholder="FSLY"
            required
          />
          <FormField
            label="Strike"
            value={strike}
            onChange={setStrike}
            placeholder="29.50"
            required
          />
          <div>
            <label className="text-xs text-gray-400">Expiration</label>
            <input
              type="date"
              value={expiration}
              onChange={(e) => setExpiration(e.target.value)}
              required
              className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <FormField
            label="Contracts"
            value={contracts}
            onChange={setContracts}
            placeholder="37"
            required
          />
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <FormField
            label="Entry Price (premium)"
            value={entryPrice}
            onChange={setEntryPrice}
            placeholder="1.55"
            required
          />
          <FormField
            label="Underlying at Entry"
            value={underlyingPrice}
            onChange={setUnderlyingPrice}
            placeholder="31.20"
            required
          />
          <FormField
            label="Option Symbol (auto-generated)"
            value={optionSymbol}
            onChange={setOptionSymbol}
            placeholder={`FSLY260402${positionType === "short_put" ? "P" : "C"}02950`}
          />
        </div>
        <div className="flex gap-3">
          <button
            type="submit"
            disabled={isPending}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
          >
            {isPending ? "Tracking..." : "Start Tracking"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-200"
          >
            Cancel
          </button>
        </div>
        {error && <p className="text-sm text-red-600">Error: {error}</p>}
      </form>
    </Card>
  );
}

function PositionCard({
  position: pos,
  onUntrack,
  isUntracking,
}: {
  position: TrackedPositionStatus;
  onUntrack: () => void;
  isUntracking: boolean;
}) {
  const itm = pos.distance_to_strike_pct < 0;

  return (
    <div
      className={`rounded-xl border p-5 ${
        pos.alerts.some((a) => a.severity === "critical")
          ? "border-red-200 bg-red-50"
          : "border-gray-200 bg-white shadow-sm"
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xl font-bold text-gray-900">{pos.symbol}</span>
          <StatusBadge
            label={pos.position_type.replace(/_/g, " ")}
            variant="info"
          />
          <span className="text-sm text-gray-400">{pos.dte}d to exp</span>
          {itm && <StatusBadge label="ITM" variant="danger" />}
        </div>
        <button
          onClick={onUntrack}
          disabled={isUntracking}
          className="rounded px-3 py-1 text-xs text-red-600 transition-colors hover:bg-red-50"
        >
          {isUntracking ? "Removing..." : "Remove"}
        </button>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-6">
        <Stat label="Strike" value={`$${pos.strike.toFixed(2)}`} />
        <Stat label="Entry Premium" value={`$${pos.entry_price.toFixed(2)}`} />
        <Stat label="Contracts" value={String(pos.contracts)} />
        <Stat label="Expiration" value={pos.expiration} />
        <Stat
          label="Entry Underlying"
          value={`$${pos.underlying_at_entry.toFixed(2)}`}
        />
        <Stat
          label="Current Price"
          value={`$${pos.underlying_price.toFixed(2)}`}
        />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 border-t border-gray-200 pt-4 text-sm sm:grid-cols-5">
        <Stat
          label="Option Bid/Ask"
          value={`$${pos.option_bid.toFixed(2)} / $${pos.option_ask.toFixed(2)}`}
        />
        <Stat label="Option Mid" value={`$${pos.option_mid.toFixed(2)}`} />
        <Stat label="Delta" value={pos.delta.toFixed(3)} />
        <div>
          <p className="text-xs text-gray-400">P&L per Contract</p>
          <p className="mt-0.5">
            <PLValue value={pos.pnl_per_contract} />
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-400">Total P&L</p>
          <p className="mt-0.5 text-lg font-bold">
            <PLValue value={pos.total_pnl} />
          </p>
        </div>
      </div>

      {pos.alerts.length > 0 && (
        <div className="mt-4 space-y-3 border-t border-gray-200 pt-4">
          {pos.alerts.map((alert, i) => (
            <div
              key={i}
              className={`rounded-lg p-3 text-sm ${
                alert.severity === "critical"
                  ? "border border-red-200 bg-red-50"
                  : alert.severity === "warning"
                    ? "border border-amber-200 bg-amber-50"
                    : "border border-gray-200 bg-gray-50"
              }`}
            >
              <div className="flex items-center gap-2">
                <StatusBadge
                  label={alert.alert_type.replace(/_/g, " ")}
                  variant={
                    alert.severity === "critical"
                      ? "danger"
                      : alert.severity === "warning"
                        ? "warning"
                        : "neutral"
                  }
                />
                <span
                  className={
                    alert.severity === "critical"
                      ? "text-red-600"
                      : alert.severity === "warning"
                        ? "text-amber-600"
                        : "text-gray-500"
                  }
                >
                  {alert.message}
                </span>
              </div>
              {alert.suggested_actions && alert.suggested_actions.length > 0 && (
                <div className="mt-2 space-y-1 pl-2">
                  {alert.suggested_actions.map((sa, j) => (
                    <div key={j} className="text-xs text-gray-500">
                      <span className="font-medium text-gray-700">
                        {sa.action.replace(/_/g, " ")}:
                      </span>{" "}
                      {sa.reason}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-gray-400">{label}</p>
      <p className="mt-0.5 font-mono text-gray-900">{value}</p>
    </div>
  );
}

function FormField({
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
      <label className="text-xs text-gray-400">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
      />
    </div>
  );
}
