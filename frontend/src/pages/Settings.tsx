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
          Edit strategy parameters, risk limits, and watchlist. Changes take
          effect immediately.
        </p>
      </div>

      {updateConfig.isSuccess && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-600">
          Settings saved and applied.
        </div>
      )}
      {updateConfig.isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
          Failed to save: {updateConfig.error.message}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* System status (read-only) */}
        <Card title="System Status" subtitle="Read-only — set via environment variables">
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
              { key: "max_risk_per_trade_pct", label: "Max risk per trade %", value: config.risk_limits.max_risk_per_trade_pct },
              { key: "max_account_exposure_pct", label: "Max account exposure %", value: config.risk_limits.max_account_exposure_pct },
              { key: "max_concentration_per_ticker_pct", label: "Max concentration per ticker %", value: config.risk_limits.max_concentration_per_ticker_pct },
              { key: "max_open_positions", label: "Max open positions", value: config.risk_limits.max_open_positions },
              { key: "max_new_trades_per_day", label: "Max trades per day", value: config.risk_limits.max_new_trades_per_day },
              { key: "max_contracts_per_position", label: "Max contracts per position", value: config.risk_limits.max_contracts_per_position },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Universe filters */}
        {config && (
          <EditableSettingsCard
            title="Universe Filters"
            subtitle="Scanner pipeline filter thresholds"
            fields={[
              { key: "min_market_cap_millions", label: "Min market cap ($M)", value: config.universe_filters.min_market_cap_millions },
              { key: "min_institutional_pct", label: "Min institutional ownership (0–1)", value: config.universe_filters.min_institutional_pct },
              { key: "min_avg_volume", label: "Min avg daily volume", value: config.universe_filters.min_avg_volume },
              { key: "min_stock_price", label: "Min stock price ($)", value: config.universe_filters.min_stock_price },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Conviction Engine */}
        {config && (
          <EditableSettingsCard
            title="Conviction Engine"
            subtitle="EMA thresholds for trend analysis and CSP eligibility"
            fields={[
              { key: "ema_fast_period", label: "Fast EMA period", value: config.conviction_engine.ema_fast_period },
              { key: "ema_slow_period", label: "Slow EMA period", value: config.conviction_engine.ema_slow_period },
              { key: "max_extension_pct", label: "Max extension above 8-EMA (%)", value: config.conviction_engine.max_extension_pct },
              { key: "min_days_above_emas", label: "Min days above both EMAs", value: config.conviction_engine.min_days_above_emas },
              { key: "max_days_above_emas", label: "Max days above both EMAs", value: config.conviction_engine.max_days_above_emas },
              { key: "pullback_proximity_pct", label: "Pullback proximity (%)", value: config.conviction_engine.pullback_proximity_pct },
              { key: "bootstrap_days", label: "OHLCV bootstrap days", value: config.conviction_engine.bootstrap_days },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Pullback CSP */}
        {config && (
          <EditableMixedCard
            title="Pullback CSP (Path B)"
            subtitle="Strike placement and eligibility for pullback entries"
            fields={[
              { key: "pullback_csp_enabled", label: "Pullback CSP enabled", type: "bool", value: config.pullback_csp.pullback_csp_enabled },
              { key: "min_prior_streak", label: "Min prior streak (days)", type: "number", value: config.pullback_csp.min_prior_streak },
              { key: "pullback_strike_offset_pct", label: "Strike offset below EMA (%)", type: "number", value: config.pullback_csp.pullback_strike_offset_pct },
              { key: "pullback_strike_ceiling_pct", label: "Strike ceiling below EMA (%)", type: "number", value: config.pullback_csp.pullback_strike_ceiling_pct },
              { key: "earliest_expiration_only", label: "Earliest expiration only", type: "bool", value: config.pullback_csp.earliest_expiration_only },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Options scan */}
        {config && (
          <EditableMixedCard
            title="Options Scan"
            subtitle="Strike range, expiration depth, LLM parallelism"
            fields={[
              { key: "max_expiration_dates", label: "Max expiration dates", type: "number", value: config.options_scan.max_expiration_dates },
              { key: "expiration_mode", label: "Expiration mode", type: "string", value: config.options_scan.expiration_mode },
              { key: "strike_range_pct", label: "Strike range below EMA (%)", type: "number", value: config.options_scan.strike_range_pct },
              { key: "llm_concurrency", label: "LLM parallel calls", type: "number", value: config.options_scan.llm_concurrency },
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
              { key: "csp_target_dte_min", label: "CSP DTE min", value: config.wheel_params.csp_target_dte_min },
              { key: "csp_target_dte_max", label: "CSP DTE max", value: config.wheel_params.csp_target_dte_max },
              { key: "cc_target_dte_min", label: "CC DTE min", value: config.wheel_params.cc_target_dte_min },
              { key: "cc_target_dte_max", label: "CC DTE max", value: config.wheel_params.cc_target_dte_max },
              { key: "min_annualized_return_pct", label: "Min annualized return %", value: config.wheel_params.min_annualized_return_pct },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Workflow schedule */}
        {config && (
          <EditableMixedCard
            title="Workflow Schedule"
            subtitle="Scheduled job times (US/Eastern)"
            fields={[
              { key: "morning_scan_time", label: "Morning scan (HH:MM)", type: "string", value: config.workflow_schedule.morning_scan_time },
              { key: "order_monitor_interval_min", label: "Order monitor interval (min)", type: "number", value: config.workflow_schedule.order_monitor_interval_min },
              { key: "midday_review_time", label: "Midday review (HH:MM)", type: "string", value: config.workflow_schedule.midday_review_time },
              { key: "eod_journal_time", label: "EOD journal (HH:MM)", type: "string", value: config.workflow_schedule.eod_journal_time },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Options Snapshot */}
        {config && (
          <EditableMixedCard
            title="Options Chain Snapshots"
            subtitle="Daily Tradier chain capture settings"
            fields={[
              { key: "options_snapshot_enabled", label: "Snapshots enabled", type: "bool", value: config.options_snapshot.options_snapshot_enabled },
              { key: "options_snapshot_time", label: "Snapshot time (HH:MM)", type: "string", value: config.options_snapshot.options_snapshot_time },
              { key: "options_snapshot_min_market_cap", label: "Min market cap ($)", type: "number", value: config.options_snapshot.options_snapshot_min_market_cap },
              { key: "options_snapshot_rpm", label: "Tradier RPM limit", type: "number", value: config.options_snapshot.options_snapshot_rpm },
              { key: "options_snapshot_concurrency", label: "Concurrency", type: "number", value: config.options_snapshot.options_snapshot_concurrency },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Notifications */}
        {config && (
          <EditableMixedCard
            title="Notifications"
            subtitle="Email alerts and daily digest"
            fields={[
              { key: "notification_email_enabled", label: "Email notifications", type: "bool", value: config.notifications.notification_email_enabled },
              { key: "notification_email_to", label: "Email to", type: "string", value: config.notifications.notification_email_to },
              { key: "notification_pullback_alert_enabled", label: "Pullback alerts", type: "bool", value: config.notifications.notification_pullback_alert_enabled },
              { key: "daily_digest_enabled", label: "Daily digest", type: "bool", value: config.notifications.daily_digest_enabled },
              { key: "daily_digest_time", label: "Digest time (HH:MM)", type: "string", value: config.notifications.daily_digest_time },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* LLM Models */}
        {config && (
          <EditableMixedCard
            title="LLM Models"
            subtitle="Gemini model selection"
            fields={[
              { key: "gemini_model_fast", label: "Fast model", type: "string", value: config.llm.gemini_model_fast },
              { key: "gemini_model_deep", label: "Deep model", type: "string", value: config.llm.gemini_model_deep },
            ]}
            onSave={(updates) => updateConfig.mutate(updates)}
            isPending={updateConfig.isPending}
          />
        )}

        {/* Scan Persistence */}
        {config && (
          <EditableSettingsCard
            title="Scan Persistence"
            subtitle="How many past scans to retain"
            fields={[
              { key: "scan_retention_count", label: "Scans to keep", value: config.scan_persistence.scan_retention_count },
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
  subtitle,
  fields,
  onSave,
  isPending,
}: {
  title: string;
  subtitle?: string;
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
    <Card title={title} subtitle={subtitle}>
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

type MixedField = {
  key: string;
  label: string;
  type: "number" | "bool" | "string";
  value: number | boolean | string;
};

function EditableMixedCard({
  title,
  subtitle,
  fields,
  onSave,
  isPending,
}: {
  title: string;
  subtitle?: string;
  fields: MixedField[];
  onSave: (updates: Record<string, number | boolean | string>) => void;
  isPending: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<Record<string, string | boolean>>({});

  useEffect(() => {
    const init: Record<string, string | boolean> = {};
    for (const f of fields) {
      init[f.key] = f.type === "bool" ? (f.value as boolean) : String(f.value);
    }
    setValues(init);
  }, [fields.map((f) => String(f.value)).join(",")]);

  const handleSave = () => {
    const updates: Record<string, number | boolean | string> = {};
    for (const f of fields) {
      const current = values[f.key];
      if (f.type === "bool") {
        if (current !== f.value) updates[f.key] = current as boolean;
      } else if (f.type === "number") {
        const num = parseFloat(current as string);
        if (!isNaN(num) && num !== f.value) updates[f.key] = num;
      } else {
        if (current !== f.value) updates[f.key] = current as string;
      }
    }
    if (Object.keys(updates).length > 0) {
      onSave(updates);
    }
    setEditing(false);
  };

  return (
    <Card title={title} subtitle={subtitle}>
      <div className="space-y-2 text-sm">
        {fields.map((f) => (
          <div
            key={f.key}
            className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2"
          >
            <span className="text-gray-500">{f.label}</span>
            {editing ? (
              f.type === "bool" ? (
                <button
                  onClick={() =>
                    setValues((prev) => ({
                      ...prev,
                      [f.key]: !prev[f.key],
                    }))
                  }
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    values[f.key] ? "bg-emerald-500" : "bg-gray-300"
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${
                      values[f.key] ? "translate-x-6" : "translate-x-1"
                    }`}
                  />
                </button>
              ) : (
                <input
                  type="text"
                  value={(values[f.key] as string) ?? ""}
                  onChange={(e) =>
                    setValues((prev) => ({
                      ...prev,
                      [f.key]: e.target.value,
                    }))
                  }
                  className="w-36 rounded border border-gray-300 bg-white px-2 py-1 text-right font-mono text-sm text-gray-900 focus:border-blue-500 focus:outline-none"
                />
              )
            ) : f.type === "bool" ? (
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                  f.value
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-gray-100 text-gray-500"
                }`}
              >
                {f.value ? "ON" : "OFF"}
              </span>
            ) : (
              <span className="font-mono text-gray-900">
                {String(f.value)}
              </span>
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
