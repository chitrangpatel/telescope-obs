package db

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/types/known/structpb"
	"google.golang.org/protobuf/types/known/timestamppb"

	telescopev1 "github.com/telescope-obs/telescope-obs/gen/go/telescope/v1"
)

// mjdUnixEpoch is the MJD value of the Unix epoch (1970-01-01 00:00:00 UTC).
const mjdUnixEpoch = 40587.0

// Store is the gateway's persistence layer backed by TimescaleDB.
// All telescope-specific science fields are stored as JSONB and never
// interpreted by the gateway — only the structural columns are typed.
type Store struct {
	pool *pgxpool.Pool
}

func New(ctx context.Context, connStr string) (*Store, error) {
	pool, err := pgxpool.New(ctx, connStr)
	if err != nil {
		return nil, fmt.Errorf("open pool: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping db: %w", err)
	}
	return &Store{pool: pool}, nil
}

func (s *Store) Close() { s.pool.Close() }

// ── Candidates ────────────────────────────────────────────────────

func (s *Store) InsertCandidate(ctx context.Context, c *telescopev1.BeamCandidate) error {
	payload, err := marshalStruct(c.Payload)
	if err != nil {
		return err
	}
	obs := c.GetContext()
	_, err = s.pool.Exec(ctx, `
		INSERT INTO candidates
			(candidate_id, telescope, beam_id, stage, trace_id, span_id,
			 service_version, observation_time, payload)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
		ON CONFLICT (candidate_id, observation_time) DO NOTHING`,
		c.CandidateId,
		obs.GetTelescope(),
		int(obs.GetBeamId()),
		obs.GetStage(),
		obs.GetTraceId(),
		obs.GetSpanId(),
		obs.GetServiceVersion(),
		obs.GetObservationTime().AsTime(),
		payload,
	)
	return err
}

// PatchCandidate shallow-merges `patch` into the existing payload using
// JSONB || — the direct implementation of the UpdateCandidate RPC.
func (s *Store) PatchCandidate(ctx context.Context, candidateID string, patch *structpb.Struct) error {
	p, err := marshalStruct(patch)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx,
		`UPDATE candidates SET payload = payload || $2 WHERE candidate_id = $1`,
		candidateID, p,
	)
	return err
}

func (s *Store) GetCandidate(ctx context.Context, candidateID string) (*telescopev1.BeamCandidate, error) {
	row := s.pool.QueryRow(ctx, `
		SELECT candidate_id, telescope, beam_id, stage, trace_id, span_id,
		       service_version, observation_time, payload
		FROM candidates
		WHERE candidate_id = $1
		ORDER BY observation_time DESC
		LIMIT 1`,
		candidateID,
	)
	return scanCandidate(row)
}

func (s *Store) ListCandidates(ctx context.Context, req *telescopev1.ListCandidatesRequest) ([]*telescopev1.BeamCandidate, error) {
	limit := int(req.GetLimit())
	if limit == 0 || limit > 500 {
		limit = 100
	}

	args := []any{limit}
	where := ""

	if req.Telescope != "" {
		args = append(args, req.Telescope)
		where += fmt.Sprintf(" AND telescope = $%d", len(args))
	}
	if req.StartMjd != 0 {
		args = append(args, mjdToTime(req.StartMjd))
		where += fmt.Sprintf(" AND observation_time >= $%d", len(args))
	}
	if req.EndMjd != 0 {
		args = append(args, mjdToTime(req.EndMjd))
		where += fmt.Sprintf(" AND observation_time <= $%d", len(args))
	}

	rows, err := s.pool.Query(ctx, `
		SELECT candidate_id, telescope, beam_id, stage, trace_id, span_id,
		       service_version, observation_time, payload
		FROM candidates
		WHERE TRUE`+where+`
		ORDER BY observation_time DESC
		LIMIT $1`,
		args...,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []*telescopev1.BeamCandidate
	for rows.Next() {
		c, err := scanCandidate(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// ── Events ────────────────────────────────────────────────────────

// InsertEvent inserts the event and its provenance edges atomically.
func (s *Store) InsertEvent(ctx context.Context, e *telescopev1.AstrophysicalEvent) error {
	payload, err := marshalStruct(e.Payload)
	if err != nil {
		return err
	}

	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx) //nolint:errcheck

	_, err = tx.Exec(ctx, `
		INSERT INTO events (event_id, trace_id, candidate_ids, payload, event_time)
		VALUES ($1,$2,$3,$4,$5)
		ON CONFLICT (event_id, event_time) DO NOTHING`,
		e.EventId, e.TraceId, e.CandidateIds, payload, e.EventTime.AsTime(),
	)
	if err != nil {
		return err
	}

	for _, edge := range e.ProvenanceEdges {
		_, err = tx.Exec(ctx, `
			INSERT INTO provenance_edges
				(source_candidate_id, target_event_id, weight, association_method)
			VALUES ($1,$2,$3,$4)
			ON CONFLICT DO NOTHING`,
			edge.SourceCandidateId, e.EventId, edge.Weight, edge.AssociationMethod,
		)
		if err != nil {
			return err
		}
	}

	return tx.Commit(ctx)
}

// PatchEvent shallow-merges `patch` into the existing event payload.
func (s *Store) PatchEvent(ctx context.Context, eventID string, patch *structpb.Struct) error {
	p, err := marshalStruct(patch)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx,
		`UPDATE events SET payload = payload || $2 WHERE event_id = $1`,
		eventID, p,
	)
	return err
}

func (s *Store) GetEvent(ctx context.Context, eventID string) (*telescopev1.AstrophysicalEvent, error) {
	row := s.pool.QueryRow(ctx, `
		SELECT event_id, trace_id, candidate_ids, payload, event_time
		FROM events
		WHERE event_id = $1
		ORDER BY event_time DESC
		LIMIT 1`,
		eventID,
	)
	return scanEvent(row)
}

func (s *Store) ListEvents(ctx context.Context, req *telescopev1.ListEventsRequest) ([]*telescopev1.AstrophysicalEvent, error) {
	limit := int(req.GetLimit())
	if limit == 0 || limit > 500 {
		limit = 100
	}
	rows, err := s.pool.Query(ctx, `
		SELECT event_id, trace_id, candidate_ids, payload, event_time
		FROM events
		ORDER BY event_time DESC
		LIMIT $1`,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []*telescopev1.AstrophysicalEvent
	for rows.Next() {
		ev, err := scanEvent(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, ev)
	}
	return out, rows.Err()
}

// ── Trace associations ────────────────────────────────────────────

// TraceAssoc is one row from event_traces or candidate_traces.
type TraceAssoc struct {
	TraceID   string
	Operation string
}

// RecordEventTrace inserts a trace association for an event.
// Silently ignored if (event_id, trace_id) already exists.
func (s *Store) RecordEventTrace(ctx context.Context, eventID, traceID, operation string) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO event_traces (event_id, trace_id, operation)
		VALUES ($1, $2, $3)
		ON CONFLICT DO NOTHING`,
		eventID, traceID, operation,
	)
	return err
}

// GetEventTraces returns all traces previously associated with an event,
// ordered oldest first so span links are stable.
func (s *Store) GetEventTraces(ctx context.Context, eventID string) ([]TraceAssoc, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT trace_id, operation FROM event_traces
		WHERE event_id = $1
		ORDER BY recorded_at ASC`,
		eventID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []TraceAssoc
	for rows.Next() {
		var a TraceAssoc
		if err := rows.Scan(&a.TraceID, &a.Operation); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// RecordCandidateTrace inserts a trace association for a candidate.
func (s *Store) RecordCandidateTrace(ctx context.Context, candidateID, traceID, operation string) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO candidate_traces (candidate_id, trace_id, operation)
		VALUES ($1, $2, $3)
		ON CONFLICT DO NOTHING`,
		candidateID, traceID, operation,
	)
	return err
}

// GetCandidateTraces returns all traces previously associated with a candidate.
func (s *Store) GetCandidateTraces(ctx context.Context, candidateID string) ([]TraceAssoc, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT trace_id, operation FROM candidate_traces
		WHERE candidate_id = $1
		ORDER BY recorded_at ASC`,
		candidateID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []TraceAssoc
	for rows.Next() {
		var a TraceAssoc
		if err := rows.Scan(&a.TraceID, &a.Operation); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// ── Downstream results ────────────────────────────────────────────

func (s *Store) InsertDownstreamResult(ctx context.Context, r *telescopev1.DownstreamResult) error {
	payload, err := marshalStruct(r.Payload)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx, `
		INSERT INTO downstream_results
			(event_id, trace_id, action_span_id, payload, completed_at)
		VALUES ($1,$2,$3,$4,$5)`,
		r.EventId, r.TraceId, r.ActionSpanId, payload, r.CompletedAt.AsTime(),
	)
	return err
}

// ── Scan helpers ──────────────────────────────────────────────────

type scanner interface {
	Scan(dest ...any) error
}

func scanCandidate(row scanner) (*telescopev1.BeamCandidate, error) {
	var (
		candidateID, telescope, stage, traceID, spanID, serviceVersion string
		beamID                                                          int
		obsTime                                                         time.Time
		payloadJSON                                                     []byte
	)
	if err := row.Scan(
		&candidateID, &telescope, &beamID, &stage,
		&traceID, &spanID, &serviceVersion, &obsTime, &payloadJSON,
	); err != nil {
		if err == pgx.ErrNoRows {
			return nil, fmt.Errorf("candidate not found")
		}
		return nil, err
	}
	payload, err := unmarshalStruct(payloadJSON)
	if err != nil {
		return nil, err
	}
	return &telescopev1.BeamCandidate{
		CandidateId: candidateID,
		Context: &telescopev1.ObservationContext{
			Telescope:       telescope,
			BeamId:          uint32(beamID),
			Stage:           stage,
			TraceId:         traceID,
			SpanId:          spanID,
			ServiceVersion:  serviceVersion,
			ObservationTime: timestamppb.New(obsTime),
		},
		Payload: payload,
	}, nil
}

func scanEvent(row scanner) (*telescopev1.AstrophysicalEvent, error) {
	var (
		eventID, traceID string
		candidateIDs     []string
		payloadJSON      []byte
		eventTime        time.Time
	)
	if err := row.Scan(&eventID, &traceID, &candidateIDs, &payloadJSON, &eventTime); err != nil {
		if err == pgx.ErrNoRows {
			return nil, fmt.Errorf("event not found")
		}
		return nil, err
	}
	payload, err := unmarshalStruct(payloadJSON)
	if err != nil {
		return nil, err
	}
	return &telescopev1.AstrophysicalEvent{
		EventId:      eventID,
		TraceId:      traceID,
		CandidateIds: candidateIDs,
		Payload:      payload,
		EventTime:    timestamppb.New(eventTime),
	}, nil
}

// ── JSON ↔ Struct helpers ─────────────────────────────────────────

func marshalStruct(s *structpb.Struct) ([]byte, error) {
	if s == nil {
		return []byte("{}"), nil
	}
	return protojson.Marshal(s)
}

func unmarshalStruct(b []byte) (*structpb.Struct, error) {
	if len(b) == 0 {
		return &structpb.Struct{}, nil
	}
	s := &structpb.Struct{}
	return s, protojson.Unmarshal(b, s)
}

func mjdToTime(mjd float64) time.Time {
	unixSec := (mjd - mjdUnixEpoch) * 86400.0
	sec := int64(unixSec)
	nsec := int64((unixSec - float64(sec)) * 1e9)
	return time.Unix(sec, nsec).UTC()
}
