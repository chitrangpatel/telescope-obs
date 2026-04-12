# telescope-obs

Full-stack observability platform for event tracing pipelines.
Correlates OpenTelemetry **traces**, **logs**, and **metrics** across three mock
telescope pipelines (CHIME, SPT, HIRAX) and exposes them through a pair of Grafana
dashboards backed by Tempo, Loki, Prometheus, and TimescaleDB.

---

## Architecture

```
┌─ Telescope Pipelines (Python) ──────────────────────┐
│  CHIME · SPT · HIRAX                                 │
│  • emit BeamCandidates → cluster → AstrophysicalEvent│
│  • report downstream actions (data_dump, voevent, …) │
│  • OTel SDK: traces + metrics + logs w/ src=file#Lno │
└──────────────────────┬───────────────────────────────┘
                       │ gRPC + W3C traceparent
                       ▼
         ┌─────────────────────────────┐
         │  TelemetryGateway  (Go)      │
         │  • opens / closes OTel spans │
         │  • writes to TimescaleDB     │
         │  • records event_traces      │
         └────────────┬────────────────┘
              OTLP gRPC│              SQL
              ┌────────┘        ┌────────────────────┐
              ▼                 │  TimescaleDB        │
   ┌──────────────────┐         │  candidates         │
   │  OTel Collector  │         │  events             │
   │  traces ─► Tempo │         │  event_traces       │
   │  logs   ─► Loki  │         │  downstream_results │
   │  metrics► Prom   │         └────────────────────┘
   └──────────────────┘
        │      │      │
     Tempo   Loki   Prometheus
        └──────┴──────┘
               │
           Grafana :3000
```

---

## Quick start

The gateway and telescope pipeline images are built locally, and both require
the generated protobuf stubs (`gen/`) to be present before the Docker build.
`gen/` is gitignored, so on a fresh clone you must generate it first.

### 1. Prerequisites

- Docker + Docker Compose
- Go 1.22+
- Python 3.12+ with `venv`
- `protoc` (Protocol Buffers compiler)

```bash
# macOS
brew install protobuf

# Go proto plugins
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

# Python proto tools (into the project venv)
python3 -m venv .venv
.venv/bin/pip install grpcio-tools
```

### 2. Generate protobuf stubs

```bash
make proto          # generates gen/go/ and gen/python/
```

### 3. Build images and start all services

```bash
docker compose up --build -d
```

This builds the `gateway` and `chime`/`spt`/`hirax` images locally (which copy
`gen/` in) and pulls the rest from Docker Hub. All services start in dependency
order; the DB runs migrations automatically on first start.

### 4. Open Grafana

```
http://localhost:3000
```

Anonymous admin access — no login required.

---

## GitHub source links in logs (optional)

Every log line carries a `src=<url>#L<lineno>` suffix injected by
`lib/python/telescope_obs/otel.py`. When `GITHUB_REPO` and `GIT_COMMIT_SHA` are
set, Grafana's Loki derived field renders a **"View on GitHub"** link pointing to
the exact line.

```bash
export GITHUB_REPO=https://github.com/chitrangpatel/telescope-obs
export GIT_COMMIT_SHA=$(git rev-parse HEAD)

# Restart only Grafana and the pipeline containers to pick up the new env:
docker compose up --build -d --force-recreate grafana chime spt hirax
```

---

## Services

| Service | Port | Purpose |
|---------|------|---------|
| Grafana | 3000 | Dashboards |
| TimescaleDB | 5432 | Candidates / events / traces |
| Gateway (gRPC) | 50051 | Telemetry ingestion |
| Tempo | 3200 | Distributed traces |
| Loki | 3100 | Structured logs |
| Prometheus | 9090 | Metrics |
| OTel Collector | 4317 / 4318 | OTLP receiver |

---

## Dashboards

### Pipeline Health
Real-time throughput, latency, action success rates, and error tables.
Click any `trace_id` cell to load the full span tree + correlated logs inline.

### Event Inspector
Search for any `event_id` (UUID) to see:
- Event summary (classification, DM, SNR, candidate count)
- Every beam candidate that contributed to the clustering
- All traces ever associated with the event (creation, async actions, re-analysis)
- Downstream action results with colour-coded pass/fail status
- Span tree + logs for whichever trace you click — updates inline without leaving the page

---

## Project layout

```
proto/                   gRPC service + message definitions
gen/                     Generated stubs — gitignored, produced by make proto
  go/telescope/v1/       Go stubs (events.pb.go, events_grpc.pb.go)
  python/                Python stubs (events_pb2.py, events_pb2_grpc.py)
gateway/
  cmd/gateway/           Go service entrypoint
  internal/db/           TimescaleDB persistence layer
  internal/grpc/         gRPC server (span management, event recording)
  migrations/            SQL schema (hypertables, event_traces)
lib/python/telescope_obs/
  client.py              TelemetryClient / TelemetryRun API
  otel.py                OTel setup + source-location log factory
  metrics.py             InfraMetrics + PipelineMetrics
telescopes/
  Dockerfile             Shared image; PIPELINE build-arg selects the pipeline
  chime/pipeline.py      CHIME mock
  spt/pipeline.py        SPT mock
  hirax/pipeline.py      HIRAX mock
config/
  otel-collector.yaml    OTLP → Tempo / Loki / Prometheus
  grafana/               Datasource + dashboard provisioning
```

---

## Useful commands

```bash
make up           # docker compose up --build -d
make down         # docker compose down
make logs         # docker compose logs -f
make proto        # regenerate gen/ from proto/events.proto
make proto-clean  # remove gen/
```
