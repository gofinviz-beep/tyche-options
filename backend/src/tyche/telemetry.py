"""OpenTelemetry configuration — metrics, traces, and GCP export.

Call ``configure_telemetry()`` once at application startup *before*
``configure_logging()``.  When ``gcp_project_id`` is provided the SDK
ships metrics to Cloud Monitoring and traces to Cloud Trace via their
native exporters; otherwise console/noop exporters are used for local
development.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter, Histogram, Meter
    from opentelemetry.trace import Tracer

_CONFIGURED = False

# ---------------------------------------------------------------------------
# Public accessors (safe to call before configure; return noop instances)
# ---------------------------------------------------------------------------


def get_meter(name: str = "tyche") -> Meter:
    return metrics.get_meter(name)


def get_tracer(name: str = "tyche") -> Tracer:
    return trace.get_tracer(name)


# ---------------------------------------------------------------------------
# Pre-created instruments — importable from anywhere in the backend
# ---------------------------------------------------------------------------

_meter = metrics.get_meter("tyche")

http_request_duration: Histogram = _meter.create_histogram(
    name="http.server.request.duration",
    description="HTTP request duration in seconds",
    unit="s",
)

scanner_stage_duration: Histogram = _meter.create_histogram(
    name="scanner.stage.duration",
    description="Duration of individual scanner pipeline stages",
    unit="s",
)

scanner_total_duration: Histogram = _meter.create_histogram(
    name="scanner.total.duration",
    description="Total wall-clock duration of a full scan",
    unit="s",
)

scanner_errors: Counter = _meter.create_counter(
    name="scanner.errors",
    description="Count of scanner pipeline errors",
)

api_errors: Counter = _meter.create_counter(
    name="api.errors",
    description="Count of API errors (backend + frontend-reported)",
)

llm_call_duration: Histogram = _meter.create_histogram(
    name="llm.call.duration",
    description="Duration of individual LLM calls",
    unit="s",
)

broker_call_duration: Histogram = _meter.create_histogram(
    name="broker.call.duration",
    description="Duration of broker API calls",
    unit="s",
)

csp_scan_candidates_found: Histogram = _meter.create_histogram(
    name="scanner.csp.candidates_found",
    description="Number of CSP candidates surviving the full pipeline",
)

csp_scan_drops: Counter = _meter.create_counter(
    name="scanner.csp.drops",
    description="Count of CSP candidates dropped by reason",
)


# ---------------------------------------------------------------------------
# One-time configuration
# ---------------------------------------------------------------------------


def configure_telemetry(
    *,
    service_name: str = "tyche-options",
    gcp_project_id: str = "",
    enabled: bool = True,
) -> None:
    """Initialise OTel TracerProvider and MeterProvider.

    Args:
        service_name: OTel resource ``service.name`` attribute.
        gcp_project_id: When non-empty, use GCP Cloud Trace / Monitoring
            exporters.  Otherwise fall back to console exporters.
        enabled: If *False*, skip configuration entirely (noop providers).
    """
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED:
        return
    _CONFIGURED = True

    if not enabled:
        logging.getLogger(__name__).info("OpenTelemetry disabled by config")
        return

    resource = Resource.create({"service.name": service_name})

    # --- Traces ---
    if gcp_project_id:
        try:
            from opentelemetry.exporter.cloud_trace import (
                CloudTraceSpanExporter,
            )

            span_exporter = CloudTraceSpanExporter(project_id=gcp_project_id)
        except Exception:
            logging.getLogger(__name__).warning(
                "GCP trace exporter unavailable, falling back to console",
                exc_info=True,
            )
            span_exporter = ConsoleSpanExporter()
    else:
        span_exporter = ConsoleSpanExporter()

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    # --- Metrics ---
    if gcp_project_id:
        try:
            from opentelemetry.exporter.cloud_monitoring import (
                CloudMonitoringMetricsExporter,
            )

            metric_exporter = CloudMonitoringMetricsExporter(
                project_id=gcp_project_id,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "GCP metric exporter unavailable, falling back to console",
                exc_info=True,
            )
            metric_exporter = ConsoleMetricExporter()
    else:
        metric_exporter = ConsoleMetricExporter()

    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=60_000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    logging.getLogger(__name__).info(
        "OpenTelemetry configured service=%s gcp=%s",
        service_name,
        bool(gcp_project_id),
    )


def shutdown_telemetry() -> None:
    """Flush and shut down providers — call during app shutdown."""
    tp = trace.get_tracer_provider()
    if hasattr(tp, "shutdown"):
        tp.shutdown()

    mp = metrics.get_meter_provider()
    if hasattr(mp, "shutdown"):
        mp.shutdown()
