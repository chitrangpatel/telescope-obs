# lib/python/telescope_obs/ — Agent Context

The Python client library. The only code telescope pipeline developers interact with directly.
Wraps gRPC stubs and OTel wiring so pipeline devs never handle trace IDs or proto messages.

## Files

| File          | Purpose |
|---------------|---------|
| `client.py`   | `TelemetryClient` + `TelemetryRun` — the public API |
| `otel.py`     | `setup_otel()` — initialises traces, metrics, logs; source-location factory |
| `metrics.py`  | `InfraMetrics` (CPU/RAM/disk), `PipelineMetrics` (counters/histograms) |
| `__init__.py` | Re-exports `TelemetryClient`, `InfraMetrics`, `PipelineMetrics` |

## TelemetryClient / TelemetryRun (client.py)

```python
setup_otel("chime-pipeline", "0.1.0", "otel-collector:4317")
client = TelemetryClient("gateway:50051", telescope="chime")

with client.start_run("chime.detection") as run:
    cid = run.submit_candidate(beam_id=42, stage="pipeline", payload={...})
    eid, sids = run.report_event(candidate_ids=[cid], payload={...})
    for sid in sids:
        run.report_downstream_result(eid, sid, {"action": "data_dump", ...})

# Later (new trace, span-linked to detection via gateway):
with client.start_run("chime.diagnostic_plot") as run:
    run.update_event(eid, patch={"diagnostic_plot_uri": "file:///..."})
```

`start_run()` creates a root OTel span. All `TelemetryRun` methods create child spans
under it — the entire pipeline run shares one `trace_id`. A second `start_run()` creates
a fresh trace; the gateway links it to prior traces for the same entity.

The W3C `traceparent` is injected into gRPC metadata automatically by
`opentelemetry-instrumentation-grpc` AND written explicitly into `ObservationContext`
as a fallback for the gateway's manual reconstruction path.

## setup_otel (otel.py)

Call once at service startup. Sets up three global providers:
- `TracerProvider` → OTLP gRPC → collector → Tempo
- `MeterProvider` (15s export) → OTLP gRPC → collector → Prometheus
- `LoggerProvider` → OTLP gRPC → collector → Loki

Also installs:
- `LoggingInstrumentor` (if available) — bridges stdlib logging → OTel logs; injects
  `trace_id`/`span_id` into stdout format.
- `_install_source_location_factory()` — uses `logging.setLogRecordFactory()` to append
  `src=<github-url>#L<lineno>` to every log message body. This is what creates
  Grafana's "View on GitHub" links. Uses `GITHUB_REPO` and `GIT_COMMIT_SHA` env vars.

**Critical**: use `setLogRecordFactory()`, NOT a filter on `logging.getLogger()`.
Python's `callHandlers()` walks the logger hierarchy calling handlers directly without
applying parent-logger filters, so root-logger filters are bypassed for child loggers.
A factory runs at record-creation time, before any logger or handler.

## Source location URL format

When `GITHUB_REPO` and `GIT_COMMIT_SHA` are set:
```
src=https://github.com/chitrangpatel/telescope-obs/blob/<sha>/telescopes/chime/pipeline.py#L128
```
The full URL is captured whole by the Loki derived field regex `src=(https://\S+)` and
used directly as the link href. This avoids Grafana URL-encoding `#` as `%23`, which
breaks GitHub's fragment-based line anchoring.

## Path normalisation (_normalize_path)

- `/app/telescopes/chime/pipeline.py` → `telescopes/chime/pipeline.py`
- `/app/lib/python/telescope_obs/client.py` → `lib/python/telescope_obs/client.py`
- `…/site-packages/telescope_obs/otel.py` → `lib/python/telescope_obs/otel.py`
- anything else → basename only (safe fallback)

## InfraMetrics / PipelineMetrics (metrics.py)

`InfraMetrics(telescope, data_path)`: CPU, RAM, disk gauges via psutil. Uses OTel
observable callbacks — no background thread; SDK polls them each export interval.

`PipelineMetrics(telescope)`: counters (`candidates_total`, `events_total`,
`actions_total`) and a histogram (`detection_duration_seconds`). All carry a
`telescope` label for per-telescope Grafana panel repeat.
