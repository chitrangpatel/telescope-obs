# proto/ — Agent Context

Contains the single protobuf schema for the entire system: `events.proto`.

## Design philosophy

The schema is intentionally **telescope-agnostic**. It enforces only:
1. W3C trace context (`trace_id` / `span_id`) on every ingest message so the gateway
   can attach OTel spans without inspecting payloads.
2. DAG provenance: N `BeamCandidate`s → 1 `AstrophysicalEvent` via `ProvenanceEdge`.
3. Downstream action span-binding (`action_span_id`) so the gateway can close the
   correct child span based on `payload["status"]`.
4. Late-arriving enrichment via `UpdateCandidate` / `UpdateEvent` RPCs.

All science fields, classification, action type/status, and result details live in
`google.protobuf.Struct` payloads — schema is the telescope team's choice.

## Service surface

```
TelemetryGateway
  Ingest path (pipelines → gateway)
    SubmitCandidate        submit one beam candidate
    ReportEvent            cluster N candidates into one event; returns action_span_ids
    ReportDownstreamResult close an action span with success/failure status

  Enrichment path (post-processing → gateway)
    UpdateCandidate        JSONB shallow-merge patch into existing candidate payload
    UpdateEvent            JSONB shallow-merge patch into existing event payload

  Query path (Grafana / tooling → gateway)
    GetCandidate / GetEvent / ListCandidates / ListEvents
```

## Key messages

- `ObservationContext` — trace envelope attached to every ingest message; the gateway
  reads `trace_id` to reconstruct the calling span's context.
- `BeamCandidate` — per-beam detection; `payload` holds DM, SNR, arrival time, etc.
- `AstrophysicalEvent` — clustering result; `candidate_ids[]` + `provenance_edges[]` + `payload`.
- `DownstreamResult` — outcome of one async action; gateway uses `action_span_id` to
  close the right child span opened during `ReportEvent`.

## Generated stubs

```
gen/go/telescope/v1/     Go stubs (events.pb.go, events_grpc.pb.go)
gen/python/              Python stubs (events_pb2.py, events_pb2_grpc.py)
```

Regenerate with:
```bash
make proto-go
make proto-python
```
