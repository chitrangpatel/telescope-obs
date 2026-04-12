# gateway/migrations/ — Agent Context

SQL files applied automatically on first TimescaleDB container start via
`/docker-entrypoint-initdb.d`. Files run in lexicographic order.

## 001_init.sql

Creates the core science tables:
- `candidates` — beam candidates; hypertable on `observation_time` (7-day chunks)
- `events` — astrophysical events; hypertable on `event_time` (7-day chunks)
- `provenance_edges` — DAG: which candidates contributed to which event
- `downstream_results` — async action outcomes (data_dump, voevent_alert, db_write)

All payload fields are JSONB. Indexes cover: telescope+time (list queries),
trace_id (correlation), GIN on payload (JSON path queries).

## 002_traces.sql

Creates multi-trace association tables:
- `event_traces (event_id, trace_id, operation, recorded_at)` — every trace that has
  ever touched an event; the gateway reads this to build OTel span links.
- `candidate_traces (candidate_id, trace_id, operation, recorded_at)` — same for candidates.

`operation` is a free-form label, e.g. `"creation"`, `"diagnostic_plot"`, `"re_analysis"`.

## Applying to a running DB

If the DB container is already running and you need to add a new migration, exec into
the container and run the SQL manually:
```bash
docker compose exec db psql -U telescope -d telescope_obs -f /path/to/new_migration.sql
```
Or drop and recreate the volume to re-run all migrations from scratch:
```bash
docker compose down -v && docker compose up -d
```
