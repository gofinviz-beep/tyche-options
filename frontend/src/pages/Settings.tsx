import { useState, useEffect } from "react";
import { Card } from "@/components/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { useSystemConfig, useUpdateConfig } from "@/hooks/useApi";

export function Settings() {
  const { data: config, isLoading } = useSystemConfig();
  const updateConfig = useUpdateConfig();

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="mt-1 text-sm text-gray-500">
          Edit strategy parameters, risk limits, and watchlist. Changes are
          saved to your .env file and take effect on the next server restart or
          scan.
        </p>
      </div>

      {updateConfig.isSuccess && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-600">
          Settings saved successfully. Restart the backend to apply all changes.
        </div>
      )}
      {updateConfig.isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
          Failed to save: {updateConfig.error.message}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* System status (read-only) */}
        <Card title="System Status" subtitle="Read-only — configure in .env">
          <div className="space-y-3">
            <StatusRow
              label="Mode"
              badge={
                <StatusBadge
                  label={config?.sandbox_mode ? "SANDBOX" : "LIVE"}
                  variant={config?.sandbox_mode ? "warning" : "success"}
                />
              }
            />
            <StatusRow
              label="Trading"
              badge={
                <StatusBadge
                  label={
                    config?.preview_only ? "PREVIEW ONLY" : "LIVE TRADING"
                  }
                  variant={config?.preview_only ? "info" : "success"}
                />
              }
            />
            <StatusRow
              label="Broker"
              badge={
                <StatusBadge
                  label={
                    config?.broker_configured ? "Connected" : "Not configured"
                  }
                  variant={config?.broker_configured ? "success" : "neutral"}
                />
              }
            />
            <StatusRow
              label="LLM"
              badge={
                <StatusBadge
                  label={
                    config?.llm_configured ? "Gemini Active" : "Not configured"
                  }
                  variant={config?.llm_configured ? "success" : "neutral"}
                />
              }
            />
            <StatusRow
              label="Earnings API"
              badge={
                <StatusBadge
                  label={
                    config?.earnings_api_configured
                      ? "Connected"
                      : "Not configured"
                  }
                  variant={
                    config?.earnings_api_configured ? "success" : "neutral"
                  }
                />
              }
            />
          </div>
        </Card>

        {/* Capital */}
        {config && (
          <EditableNumberCard
            title="Available Capital"
            subtitle="Cash available for CSP collateral"
            value={config.available_capital}
            prefix="$"
            onSave={(val) =>
              updateConfig.mutate({ available_capital: val })
            }
            isPending={updateConfig.isPending}
          />
        )}

        {/* Risk limits */}
        {config && (
          <EditableSettingsCard
            title="Risk Limits"
            fields={[
              {
                key: "max_risk_per_trade_pct",
                label: "Max risk per trade %",
                value: config.risk_limits.max_risk_per_trade_pct,
              },
              {
                key: "max_account_exposure_pct",
                label: "Max account exposure %",
                value: config.risk_limits.max_account_exposure_pct,
              },
              {
                key: "max_concentration_per_ticker_pct",
                label: "Max concentration per ticker %",
                value: config.risk_limits.max_concentration_per_ticker_pct,
              },
              {
                key: "max_open_positions",
                label: "Max open positions",
                value: config.risk_limits.max_open_positions,
              },
              {
                key: "max_new_trades_per_day",
                label: "Max trades per day",
                value: config.risk_limits.max_new_trades_per_day,
              },
              {
                key: "max_contracts_per_position",
                label: "Max contracts per position",
                value: config.risk_limits.max_contracts_per_position,
              },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Wheel params */}
        {config && (
          <EditableSettingsCard
            title="Wheel Strategy Parameters"
            fields={[
              {
                key: "csp_target_dte_min",
                label: "CSP DTE min",
                value: config.wheel_params.csp_target_dte_min,
              },
              {
                key: "csp_target_dte_max",
                label: "CSP DTE max",
                value: config.wheel_params.csp_target_dte_max,
              },
              {
                key: "cc_target_dte_min",
                label: "CC DTE min",
                value: config.wheel_params.cc_target_dte_min,
              },
              {
                key: "cc_target_dte_max",
                label: "CC DTE max",
                value: config.wheel_params.cc_target_dte_max,
              },
              {
                key: "min_annualized_return_pct",
                label: "Min annualized return %",
                value: config.wheel_params.min_annualized_return_pct,
              },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Watchlist */}
        {config && (
          <WatchlistEditor
            symbols={config.watchlist}
            onSave={(symbols) => updateConfig.mutate({ watchlist: symbols })}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Workflow schedule (read-only) */}
        <Card
          title="Workflow Schedule"
          subtitle="Read-only — configure in .env"
        >
          <div className="space-y-2 text-sm">
            {config?.workflow_schedule &&
              Object.entries(config.workflow_schedule).map(([key, value]) => (
                <div
                  key={key}
                  className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2"
                >
                  <span className="text-gray-500">
                    {key.replace(/_/g, " ")}
                  </span>
                  <span className="font-mono text-gray-900">{value}</span>
                </div>
              ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

function EditableNumberCard({
  title,
  subtitle,
  value,
  prefix,
  onSave,
  isPending,
}: {
  title: string;
  subtitle: string;
  value: number;
  prefix?: string;
  onSave: (val: number) => void;
  isPending: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [editVal, setEditVal] = useState(value.toString());

  useEffect(() => {
    setEditVal(value.toString());
  }, [value]);

  const handleSave = () => {
    const num = parseFloat(editVal);
    if (!isNaN(num)) {
      onSave(num);
      setEditing(false);
    }
  };

  return (
    <Card title={title} subtitle={subtitle}>
      <div className="flex items-center justify-between">
        {editing ? (
          <div className="flex items-center gap-2">
            {prefix && <span className="text-gray-500">{prefix}</span>}
            <input
              type="text"
              value={editVal}
              onChange={(e) => setEditVal(e.target.value)}
              className="w-40 rounded-lg border border-gray-300 bg-white px-3 py-1.5 font-mono text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSave();
                if (e.key === "Escape") setEditing(false);
              }}
            />
          </div>
        ) : (
          <span className="text-2xl font-bold text-gray-900">
            {prefix}
            {value.toLocaleString()}
          </span>
        )}
        {editing ? (
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              disabled={isPending}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
            >
              {isPending ? "Saving..." : "Save"}
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setEditVal(value.toString());
              }}
              className="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-200"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => setEditing(true)}
            className="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-200"
          >
            Edit
          </button>
        )}
      </div>
    </Card>
  );
}

function EditableSettingsCard({
  title,
  fields,
  onSave,
  isPending,
}: {
  title: string;
  fields: { key: string; label: string; value: number }[];
  onSave: (updates: Record<string, number>) => void;
  isPending: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});

  useEffect(() => {
    const init: Record<string, string> = {};
    for (const f of fields) {
      init[f.key] = f.value.toString();
    }
    setValues(init);
  }, [fields.map((f) => f.value).join(",")]);

  const handleSave = () => {
    const updates: Record<string, number> = {};
    for (const f of fields) {
      const num = parseFloat(values[f.key] ?? "");
      if (!isNaN(num) && num !== f.value) {
        updates[f.key] = num;
      }
    }
    if (Object.keys(updates).length > 0) {
      onSave(updates);
    }
    setEditing(false);
  };

  return (
    <Card title={title}>
      <div className="space-y-2 text-sm">
        {fields.map((f) => (
          <div
            key={f.key}
            className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2"
          >
            <span className="text-gray-500">{f.label}</span>
            {editing ? (
              <input
                type="text"
                value={values[f.key] ?? ""}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, [f.key]: e.target.value }))
                }
                className="w-24 rounded border border-gray-300 bg-white px-2 py-1 text-right font-mono text-sm text-gray-900 focus:border-blue-500 focus:outline-none"
              />
            ) : (
              <span className="font-mono text-gray-900">{f.value}</span>
            )}
          </div>
        ))}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        {editing ? (
          <>
            <button
              onClick={handleSave}
              disabled={isPending}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
            >
              {isPending ? "Saving..." : "Save"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-200"
            >
              Cancel
            </button>
          </>
        ) : (
          <button
            onClick={() => setEditing(true)}
            className="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-200"
          >
            Edit
          </button>
        )}
      </div>
    </Card>
  );
}

function WatchlistEditor({
  symbols,
  onSave,
  isPending,
}: {
  symbols: string[];
  onSave: (symbols: string[]) => void;
  isPending: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [newSymbol, setNewSymbol] = useState("");
  const [localSymbols, setLocalSymbols] = useState<string[]>(symbols);

  useEffect(() => {
    setLocalSymbols(symbols);
  }, [symbols.join(",")]);

  const addSymbol = () => {
    const sym = newSymbol.trim().toUpperCase();
    if (sym && !localSymbols.includes(sym)) {
      setLocalSymbols([...localSymbols, sym]);
      setNewSymbol("");
    }
  };

  const removeSymbol = (sym: string) => {
    setLocalSymbols(localSymbols.filter((s) => s !== sym));
  };

  const handleSave = () => {
    onSave(localSymbols);
    setEditing(false);
  };

  return (
    <Card title="Watchlist">
      {editing ? (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {localSymbols.map((sym) => (
              <span
                key={sym}
                className="flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-sm font-semibold text-blue-700"
              >
                {sym}
                <button
                  onClick={() => removeSymbol(sym)}
                  className="text-blue-400 transition-colors hover:text-red-500"
                >
                  &times;
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value)}
              placeholder="Add ticker..."
              className="flex-1 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addSymbol();
                }
              }}
            />
            <button
              onClick={addSymbol}
              className="rounded-lg bg-gray-100 px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-200"
            >
              Add
            </button>
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              disabled={isPending}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
            >
              {isPending ? "Saving..." : "Save Watchlist"}
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setLocalSymbols(symbols);
              }}
              className="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-200"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {localSymbols.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {localSymbols.map((sym) => (
                <span
                  key={sym}
                  className="rounded-full bg-blue-50 px-3 py-1 text-sm font-semibold text-blue-700"
                >
                  {sym}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400">
              No watchlist symbols configured.
            </p>
          )}
          <div className="flex justify-end">
            <button
              onClick={() => setEditing(true)}
              className="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-200"
            >
              Edit
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}

function StatusRow({
  label,
  badge,
}: {
  label: string;
  badge: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-gray-500">{label}</span>
      {badge}
    </div>
  );
}
