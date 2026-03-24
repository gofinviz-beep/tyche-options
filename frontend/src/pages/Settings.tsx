import { Card } from "@/components/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { useSystemConfig } from "@/hooks/useApi";

export function Settings() {
  const { data: config, isLoading } = useSystemConfig();

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-600 border-t-white" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Settings</h1>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* System status */}
        <Card title="System Status">
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
                  label={config?.preview_only ? "PREVIEW ONLY" : "LIVE TRADING"}
                  variant={config?.preview_only ? "info" : "success"}
                />
              }
            />
            <StatusRow
              label="Broker"
              badge={
                <StatusBadge
                  label={config?.broker_configured ? "Connected" : "Not configured"}
                  variant={config?.broker_configured ? "success" : "neutral"}
                />
              }
            />
            <StatusRow
              label="LLM"
              badge={
                <StatusBadge
                  label={config?.llm_configured ? "Gemini Active" : "Not configured"}
                  variant={config?.llm_configured ? "success" : "neutral"}
                />
              }
            />
            <StatusRow
              label="Earnings API"
              badge={
                <StatusBadge
                  label={
                    config?.earnings_api_configured ? "Connected" : "Not configured"
                  }
                  variant={config?.earnings_api_configured ? "success" : "neutral"}
                />
              }
            />
          </div>
        </Card>

        {/* Risk limits */}
        <Card title="Risk Limits">
          <div className="space-y-2 text-sm">
            {config?.risk_limits &&
              Object.entries(config.risk_limits).map(([key, value]) => (
                <div
                  key={key}
                  className="flex items-center justify-between rounded-lg bg-gray-800/50 px-3 py-2"
                >
                  <span className="text-gray-400">
                    {key.replace(/_/g, " ")}
                  </span>
                  <span className="font-mono text-white">{value}</span>
                </div>
              ))}
          </div>
        </Card>

        {/* Wheel params */}
        <Card title="Wheel Strategy Parameters">
          <div className="space-y-2 text-sm">
            {config?.wheel_params &&
              Object.entries(config.wheel_params).map(([key, value]) => (
                <div
                  key={key}
                  className="flex items-center justify-between rounded-lg bg-gray-800/50 px-3 py-2"
                >
                  <span className="text-gray-400">
                    {key.replace(/_/g, " ")}
                  </span>
                  <span className="font-mono text-white">{value}</span>
                </div>
              ))}
          </div>
        </Card>

        {/* Watchlist */}
        <Card title="Watchlist">
          {config?.watchlist.length ? (
            <div className="flex flex-wrap gap-2">
              {config.watchlist.map((symbol) => (
                <span
                  key={symbol}
                  className="rounded-full bg-gray-800 px-3 py-1 text-sm font-semibold text-white"
                >
                  {symbol}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-500">
              No symbols configured. Set <code className="text-gray-400">TYCHE_WATCHLIST_SYMBOLS</code> in
              your <code className="text-gray-400">.env</code> file.
            </p>
          )}
        </Card>
      </div>
    </div>
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
      <span className="text-sm text-gray-400">{label}</span>
      {badge}
    </div>
  );
}
