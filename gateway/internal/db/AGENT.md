# gateway/internal/db/ — Agent Context

`store.go` is the gateway's persistence layer backed by TimescaleDB (PostgreSQL + hypertables).
Uses `pgx/v5` with a connection pool (`pgxpool`).

## Schema overview

| Table                | Key columns                                   | Notes |
|----------------------|-----------------------------------------------|-------|
| `candidates`         | candidate_id, telescope, trace_id, payload JSONB | Hypertable on observation_time (7-day chunks) |
| `events`             | event_id, trace_id, candidate_ids[], payload JSONB | Hypertable on event_time |
| `provenance_edges`   | source_candidate_id, target_event_id, weight | DAG: N candidates → 1 event |
| `downstream_results` | event_id, trace_id, action_span_id, payload JSONB | One row per async action outcome |
| `event_traces`       | event_id, trace_id, operation                | All traces ever touching an event |
| `candidate_traces`   | candidate_id, trace_id, operation            | All traces ever touching a candidate |

## Design patterns

- **JSONB payloads**: all telescope-specific science fields are stored as JSONB and
  never interpreted by the gateway. Enrichment merges new fields via `payload || $patch`.
- **`PatchCandidate` / `PatchEvent`**: shallow-merge using `JSONB ||` operator —
  existing keys are overwritten, absent keys are untouched.
- **`InsertEvent`**: transactional — inserts the event row and all `provenance_edges`
  atomically, rolling back on any failure.
- **`ON CONFLICT DO NOTHING`**: all inserts are idempotent; duplicate submissions
  from retrying pipelines are silently dropped.
- **`TraceAssoc`**: the struct returned by `GetEventTraces` / `GetCandidateTraces`;
  carries `TraceID` and `Operation` for building OTel span links in the gRPC layer.

## MJD ↔ time conversion

`mjdToTime(mjd float64) time.Time` converts Modified Julian Date to UTC time using
`mjdUnixEpoch = 40587.0` (1970-01-01). Used for `ListCandidates` time-range filters.
