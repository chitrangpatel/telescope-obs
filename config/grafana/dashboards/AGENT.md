# config/grafana/dashboards/ — Agent Context

Grafana dashboard JSON files provisioned automatically at startup.
`dashboards.yaml` points Grafana at this directory.

## Dashboards

### Pipeline Health (`pipeline_health.json`)
UID: `telescope-pipeline-health`

Real-time throughput and health for all three telescope pipelines:
- Candidate and event rates (Prometheus)
- Detection duration histogram
- Action success/failure rates by telescope
- Error log table (Loki)
- Click any `trace_id` cell → updates the span tree + correlated logs panels inline
  using a `var-trace_id` dashboard variable.

### Event Inspector (`event_inspector.json`)
UID: `telescope-event-inspector`

Deep-dive into a single event by UUID:
- Event summary: classification, DM, SNR, candidate count (TimescaleDB)
- Contributing candidates table
- All traces ever associated with the event (TimescaleDB `event_traces`)
- Downstream action results with pass/fail colour coding
- Span tree (Tempo) + logs (Loki) for the currently selected trace

**Variables:**
| Variable           | Type    | Hidden | Purpose |
|--------------------|---------|--------|---------|
| `event_id`         | textbox | no     | User pastes the UUID to inspect |
| `selected_trace_id`| textbox | yes (2)| Set by clicking a trace_id link; empty = use creation trace |
| `active_trace_id`  | query   | yes (2)| `COALESCE(NULLIF($selected_trace_id,''), trace_id) FROM events WHERE event_id=$event_id` — the trace currently shown in span/log panels |

**Trace ID click pattern**: all `trace_id` cells link to
`/d/telescope-event-inspector?var-event_id=${event_id}&var-selected_trace_id=${__value.raw}`,
which reloads the same page with the new trace selected — no navigation away.

## Adding a new dashboard

1. Create the JSON file in this directory with a unique `uid` field.
2. The provisioning loader picks it up on Grafana restart (`docker compose restart grafana`).
3. Use `"datasource": {"type": "postgres", "uid": "timescaledb"}` for TimescaleDB panels,
   `{"type": "tempo", "uid": "tempo"}` for trace panels, `{"type": "loki", "uid": "loki"}` for logs.
