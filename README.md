# telescope-obs

Full-stack observability platform for fast radio burst (FRB) detection pipelines.
Correlates OpenTelemetry **traces**, **logs**, and **metrics** across three mock
radio telescopes (CHIME, SPT, HIRAX) and exposes them through a pair of Grafana
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

```bash
docker compose up -d
```

Open **http://localhost:3000** (anonymous admin, no password).

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

## GitHub source links in logs

Every log line carries a `src=<repo-relative-path>#L<lineno>` suffix injected
by `lib/python/telescope_obs/otel.py`.  Grafana's Loki derived field turns this
into a **"View on GitHub"** link in the logs panel.

Set these environment variables before starting (or restarting) Grafana:

```bash
export GITHUB_REPO=https://github.com/chitrangpatel/telescope-obs
export GIT_COMMIT_SHA=$(git rev-parse HEAD)
docker compose up -d --force-recreate grafana
```

---

## Project layout

```
proto/                   gRPC service + message definitions
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
  chime/pipeline.py      CHIME mock (400–800 MHz, FRB)
  spt/pipeline.py        SPT mock
  hirax/pipeline.py      HIRAX mock
config/
  otel-collector.yaml    OTLP → Tempo / Loki / Prometheus
  grafana/               Datasource + dashboard provisioning
```

---

## Regenerating protobuf stubs

```bash
make proto-go      # → gen/go/
make proto-python  # → gen/python/
```

Requires `protoc`, `protoc-gen-go`, `protoc-gen-go-grpc`, and `grpcio-tools`.
