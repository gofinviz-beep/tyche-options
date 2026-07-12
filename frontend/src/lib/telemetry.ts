/**
 * Frontend telemetry — batches error and timing events, flushes
 * them to the backend ``POST /api/v1/telemetry/events`` endpoint.
 *
 * Usage:
 *   import { telemetry } from "@/lib/telemetry";
 *   telemetry.reportError("/scanner/scan", 500, "Internal error", 1234);
 *   telemetry.reportTiming("/scanner/latest", 87, 200);
 *   telemetry.reportCrash("Uncaught TypeError: ...", { componentStack: "..." });
 */

interface TelemetryEvent {
  type: "error" | "timing" | "crash";
  path: string;
  status?: number;
  message: string;
  duration_ms?: number;
  timestamp: string;
  extra: Record<string, unknown>;
}

const MAX_BUFFER = 50;
const FLUSH_INTERVAL_MS = 10_000;
const SLOW_THRESHOLD_MS = 5_000;
const TELEMETRY_URL = "/api/v1/telemetry/events";

const buffer: TelemetryEvent[] = [];
let flushTimer: ReturnType<typeof setInterval> | null = null;

function enqueue(event: TelemetryEvent): void {
  if (buffer.length >= MAX_BUFFER) {
    buffer.shift();
  }
  buffer.push(event);
}

async function flush(): Promise<void> {
  if (buffer.length === 0) return;

  const events = buffer.splice(0);
  try {
    await fetch(TELEMETRY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events }),
      keepalive: true,
    });
  } catch {
    // If flush fails, re-add events (up to limit) so they retry next cycle
    buffer.unshift(...events.slice(0, MAX_BUFFER - buffer.length));
  }
}

function startAutoFlush(): void {
  if (flushTimer) return;
  flushTimer = setInterval(flush, FLUSH_INTERVAL_MS);

  if (typeof window !== "undefined") {
    window.addEventListener("beforeunload", () => {
      flush();
    });
    window.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flush();
    });
  }
}

export const telemetry = {
  reportError(
    path: string,
    status: number,
    message: string,
    durationMs?: number,
    extra: Record<string, unknown> = {},
  ): void {
    enqueue({
      type: "error",
      path,
      status,
      message,
      duration_ms: durationMs,
      timestamp: new Date().toISOString(),
      extra,
    });
  },

  reportTiming(
    path: string,
    durationMs: number,
    status: number,
  ): void {
    if (durationMs >= SLOW_THRESHOLD_MS) {
      enqueue({
        type: "timing",
        path,
        status,
        message: "slow_request",
        duration_ms: durationMs,
        timestamp: new Date().toISOString(),
        extra: {},
      });
    }
  },

  reportCrash(
    message: string,
    extra: Record<string, unknown> = {},
  ): void {
    enqueue({
      type: "crash",
      path: "",
      message,
      timestamp: new Date().toISOString(),
      extra,
    });
    flush();
  },

  flush,
  start: startAutoFlush,
};

// Auto-start when module is imported
startAutoFlush();
