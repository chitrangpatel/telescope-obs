-- telescope-obs: initial schema
-- Runs automatically on first container start via /docker-entrypoint-initdb.d

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── Beam candidates ────────────────────────────────────────────────────────────
-- One row per BeamCandidate proto message.
-- `payload` holds the telescope-specific Struct (dm, snr, …) as JSONB.
-- Diagnostic plots and any post-processing enrichment are merged in via
--   UPDATE candidates SET payload = payload || $patch WHERE candidate_id = $id
-- which maps directly to the UpdateCandidate RPC.

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id     TEXT        NOT NULL,
    telescope        TEXT        NOT NULL,
    beam_id          INTEGER     NOT NULL,
    stage            TEXT        NOT NULL,
    trace_id         TEXT        NOT NULL,
    span_id          TEXT        NOT NULL,
    service_version  TEXT,
    observation_time TIMESTAMPTZ NOT NULL,
    payload          JSONB       NOT NULL DEFAULT '{}',
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (candidate_id, observation_time)
);

-- TimescaleDB hypertable: partitions by observation_time (7-day chunks)
SELECT create_hypertable(
    'candidates', 'observation_time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE
);

-- Query patterns: telescope + time range (ListCandidates), trace correlation, payload search
CREATE INDEX ON candidates (telescope, observation_time DESC);
CREATE INDEX ON candidates (trace_id);
CREATE INDEX ON candidates USING GIN (payload jsonb_path_ops);


-- ── Astrophysical events ───────────────────────────────────────────────────────
-- One row per AstrophysicalEvent proto message.
-- `candidate_ids` is a TEXT[] for fast containment checks (ANY / @> operator).
-- `payload` holds classification, best-fit parameters, plot URIs, etc.

CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT        NOT NULL,
    trace_id    TEXT        NOT NULL,
    -- Denormalised for fast "which event used this candidate?" lookups.
    -- Authoritative DAG lives in provenance_edges.
    candidate_ids TEXT[]    NOT NULL DEFAULT '{}',
    payload       JSONB     NOT NULL DEFAULT '{}',
    event_time    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (event_id, event_time)
);

SELECT create_hypertable(
    'events', 'event_time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE
);

CREATE INDEX ON events (trace_id);
CREATE INDEX ON events USING GIN (candidate_ids);
CREATE INDEX ON events USING GIN (payload jsonb_path_ops);


-- ── Provenance edges (DAG) ─────────────────────────────────────────────────────
-- Encodes the N-to-1 relationship: N candidates → 1 event.
-- This is the authoritative DAG; candidate_ids[] in events is a fast-lookup cache.

CREATE TABLE IF NOT EXISTS provenance_edges (
    source_candidate_id  TEXT  NOT NULL,
    target_event_id      TEXT  NOT NULL,
    weight               REAL  NOT NULL DEFAULT 1.0,
    association_method   TEXT,
    PRIMARY KEY (source_candidate_id, target_event_id)
);

CREATE INDEX ON provenance_edges (target_event_id);
CREATE INDEX ON provenance_edges (source_candidate_id);


-- ── Downstream action results ──────────────────────────────────────────────────
-- One row per DownstreamResult proto message (data_dump / voevent / db_write).
-- `payload` holds action type, status, output URI, error, etc. — all
-- telescope/service-defined; the gateway never interprets the contents,
-- only uses action_span_id to close the OTel child span.

CREATE TABLE IF NOT EXISTS downstream_results (
    id             BIGSERIAL   PRIMARY KEY,
    event_id       TEXT        NOT NULL,
    trace_id       TEXT        NOT NULL,
    action_span_id TEXT        NOT NULL,
    payload        JSONB       NOT NULL DEFAULT '{}',
    completed_at   TIMESTAMPTZ NOT NULL,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ON downstream_results (event_id);
CREATE INDEX ON downstream_results (trace_id);
CREATE INDEX ON downstream_results USING GIN (payload jsonb_path_ops);
