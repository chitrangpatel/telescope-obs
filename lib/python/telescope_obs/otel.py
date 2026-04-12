"""OpenTelemetry initialisation for telescope pipeline services."""

import logging
import re

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# Bridges Python's stdlib logging into OTel logs.
# Import is conditional so the library degrades gracefully if not installed.
try:
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    _has_logging_instrumentor = True
except ImportError:
    _has_logging_instrumentor = False


def setup_otel(service_name: str, service_version: str, otlp_endpoint: str) -> None:
    """
    Initialise the global TracerProvider, MeterProvider, and LoggerProvider,
    all exporting to the OTel collector via OTLP gRPC.  Call once at service
    startup before creating a TelemetryClient or any Metrics objects.

    Traces  → collector → Tempo
    Metrics → collector → Prometheus (scraped from :8889)
    Logs    → collector → Loki
               Every stdlib log record emitted while a span is active
               carries trace_id + span_id automatically, enabling
               Grafana's "Logs for this trace" button in Tempo.
    """
    resource = Resource.create({
        ResourceAttributes.SERVICE_NAME:    service_name,
        ResourceAttributes.SERVICE_VERSION: service_version,
    })

    # ── Traces ────────────────────────────────────────────────────
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
    )
    trace.set_tracer_provider(trace_provider)

    # ── Metrics ───────────────────────────────────────────────────
    # Export every 15 s; the collector fans out to the Prometheus scrape
    # endpoint at :8889, labelled with telescope / service attributes.
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True),
                export_interval_millis=15_000,
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)

    # ── Logs ──────────────────────────────────────────────────────
    # The LoggerProvider ships log records to the collector which
    # forwards them to Loki.  The OTel SDK automatically attaches
    # trace_id and span_id to every record emitted while a span is
    # active, so Loki stores them as indexed labels enabling
    # Tempo → Loki drill-down and vice versa.
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=otlp_endpoint, insecure=True))
    )
    set_logger_provider(log_provider)

    # LoggingInstrumentor does two things:
    #   1. Adds a LoggingHandler to the root logger wired to the global
    #      LoggerProvider (set above), bridging stdlib logging → OTel → Loki.
    #   2. Injects trace_id/span_id into the log format so they appear in
    #      stdout as well (useful for local dev without Loki).
    # Do NOT also add a manual LoggingHandler — that would duplicate every
    # log record in Loki.
    if _has_logging_instrumentor:
        LoggingInstrumentor().instrument(set_logging_format=True)
    else:
        # Fallback: manually bridge stdlib → OTel LoggerProvider.
        from opentelemetry.sdk._logs import LoggingHandler
        logging.getLogger().addHandler(
            LoggingHandler(level=logging.NOTSET, logger_provider=log_provider)
        )
        _install_trace_context_filter()

    # W3C traceparent propagation — injected into gRPC metadata by the
    # instrumentation layer, and explicitly into ObservationContext.
    set_global_textmap(TraceContextTextMapPropagator())

    # Install source-location factory last so it runs at record-creation time,
    # before any logger filter or handler processes the record.
    # Adding a filter to the root logger does NOT work for child loggers
    # (e.g. logging.getLogger("chime.pipeline")) because Python's
    # callHandlers walks up the hierarchy calling handlers directly, bypassing
    # parent-logger filters.  A log-record factory runs before everything.
    _install_source_location_factory()


# ── Source-location log record factory ───────────────────────────────────────
#
# WHY a factory, not a filter on the root logger:
#   Python's Logger.callHandlers() walks the logger hierarchy and calls each
#   handler directly — it does NOT call parent-logger filters.  A filter on
#   logging.getLogger() only fires for records emitted directly to the root
#   logger, not for child loggers (e.g. logging.getLogger("chime.pipeline"))
#   that propagate upward.  A log-record factory runs at record-creation time,
#   before any logger filter or handler, so it intercepts everything.

_SITE_PKGS_RE = re.compile(r".*[/\\]site-packages[/\\]")
_APP_ROOT = "/app/"


def _normalize_path(pathname: str, filename: str) -> str:
    """Return a repo-relative path suitable for a GitHub blob URL.

    Normalisation rules
    ───────────────────
    * ``/app/telescopes/chime/pipeline.py``        →  ``telescopes/chime/pipeline.py``
    * ``/app/lib/python/telescope_obs/client.py``  →  ``lib/python/telescope_obs/client.py``
    * pip site-packages ``…/telescope_obs/x.py``   →  ``lib/python/telescope_obs/x.py``
    * anything else                                →  basename only (safe fallback)
    """
    if pathname.startswith(_APP_ROOT):
        return pathname[len(_APP_ROOT):]
    m = _SITE_PKGS_RE.search(pathname)
    if m:
        return "lib/python/" + pathname[m.end():]
    return filename  # basename fallback


def _install_source_location_factory() -> None:
    """Chain a log-record factory that appends ``src=<path>#L<lineno>`` to
    every log message body so the code location is visible in Loki and the
    Grafana derived field can extract it to build a GitHub permalink."""
    _prev = logging.getLogRecordFactory()

    def _factory(name, level, fn, lno, msg, args, exc_info,
                 func=None, sinfo=None):
        record = _prev(name, level, fn, lno, msg, args, exc_info, func, sinfo)
        rel = _normalize_path(record.pathname, record.filename)
        # Pre-resolve %-style args now; set args=None so no handler double-formats.
        try:
            body = record.getMessage()
        except Exception:
            body = str(record.msg)
        record.msg = f"{body}  src={rel}#L{record.lineno}"
        record.args = None
        return record

    logging.setLogRecordFactory(_factory)


# ── Fallback trace-context log filter ────────────────────────────────────────

class _TraceContextFilter(logging.Filter):
    """Injects otelTraceID and otelSpanID into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        sc = trace.get_current_span().get_span_context()
        if sc.is_valid:
            record.otelTraceID = format(sc.trace_id, "032x")
            record.otelSpanID  = format(sc.span_id,  "016x")
        else:
            record.otelTraceID = ""
            record.otelSpanID  = ""
        return True


def _install_trace_context_filter() -> None:
    root = logging.getLogger()
    root.addFilter(_TraceContextFilter())
