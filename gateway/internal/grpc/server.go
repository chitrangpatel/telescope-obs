package grpc

import (
	"context"
	"sync"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	otelcodes "go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/emptypb"

	"github.com/telescope-obs/telescope-obs/gateway/internal/db"
	telescopev1 "github.com/telescope-obs/telescope-obs/gen/go/telescope/v1"
)

// Server implements telescopev1.TelemetryGatewayServer.
//
// Span lifecycle for downstream actions
// ──────────────────────────────────────
//   ReportEvent opens one child span per expected downstream action
//   (data_dump, voevent_alert, db_write) and parks them in actionSpans,
//   keyed by their hex span ID.  ReportDownstreamResult retrieves the
//   matching span, stamps it with the status from the payload Struct,
//   and ends it — completing that leg of the distributed trace.
//
// Multi-trace association
// ───────────────────────
//   An event or candidate is a long-lived object that multiple independent
//   pipeline runs can touch at different times, each with its own trace:
//   detection → downstream actions → diagnostic plot → re-analysis, etc.
//
//   The gateway records every (entity_id, trace_id, operation) pair in
//   event_traces / candidate_traces.  On any subsequent operation, it
//   fetches all prior trace IDs and injects them as OTel span links into
//   the new span, so Tempo can navigate across traces for the same event.
type Server struct {
	telescopev1.UnimplementedTelemetryGatewayServer

	store  *db.Store
	tracer trace.Tracer

	// actionSpans holds open spans created during ReportEvent, waiting to
	// be closed by the corresponding ReportDownstreamResult call.
	actionSpans sync.Map // key: spanID (hex string) → trace.Span
}

func NewServer(store *db.Store) *Server {
	return &Server{
		store:  store,
		tracer: otel.Tracer("telescope-obs-gateway"),
	}
}

// ── Ingest ────────────────────────────────────────────────────────

func (s *Server) SubmitCandidate(
	ctx context.Context,
	req *telescopev1.SubmitCandidateRequest,
) (*telescopev1.SubmitCandidateResponse, error) {
	c := req.GetCandidate()
	ctx = withRemoteParent(ctx, c.GetContext())

	ctx, span := s.tracer.Start(ctx, "gateway.submit_candidate")
	defer span.End()

	obs := c.GetContext()
	span.SetAttributes(obsAttrs(obs)...)
	span.SetAttributes(strAttr("candidate.id", c.GetCandidateId()))

	if err := s.store.InsertCandidate(ctx, c); err != nil {
		span.RecordError(err)
		span.SetStatus(otelcodes.Error, err.Error())
		return nil, status.Errorf(codes.Internal, "insert candidate: %v", err)
	}

	// Record the originating trace for this candidate.
	_ = s.store.RecordCandidateTrace(ctx, c.CandidateId, obs.GetTraceId(), "creation")

	return &telescopev1.SubmitCandidateResponse{
		CandidateId: c.CandidateId,
		Accepted:    true,
	}, nil
}

func (s *Server) ReportEvent(
	ctx context.Context,
	req *telescopev1.ReportEventRequest,
) (*telescopev1.ReportEventResponse, error) {
	e := req.GetEvent()

	// Reconstruct trace context from trace_id so the event span becomes
	// a child of the clustering service's span.
	ctx = withRemoteParentFromIDs(ctx, e.GetTraceId(), "")

	ctx, eventSpan := s.tracer.Start(ctx, "gateway.report_event")
	defer eventSpan.End()

	eventSpan.SetAttributes(
		strAttr("event.id", e.GetEventId()),
		intAttr("event.num_candidates", len(e.GetCandidateIds())),
		intAttr("event.num_beams", len(e.GetCandidateIds())),
	)

	if err := s.store.InsertEvent(ctx, e); err != nil {
		eventSpan.RecordError(err)
		eventSpan.SetStatus(otelcodes.Error, err.Error())
		return nil, status.Errorf(codes.Internal, "insert event: %v", err)
	}

	// Record the originating trace for this event.
	_ = s.store.RecordEventTrace(ctx, e.EventId, e.GetTraceId(), "creation")

	// Open a child span for each expected downstream action.
	// The actions service will call ReportDownstreamResult with the span IDs
	// it receives here, closing these spans with the actual outcome.
	actions := []string{"data_dump", "voevent_alert", "db_write"}
	actionSpanIDs := make([]string, 0, len(actions))
	for _, name := range actions {
		_, actionSpan := s.tracer.Start(ctx, "downstream."+name,
			trace.WithAttributes(strAttr("event.id", e.GetEventId())),
		)
		sid := actionSpan.SpanContext().SpanID().String()
		s.actionSpans.Store(sid, actionSpan)
		actionSpanIDs = append(actionSpanIDs, sid)
	}

	return &telescopev1.ReportEventResponse{
		EventId:       e.EventId,
		ActionSpanIds: actionSpanIDs,
	}, nil
}

func (s *Server) ReportDownstreamResult(
	ctx context.Context,
	req *telescopev1.ReportDownstreamResultRequest,
) (*emptypb.Empty, error) {
	r := req.GetResult()

	// Close the parked action span with the status from the payload.
	if v, ok := s.actionSpans.LoadAndDelete(r.GetActionSpanId()); ok {
		actionSpan := v.(trace.Span)
		fields := r.GetPayload().GetFields()

		if statusVal := fields["status"].GetStringValue(); statusVal == "failure" {
			errMsg := fields["error"].GetStringValue()
			actionSpan.SetStatus(otelcodes.Error, errMsg)
		} else {
			actionSpan.SetStatus(otelcodes.Ok, "")
		}
		// action is the string label in the payload, e.g. "data_dump"
		actionSpan.SetAttributes(strAttr("action", fields["action"].GetStringValue()))
		actionSpan.End()
	}

	if err := s.store.InsertDownstreamResult(ctx, r); err != nil {
		return nil, status.Errorf(codes.Internal, "insert downstream result: %v", err)
	}

	// Record this trace against the event — the downstream action pipeline
	// may have its own trace (separate job), distinct from the creation trace.
	operation := r.GetPayload().GetFields()["action"].GetStringValue()
	if operation == "" {
		operation = "downstream"
	}
	_ = s.store.RecordEventTrace(ctx, r.GetEventId(), r.GetTraceId(), operation)

	return &emptypb.Empty{}, nil
}

// ── Enrichment ────────────────────────────────────────────────────

func (s *Server) UpdateCandidate(
	ctx context.Context,
	req *telescopev1.UpdateCandidateRequest,
) (*telescopev1.UpdateCandidateResponse, error) {
	// Fetch all prior traces for this candidate and build span links so
	// Tempo can navigate: "this enrichment run touches candidate X, which
	// was originally detected by trace Y."
	priorTraces, _ := s.store.GetCandidateTraces(ctx, req.GetCandidateId())

	ctx, span := s.tracer.Start(ctx, "gateway.update_candidate",
		trace.WithLinks(traceLinks(priorTraces)...),
	)
	defer span.End()
	span.SetAttributes(strAttr("candidate.id", req.GetCandidateId()))

	if err := s.store.PatchCandidate(ctx, req.GetCandidateId(), req.GetPatch()); err != nil {
		span.RecordError(err)
		span.SetStatus(otelcodes.Error, err.Error())
		return nil, status.Errorf(codes.Internal, "patch candidate: %v", err)
	}

	_ = s.store.RecordCandidateTrace(ctx, req.GetCandidateId(), req.GetTraceId(), "enrichment")

	return &telescopev1.UpdateCandidateResponse{Applied: true}, nil
}

func (s *Server) UpdateEvent(
	ctx context.Context,
	req *telescopev1.UpdateEventRequest,
) (*telescopev1.UpdateEventResponse, error) {
	// Fetch all prior traces for this event and build span links.
	priorTraces, _ := s.store.GetEventTraces(ctx, req.GetEventId())

	ctx, span := s.tracer.Start(ctx, "gateway.update_event",
		trace.WithLinks(traceLinks(priorTraces)...),
	)
	defer span.End()
	span.SetAttributes(strAttr("event.id", req.GetEventId()))

	if err := s.store.PatchEvent(ctx, req.GetEventId(), req.GetPatch()); err != nil {
		span.RecordError(err)
		span.SetStatus(otelcodes.Error, err.Error())
		return nil, status.Errorf(codes.Internal, "patch event: %v", err)
	}

	_ = s.store.RecordEventTrace(ctx, req.GetEventId(), req.GetTraceId(), "enrichment")

	return &telescopev1.UpdateEventResponse{Applied: true}, nil
}

// ── Query ─────────────────────────────────────────────────────────

func (s *Server) GetCandidate(
	ctx context.Context,
	req *telescopev1.GetCandidateRequest,
) (*telescopev1.BeamCandidate, error) {
	c, err := s.store.GetCandidate(ctx, req.GetCandidateId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "%v", err)
	}
	return c, nil
}

func (s *Server) GetEvent(
	ctx context.Context,
	req *telescopev1.GetEventRequest,
) (*telescopev1.AstrophysicalEvent, error) {
	e, err := s.store.GetEvent(ctx, req.GetEventId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "%v", err)
	}
	return e, nil
}

func (s *Server) ListCandidates(
	ctx context.Context,
	req *telescopev1.ListCandidatesRequest,
) (*telescopev1.ListCandidatesResponse, error) {
	candidates, err := s.store.ListCandidates(ctx, req)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "list candidates: %v", err)
	}
	return &telescopev1.ListCandidatesResponse{Candidates: candidates}, nil
}

func (s *Server) ListEvents(
	ctx context.Context,
	req *telescopev1.ListEventsRequest,
) (*telescopev1.ListEventsResponse, error) {
	events, err := s.store.ListEvents(ctx, req)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "list events: %v", err)
	}
	return &telescopev1.ListEventsResponse{Events: events}, nil
}

// ── OTel helpers ──────────────────────────────────────────────────

// withRemoteParent reconstructs the W3C trace context from ObservationContext
// so the new gateway span becomes a child of the calling service's span.
// No-op if the gRPC metadata already carries a valid traceparent (set by
// the otelgrpc middleware) — in that case the context is already correct.
func withRemoteParent(ctx context.Context, obs *telescopev1.ObservationContext) context.Context {
	if obs == nil {
		return ctx
	}
	return withRemoteParentFromIDs(ctx, obs.GetTraceId(), obs.GetSpanId())
}

func withRemoteParentFromIDs(ctx context.Context, traceHex, spanHex string) context.Context {
	// Don't override a context already in a valid span (injected via metadata).
	if trace.SpanFromContext(ctx).SpanContext().IsValid() {
		return ctx
	}
	traceID, err := trace.TraceIDFromHex(traceHex)
	if err != nil {
		return ctx
	}
	cfg := trace.SpanContextConfig{
		TraceID:    traceID,
		TraceFlags: trace.FlagsSampled,
		Remote:     true,
	}
	if spanHex != "" {
		if sid, err := trace.SpanIDFromHex(spanHex); err == nil {
			cfg.SpanID = sid
		}
	}
	sc := trace.NewSpanContext(cfg)
	if !sc.IsValid() {
		return ctx
	}
	return trace.ContextWithRemoteSpanContext(ctx, sc)
}

// ── Attribute shorthands ──────────────────────────────────────────

func obsAttrs(obs *telescopev1.ObservationContext) []attribute.KeyValue {
	if obs == nil {
		return nil
	}
	return []attribute.KeyValue{
		attribute.String("telescope", obs.GetTelescope()),
		attribute.String("stage", obs.GetStage()),
		attribute.Int("beam.id", int(obs.GetBeamId())),
	}
}

func strAttr(k, v string) attribute.KeyValue { return attribute.String(k, v) }
func intAttr(k string, v int) attribute.KeyValue  { return attribute.Int(k, v) }

// traceLinks converts stored trace associations into OTel span links.
// Each link carries the operation name as an attribute so it is visible
// in Tempo's "Related traces" panel.
func traceLinks(assocs []db.TraceAssoc) []trace.Link {
	links := make([]trace.Link, 0, len(assocs))
	for _, a := range assocs {
		tid, err := trace.TraceIDFromHex(a.TraceID)
		if err != nil {
			continue
		}
		sc := trace.NewSpanContext(trace.SpanContextConfig{
			TraceID:    tid,
			TraceFlags: trace.FlagsSampled,
			Remote:     true,
		})
		links = append(links, trace.Link{
			SpanContext: sc,
			Attributes: []attribute.KeyValue{
				attribute.String("linked.operation", a.Operation),
			},
		})
	}
	return links
}
