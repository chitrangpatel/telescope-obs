# telescopes/ — Agent Context

Mock telescope pipeline services. Each telescope has its own `pipeline.py` but shares
a single `Dockerfile` that selects the right pipeline via the `PIPELINE` build arg.

## Dockerfile

Build arg `PIPELINE` (e.g. `chime`, `spt`, `hirax`) controls:
1. Which `pipeline.py` is copied into the image:
   `COPY telescopes/${PIPELINE}/pipeline.py telescopes/${PIPELINE}/pipeline.py`
   Preserves the repo-relative path inside the container at `/app/telescopes/<name>/pipeline.py`.
   This is important for the source-location log factory in `otel.py` which strips the
   `/app/` prefix to get the GitHub-linkable path.
2. The runtime command:
   `CMD ["sh", "-c", "python -u telescopes/${PIPELINE}/pipeline.py"]`
   `ENV PIPELINE=${PIPELINE}` persists the build arg to runtime so `${PIPELINE}` resolves.

The build context is the repo root so the Dockerfile can copy `lib/python/`,
`gen/python/`, and `telescopes/` in one build.

## Shared environment variables

All three pipeline containers receive:

| Var             | Purpose |
|-----------------|---------|
| `GATEWAY_ADDR`  | gRPC endpoint, e.g. `gateway:50051` |
| `OTEL_ENDPOINT` | OTel collector, e.g. `otel-collector:4317` |
| `SCAN_INTERVAL` | Seconds between detection cycles |
| `GITHUB_REPO`   | GitHub repo URL for source links in logs (optional) |
| `GIT_COMMIT_SHA`| Git SHA for source links in logs (optional) |

## Pipelines

Each pipeline follows the same structure:
1. `setup_otel(...)` — initialise OTel (traces + metrics + logs + source-location factory)
2. `InfraMetrics` + `PipelineMetrics` — infrastructure and science metrics
3. `TelemetryClient` — connect to gateway
4. Loop:
   - `run_detection()` — one detection cycle (candidates → event → actions) as one trace
   - brief sleep to simulate data collection
   - `run_diagnostic_plot()` — separate trace, span-linked to the detection trace

See individual subdirectory AGENT.md files for telescope-specific parameters.
