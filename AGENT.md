# telescope-obs — Agent Context

Full-stack observability platform for fast radio burst (FRB) detection pipelines.
Three mock radio telescopes (CHIME, SPT, HIRAX) emit OpenTelemetry traces, logs, and
metrics that are correlated and surfaced through Grafana dashboards.

## Architecture at a glance

```
Python pipelines (CHIME · SPT · HIRAX)
  │  gRPC + W3C traceparent (OTLP instrumented)
  ▼
gateway/          Go gRPC service — opens/closes OTel spans, writes TimescaleDB
  │  SQL
  ▼
TimescaleDB       PostgreSQL + hypertables: candidates, events, event_traces,
                  downstream_results, provenance_edges
  │  OTLP gRPC
  ▼
otel-collector    Fan-out: traces → Tempo, logs → Loki, metrics → Prometheus
  │
Grafana :3000     Two dashboards: Pipeline Health, Event Inspector
```

## Services (docker-compose)

| Service         | Port  | Image / Build                  |
|-----------------|-------|--------------------------------|
| db              | 5432  | timescale/timescaledb:pg16     |
| gateway         | 50051 | gateway/Dockerfile             |
| chime/spt/hirax | —     | telescopes/Dockerfile (PIPELINE arg) |
| otel-collector  | 4317/4318/8889 | otel/opentelemetry-collector-contrib |
| prometheus      | 9090  | prom/prometheus                |
| loki            | 3100  | grafana/loki                   |
| tempo           | 3200  | grafana/tempo:2.6.0            |
| grafana         | 3000  | grafana/grafana                |

## Key design decisions

- **Telescope-agnostic schema**: all science fields live in `google.protobuf.Struct` payloads.
  The gateway and client library never inspect them. Only structural fields
  (trace_id, candidate_id, event_id, observation_time) are typed.
- **Multi-trace association**: events and candidates accumulate trace IDs over their
  lifetime (creation → async actions → diagnostic plots → re-analysis). The `event_traces`
  and `candidate_traces` tables record every trace; the gateway injects OTel span links
  so Tempo can navigate across traces for the same domain object.
- **Source code links in logs**: `lib/python/telescope_obs/otel.py` installs a
  `logging.setLogRecordFactory()` that appends `src=<github-url>#L<line>` to every log body.
  Grafana Loki's derived field captures the full URL and renders a "View on GitHub" link.
- **Grafana provisioning env vars**: uses `${VAR}` syntax. Bash-style `${VAR:-default}` is NOT
  supported. Use `$$` to escape a literal `$` in YAML so provisioning doesn't expand it
  (e.g. `$${__value.raw}` in datasources.yaml → runtime `${__value.raw}`).

## Environment variables (set before `docker compose up`)

```bash
export GITHUB_REPO=https://github.com/chitrangpatel/telescope-obs
export GIT_COMMIT_SHA=$(git rev-parse HEAD)
docker compose up -d --force-recreate grafana   # pick up new env
```

## Quick start

```bash
docker compose up -d
open http://localhost:3000
```

## Regenerating proto stubs

```bash
make proto-go      # → gen/go/
make proto-python  # → gen/python/
```

## Directory map

```
proto/                   gRPC service + message definitions
gateway/
  cmd/gateway/           Go service entrypoint
  internal/db/           TimescaleDB persistence (pgx/v5)
  internal/grpc/         gRPC server — span lifecycle, multi-trace logic
  internal/otel/         OTel TracerProvider init (Go)
  migrations/            SQL schema applied on first DB container start
lib/python/telescope_obs/
  client.py              TelemetryClient / TelemetryRun — the only API surface telescope devs use
  otel.py                setup_otel(), source-location log factory
  metrics.py             InfraMetrics (CPU/RAM/disk), PipelineMetrics (counters/histograms)
telescopes/
  Dockerfile             Shared image; PIPELINE build-arg selects the pipeline
  chime/pipeline.py      CHIME mock (400–800 MHz, FRB)
  spt/pipeline.py        SPT mock
  hirax/pipeline.py      HIRAX mock
config/
  otel-collector.yaml    OTLP → Tempo / Loki / Prometheus fan-out
  prometheus.yaml        Scrape config (collector :8889)
  loki.yaml              Loki storage config
  tempo.yaml             Tempo storage + query config
  grafana/
    datasources/         Prometheus, Loki, TimescaleDB, Tempo (provisioned)
    dashboards/          Pipeline Health, Event Inspector (provisioned JSON)
```
