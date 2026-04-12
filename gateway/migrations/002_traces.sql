-- telescope-obs: per-entity trace association tables
--
-- An event or candidate is a long-lived domain object that multiple
-- independent pipeline runs can touch at different times, each with
-- its own W3C trace.  Examples for a single telescope:
--
--   operation        trace
--   ─────────────    ──────────────────────────────────────────────
--   creation         main detection pipeline (stage1→stage2→clustering)
--   data_dump        async data dump job, starts after event is formed
--   voevent_alert    async alert job
--   db_write         async DB ingestion job
--   diagnostic_plot  separate pipeline, runs after data collection finishes
--   re_analysis      re-run with updated parameters, hours/days later
--
-- The gateway uses these tables to:
--   1. Record every trace that touches a candidate or event.
--   2. On any subsequent operation (UpdateEvent, ReportDownstreamResult,
--      etc.), look up the existing trace IDs and inject OTel span links
--      into the new span — so Tempo can navigate across traces for the
--      same domain object.

CREATE TABLE IF NOT EXISTS event_traces (
    event_id    TEXT        NOT NULL,
    trace_id    TEXT        NOT NULL,
    -- Free-form label for the pipeline that produced this trace.
    -- e.g. "creation", "data_dump", "diagnostic_plot", "re_analysis"
    operation   TEXT        NOT NULL DEFAULT 'unknown',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (event_id, trace_id)
);

CREATE INDEX ON event_traces (event_id);
CREATE INDEX ON event_traces (trace_id);

CREATE TABLE IF NOT EXISTS candidate_traces (
    candidate_id TEXT        NOT NULL,
    trace_id     TEXT        NOT NULL,
    operation    TEXT        NOT NULL DEFAULT 'unknown',
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (candidate_id, trace_id)
);

CREATE INDEX ON candidate_traces (candidate_id);
CREATE INDEX ON candidate_traces (trace_id);
