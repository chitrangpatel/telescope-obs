# config/grafana/ — Agent Context

Grafana provisioning configuration. Mounted read-only into the Grafana container at
`/etc/grafana/provisioning/`.

## Structure

```
datasources/
  datasources.yaml   Prometheus, Loki (+ derived fields), TimescaleDB, Tempo
dashboards/
  dashboards.yaml    Tells Grafana to load *.json from this directory
  pipeline_health.json   Real-time pipeline throughput and health
  event_inspector.json   Per-event deep-dive (traces, logs, candidates, actions)
```

## Grafana container env vars (set in docker-compose.yml)

| Var                          | Purpose |
|------------------------------|---------|
| `GF_AUTH_ANONYMOUS_ENABLED`  | `"true"` — no login needed |
| `GF_AUTH_ANONYMOUS_ORG_ROLE` | `Admin` — anonymous users have full access |
| `GF_FEATURE_TOGGLES_ENABLE`  | `traceqlEditor` — enables TraceQL search in Tempo |
| `GITHUB_REPO`                | Used by Loki derived field source-link; pass at `docker compose up` time |
| `GIT_COMMIT_SHA`             | Pin GitHub links to a specific commit |

## Reloading after changes

Datasources and dashboards are only re-read at container start. After editing:
```bash
# Rebuild with updated env vars (e.g. new GIT_COMMIT_SHA):
GITHUB_REPO=https://github.com/chitrangpatel/telescope-obs \
GIT_COMMIT_SHA=$(git rev-parse HEAD) \
docker compose up -d --force-recreate grafana
```

Dashboards can also be edited live in the Grafana UI and exported as JSON.
