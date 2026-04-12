# config/ — Agent Context

All observability backend configuration files. Mounted read-only into their
respective containers via docker-compose volumes.

## Files

| File                  | Container       | Purpose |
|-----------------------|-----------------|---------|
| `otel-collector.yaml` | otel-collector  | OTLP fan-out: traces→Tempo, logs→Loki, metrics→Prometheus |
| `prometheus.yaml`     | prometheus      | Scrape config — polls collector :8889 |
| `loki.yaml`           | loki            | Log storage and retention |
| `tempo.yaml`          | tempo           | Trace storage and query API |
| `grafana/`            | grafana         | Datasource + dashboard provisioning |

## OTel collector pipeline (otel-collector.yaml)

```
receivers: [otlp gRPC :4317, HTTP :4318]
processors: [resource (add service.namespace=telescope-obs), batch (1s)]
exporters:
  prometheus  → :8889  (metrics)
  otlp_http/loki → http://loki:3100/otlp  (logs — Loki 2.9+ native OTLP)
  otlp_grpc/tempo → tempo:4317           (traces)
```

The collector is the single fan-out point. Pipeline services and the gateway send
everything to `:4317` (gRPC) or `:4318` (HTTP).

## Important: Grafana provisioning env var syntax

Grafana's YAML provisioning parser supports `${VAR}` for env var substitution but
does **NOT** support bash-style `${VAR:-default}`. Use `$$` to escape a literal `$`
and prevent provisioning-time expansion (e.g. `$${__value.raw}` → runtime template
`${__value.raw}`). Defaults for optional vars belong in docker-compose.yml where
Docker Compose's own `${VAR:-default}` expansion works correctly.
