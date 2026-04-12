# gateway/ — Agent Context

Go gRPC service. The single ingestion point for all telescope pipeline data.

## Responsibilities

1. **Span lifecycle management** — opens child spans for each expected downstream
   action during `ReportEvent`, parks them in a `sync.Map` (keyed by span ID),
   and ends them with the correct status when `ReportDownstreamResult` arrives.
2. **Multi-trace association** — every time an entity (event or candidate) is touched
   by a new pipeline run, the gateway records `(entity_id, trace_id, operation)` in
   `event_traces` / `candidate_traces`. On subsequent operations it fetches all prior
   trace IDs and injects OTel **span links** into the new span so Tempo can navigate
   across traces for the same domain object.
3. **TimescaleDB persistence** — candidates, events, provenance edges, downstream
   results, and trace associations are written via pgx/v5.

## Structure

```
cmd/gateway/main.go     Entrypoint: wires OTel, DB, gRPC server; handles SIGINT/SIGTERM
internal/
  otel/setup.go         TracerProvider init (OTLP/gRPC to collector)
  grpc/server.go        TelemetryGatewayServer implementation
  db/store.go           All SQL queries (pgxpool)
migrations/
  001_init.sql          candidates, events, provenance_edges, downstream_results
  002_traces.sql        event_traces, candidate_traces
Dockerfile              Multi-stage build; copies gen/ and gateway/
```

## Config (env vars)

| Var              | Default                                          |
|------------------|--------------------------------------------------|
| `GATEWAY_ADDR`   | `:50051`                                         |
| `DB_URL`         | `postgres://telescope:telescope@db:5432/telescope_obs` |
| `OTEL_ENDPOINT`  | `otel-collector:4317`                            |

## OTel instrumentation

- `otelgrpc.NewServerHandler()` is installed as a gRPC stats handler — automatically
  extracts W3C `traceparent` from incoming metadata so every RPC span is a child of
  the calling service's span.
- The gateway also manually reconstructs span context from `ObservationContext.trace_id`
  via `withRemoteParentFromIDs()` as a belt-and-suspenders fallback.
