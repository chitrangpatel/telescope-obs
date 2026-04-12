"""
telescope_obs.client
────────────────────
TelemetryClient + TelemetryRun: the only API surface telescope devs touch.
The underlying gRPC stubs and OTel wiring are fully encapsulated here.

Trace threading model
─────────────────────
  TelemetryClient.start_run() creates a root OTel span and sets it as
  the active context via Python's contextvars.  Every method on
  TelemetryRun starts a child span under that root, so the full pipeline
  (candidates → event → actions → enrichment) shares one trace_id.

  The W3C traceparent is injected into gRPC metadata automatically by
  opentelemetry-instrumentation-grpc.  It is also written explicitly into
  ObservationContext.trace_id / span_id as a belt-and-suspenders fallback
  for the gateway's manual reconstruction path.

  For late-arriving enrichment (diagnostic plots, re-analysis) call
  start_run() again — a new trace is created.  The gateway records the
  association and injects OTel span links so Tempo can navigate between
  the original detection trace and the later enrichment trace.
"""

import time
import uuid
from contextlib import contextmanager
from typing import Generator, Optional

import grpc
from google.protobuf.struct_pb2 import Struct
from google.protobuf.timestamp_pb2 import Timestamp
from opentelemetry import trace
from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient
from opentelemetry.trace import StatusCode

# Imported from gen/python/ — must be on PYTHONPATH before importing this module.
from events_pb2 import (  # type: ignore[import]
    AstrophysicalEvent,
    BeamCandidate,
    DownstreamResult,
    ObservationContext,
    ProvenanceEdge,
    ReportDownstreamResultRequest,
    ReportEventRequest,
    SubmitCandidateRequest,
    UpdateCandidateRequest,
    UpdateEventRequest,
)
from events_pb2_grpc import TelemetryGatewayStub  # type: ignore[import]

_grpc_instrumented = False


def _ensure_grpc_instrumented() -> None:
    global _grpc_instrumented
    if not _grpc_instrumented:
        GrpcInstrumentorClient().instrument()
        _grpc_instrumented = True


def _dict_to_struct(d: dict) -> Struct:
    s = Struct()
    s.update(d)
    return s


def _now_ts() -> Timestamp:
    return Timestamp(seconds=int(time.time()))


def _active_trace_and_span() -> tuple[str, str]:
    """Return (trace_id_hex, span_id_hex) of the currently active OTel span."""
    sc = trace.get_current_span().get_span_context()
    if not sc.is_valid:
        return "", ""
    return format(sc.trace_id, "032x"), format(sc.span_id, "016x")


# ── TelemetryRun ──────────────────────────────────────────────────

class TelemetryRun:
    """
    A single pipeline detection run sharing one W3C trace.

    Every method creates a child span under the root span opened by
    TelemetryClient.start_run().  Telescope devs call these methods
    directly — no trace IDs, no gRPC boilerplate.
    """

    def __init__(self, client: "TelemetryClient") -> None:
        self._client = client
        self._tracer = trace.get_tracer("telescope_obs.pipeline")

    # ── Ingest ────────────────────────────────────────────────────

    def submit_candidate(
        self,
        beam_id: int,
        stage: str,
        payload: dict,
        candidate_id: Optional[str] = None,
    ) -> str:
        """
        Submit a beam candidate to the gateway.

        Args:
            beam_id:      Beam index within the telescope array.
            stage:        Free-form label for this pipeline stage.
            payload:      Telescope-specific detection parameters.
                          e.g. {"dm": 349.2, "snr": 11.4, "arrival_time_mjd": 60012.3}
            candidate_id: UUID; auto-generated if omitted.

        Returns:
            The candidate_id accepted by the gateway.
        """
        candidate_id = candidate_id or str(uuid.uuid4())

        with self._tracer.start_as_current_span("pipeline.submit_candidate") as span:
            span.set_attribute("candidate.id", candidate_id)
            span.set_attribute("telescope", self._client.telescope)
            span.set_attribute("beam.id", beam_id)
            span.set_attribute("stage", stage)

            trace_id, span_id = _active_trace_and_span()
            obs = ObservationContext(
                trace_id=trace_id,
                span_id=span_id,
                telescope=self._client.telescope,
                beam_id=beam_id,
                stage=stage,
                service_version=self._client.service_version,
                observation_time=_now_ts(),
            )
            resp = self._client._stub.SubmitCandidate(
                SubmitCandidateRequest(
                    candidate=BeamCandidate(
                        candidate_id=candidate_id,
                        context=obs,
                        payload=_dict_to_struct(payload),
                    )
                )
            )
            if not resp.accepted:
                span.set_status(StatusCode.ERROR, resp.rejection_reason)

            return resp.candidate_id

    def report_event(
        self,
        candidate_ids: list[str],
        payload: dict,
        provenance_edges: Optional[list[dict]] = None,
        event_id: Optional[str] = None,
    ) -> tuple[str, list[str]]:
        """
        Report a formed astrophysical event (N candidates → 1 event).

        Args:
            candidate_ids:    IDs of the contributing beam candidates.
            payload:          Science parameters and classification.
                              e.g. {"classification": "frb_candidate", "best_dm": 349.2}
            provenance_edges: DAG edges; each is a dict with keys
                              source_candidate_id, weight, association_method.
            event_id:         UUID; auto-generated if omitted.

        Returns:
            (event_id, action_span_ids) where action_span_ids are the
            gateway-issued span IDs to pass to report_downstream_result.
        """
        event_id = event_id or str(uuid.uuid4())

        with self._tracer.start_as_current_span("pipeline.report_event") as span:
            span.set_attribute("event.id", event_id)
            span.set_attribute("event.num_candidates", len(candidate_ids))
            span.set_attribute("telescope", self._client.telescope)

            trace_id, _ = _active_trace_and_span()
            edges = [
                ProvenanceEdge(
                    source_candidate_id=e["source_candidate_id"],
                    weight=float(e.get("weight", 1.0)),
                    association_method=e.get("association_method", ""),
                )
                for e in (provenance_edges or [])
            ]
            resp = self._client._stub.ReportEvent(
                ReportEventRequest(
                    event=AstrophysicalEvent(
                        event_id=event_id,
                        trace_id=trace_id,
                        candidate_ids=candidate_ids,
                        provenance_edges=edges,
                        payload=_dict_to_struct(payload),
                        event_time=_now_ts(),
                    )
                )
            )
            return resp.event_id, list(resp.action_span_ids)

    def report_downstream_result(
        self,
        event_id: str,
        action_span_id: str,
        payload: dict,
    ) -> None:
        """
        Report the outcome of a downstream action (data dump, VOEvent, DB write).

        The gateway closes the corresponding child span with the status
        derived from payload["status"] ("success" or "failure").

        Args:
            event_id:       The event this action was triggered by.
            action_span_id: Span ID received from report_event's response.
            payload:        Action type and result.
                            Must include "action" and "status" keys.
                            e.g. {"action": "data_dump", "status": "success",
                                   "output_uri": "s3://...", "bytes_written": 1048576}
        """
        with self._tracer.start_as_current_span("pipeline.downstream_result") as span:
            trace_id, _ = _active_trace_and_span()
            span.set_attribute("event.id", event_id)
            span.set_attribute("action", payload.get("action", ""))
            span.set_attribute("status", payload.get("status", ""))

            self._client._stub.ReportDownstreamResult(
                ReportDownstreamResultRequest(
                    result=DownstreamResult(
                        event_id=event_id,
                        trace_id=trace_id,
                        action_span_id=action_span_id,
                        payload=_dict_to_struct(payload),
                        completed_at=_now_ts(),
                    )
                )
            )

    # ── Enrichment ────────────────────────────────────────────────
    # These are typically called from a *separate* pipeline run
    # (new trace) after data collection or re-analysis finishes.

    def update_candidate(self, candidate_id: str, patch: dict) -> None:
        """
        Merge `patch` into a candidate's payload.
        Typical use: attach diagnostic_plot_uri after plot generation.
        """
        with self._tracer.start_as_current_span("pipeline.update_candidate"):
            trace_id, _ = _active_trace_and_span()
            self._client._stub.UpdateCandidate(
                UpdateCandidateRequest(
                    candidate_id=candidate_id,
                    trace_id=trace_id,
                    patch=_dict_to_struct(patch),
                )
            )

    def update_event(self, event_id: str, patch: dict) -> None:
        """
        Merge `patch` into an event's payload.
        Typical use: attach summary plot, updated classification, etc.
        """
        with self._tracer.start_as_current_span("pipeline.update_event"):
            trace_id, _ = _active_trace_and_span()
            self._client._stub.UpdateEvent(
                UpdateEventRequest(
                    event_id=event_id,
                    trace_id=trace_id,
                    patch=_dict_to_struct(patch),
                )
            )


# ── TelemetryClient ───────────────────────────────────────────────

class TelemetryClient:
    """
    Entry point for telescope pipeline services.

    Wraps the TelemetryGateway gRPC stubs with OTel context injection.
    Instantiate once at service startup; share across pipeline iterations.

    Example
    -------
    from telescope_obs import TelemetryClient
    from telescope_obs.otel import setup_otel

    setup_otel("chime-pipeline", "0.1.0", "otel-collector:4317")
    client = TelemetryClient("gateway:50051", telescope="chime")

    while True:
        with client.start_run("chime.detection") as run:
            cid = run.submit_candidate(beam_id=42, stage="pipeline", payload={...})
            eid, sids = run.report_event(candidate_ids=[cid], payload={...})
            for sid in sids:
                run.report_downstream_result(eid, sid, {"action": "data_dump", ...})

        # Later — separate trace, span-linked to the detection trace:
        with client.start_run("chime.diagnostic_plot") as run:
            run.update_event(eid, patch={"diagnostic_plot_uri": "file:///..."})
    """

    def __init__(
        self,
        gateway_addr: str,
        telescope: str,
        service_version: str = "0.1.0",
    ) -> None:
        self.telescope = telescope
        self.service_version = service_version
        self._tracer = trace.get_tracer("telescope_obs.client")

        # Instruments the gRPC channel to inject W3C traceparent into
        # outgoing RPC metadata automatically.
        _ensure_grpc_instrumented()

        channel = grpc.insecure_channel(gateway_addr)
        self._stub = TelemetryGatewayStub(channel)

    @contextmanager
    def start_run(self, run_name: str = "pipeline_run") -> Generator[TelemetryRun, None, None]:
        """
        Context manager for one pipeline detection run.

        Creates a root OTel span and activates it via Python contextvars.
        All TelemetryRun methods called within the block become child spans
        sharing the same trace_id.

        A new call to start_run() outside this block creates a new trace —
        the multi-trace scenario for late enrichment pipelines.
        """
        with self._tracer.start_as_current_span(run_name):
            yield TelemetryRun(self)
