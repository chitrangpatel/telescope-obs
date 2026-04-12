# config/grafana/datasources/ — Agent Context

`datasources.yaml` is provisioned into Grafana automatically on startup.

## Datasources

| UID          | Type       | URL                  | Notes |
|--------------|------------|----------------------|-------|
| `prometheus` | Prometheus | http://prometheus:9090 | Default; exemplar trace links to Tempo |
| `loki`       | Loki       | http://loki:3100     | Derived fields (see below) |
| `timescaledb`| PostgreSQL | db:5432              | TimescaleDB; user/pass: telescope |
| `tempo`      | Tempo      | http://tempo:3200    | Trace→logs (Loki), service map (Prometheus), TraceQL |

## Loki derived fields

Three derived fields on every log line:

1. **TraceID** (`matcherType: label`, `matcherRegex: otelTraceID`) — for OTel OTLP logs
   where `trace_id` arrives as a structured Loki label. Links to the Pipeline Health
   dashboard via `${__value.raw}` in the URL template.

2. **TraceID_text** (`matcherType: regex`, `matcherRegex: trace_id=(\w+)`) — fallback
   for plain-text logs that embed `trace_id=<hex>` in the message body.

3. **SourceCode** (`matcherType: regex`, `matcherRegex: src=(https://\S+)`) — captures
   the full GitHub permalink emitted by the source-location factory in `otel.py`.
   URL is `$${__value.raw}` in the YAML (the `$$` escapes provisioning-time expansion;
   at runtime Grafana sees `${__value.raw}` and substitutes the captured URL verbatim).
   Renders as a **"View on GitHub"** link in the logs panel.

## Critical: the $$ escape

If you write `url: "${__value.raw}"` (single `$`), Grafana's provisioning engine treats
it as an env var lookup, gets an empty string, and the link disappears. You MUST write
`url: "$${__value.raw}"` so the provisioning engine outputs the literal string
`${__value.raw}` which Grafana then uses as a link template at runtime.

## After editing datasources.yaml

```bash
docker compose up -d --force-recreate grafana
# Verify the SourceCode URL is correct:
curl -s http://localhost:3000/api/datasources/name/Loki | python3 -c \
  "import sys,json; d=json.load(sys.stdin); [print(f['name'],'|',f.get('url','')) \
   for f in d['jsonData']['derivedFields']]"
```
