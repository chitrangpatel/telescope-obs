# gateway/internal/grpc/ — Agent Context

`server.go` implements `telescopev1.TelemetryGatewayServer`.

## Span lifecycle for downstream actions

`ReportEvent` opens one **child span** per expected downstream action
(`data_dump`, `voevent_alert`, `db_write`) and parks each in `actionSpans sync.Map`
keyed by its hex span ID. The span IDs are returned to the caller in `action_span_ids`.

`ReportDownstreamResult` retrieves the parked span by `action_span_id`, stamps it
with the outcome from `payload["status"]` (`"success"` / `"failure"`), and ends it.
This closes that leg of the distributed trace in Tempo.

## Multi-trace span links

On `UpdateCandidate` / `UpdateEvent` the server:
1. Fetches all prior trace IDs from `candidate_traces` / `event_traces`.
2. Converts them to `trace.Link` values (each carrying `linked.operation` attribute).
3. Passes them to `tracer.Start()` via `trace.WithLinks(...)`.

This lets Tempo's "Related traces" panel navigate between the original detection trace
and later enrichment traces (diagnostic plots, re-analysis) for the same entity.

## Trace context reconstruction

`withRemoteParentFromIDs(ctx, traceHex, spanHex)` manually reconstructs a remote
`SpanContext` from the `trace_id` field in the proto message. It only applies if
the gRPC metadata hasn't already put a valid span in the context (the `otelgrpc`
middleware takes precedence).

## Key methods

| Method | What it does |
|--------|-------------|
| `SubmitCandidate` | Insert candidate, record creation trace |
| `ReportEvent` | Insert event, open action child spans, record creation trace |
| `ReportDownstreamResult` | Close action span, insert downstream result, record trace |
| `UpdateCandidate` | Fetch prior traces → span links, patch payload, record enrichment trace |
| `UpdateEvent` | Same as above for events |
| `GetCandidate / GetEvent / ListCandidates / ListEvents` | Read-only query path |
