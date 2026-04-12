"""
CHIME mock pipeline — telescope-obs POC
========================================
Canadian Hydrogen Intensity Mapping Experiment.
400–800 MHz transit telescope, optimised for FRB detection.

Simulates the full single-telescope pipeline:
  1. Generate beam candidates (mocks correlator + RFI mitigation + dedispersion)
  2. Cluster candidates across beams into an astrophysical event
  3. Report event → receive gateway-issued action span IDs
  4. Report downstream action results (data dump, VOEvent, DB write)
  5. After a simulated data-collection delay, run a separate diagnostic
     plot pipeline (new trace, span-linked to the detection trace via
     the gateway's event_traces table).

All science fields are CHIME-specific and live entirely in the payload
Struct — the client library and gateway never inspect them.
"""

import logging
import os
import random
import time
import uuid

from telescope_obs import TelemetryClient, InfraMetrics, PipelineMetrics
from telescope_obs.otel import setup_otel

log = logging.getLogger("chime.pipeline")

# ── Config ────────────────────────────────────────────────────────

GATEWAY_ADDR  = os.getenv("GATEWAY_ADDR",  "gateway:50051")
OTEL_ENDPOINT = os.getenv("OTEL_ENDPOINT", "otel-collector:4317")
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL", "8.0"))  # seconds between detections

TELESCOPE    = "chime"
SERVICE_VER  = "0.1.0"

# CHIME instrument parameters
N_BEAMS          = 16       # using 16 of 1024 for the mock
FREQ_CENTER_MHZ  = 600.0
BANDWIDTH_MHZ    = 400.0    # 400–800 MHz

# MJD of Unix epoch (1970-01-01)
_MJD_UNIX_EPOCH = 40587.0


def _now_mjd() -> float:
    return _MJD_UNIX_EPOCH + time.time() / 86400.0


# ── Mock science data generators ──────────────────────────────────

def _candidate_payload(beam_id: int) -> dict:
    """
    CHIME-specific FRB candidate parameters.
    These go straight into BeamCandidate.payload — the gateway
    stores them as JSONB and never interprets them.
    """
    base_dm = random.uniform(100.0, 2500.0)
    return {
        # Dispersion measure and uncertainty
        "dm":                round(base_dm, 2),
        "dm_uncertainty":    round(random.uniform(0.5, 5.0), 2),
        # Detection quality
        "snr":               round(random.uniform(8.0, 45.0), 1),
        "rfi_score":         round(random.uniform(0.0, 0.25), 3),
        # Timing
        "arrival_time_mjd":  round(_now_mjd() + random.uniform(0, 0.001), 7),
        "pulse_width_ms":    round(random.uniform(0.3, 30.0), 2),
        # Frequency
        "center_freq_mhz":   FREQ_CENTER_MHZ,
        "bandwidth_mhz":     BANDWIDTH_MHZ,
        # Beam
        "beam_id":           beam_id,
        "sub_band":          random.randint(0, 3),
    }


def _event_payload(candidate_payloads: list[dict]) -> dict:
    """
    CHIME-specific event parameters derived from clustering.
    """
    best = max(candidate_payloads, key=lambda c: c["snr"])
    dms  = [c["dm"] for c in candidate_payloads]
    return {
        "classification":            "frb_candidate",
        "classification_confidence": round(random.uniform(0.72, 0.99), 3),
        "clustering_algorithm":      "dbscan",
        # Best-fit parameters
        "best_dm":               best["dm"],
        "best_dm_uncertainty":   best["dm_uncertainty"],
        "best_snr":              best["snr"],
        "best_arrival_time_mjd": best["arrival_time_mjd"],
        "dm_spread":             round(max(dms) - min(dms), 2),
        "num_sub_bands":         len({c["sub_band"] for c in candidate_payloads}),
    }


# ── Pipeline run ──────────────────────────────────────────────────

def run_detection(client: TelemetryClient, pm: PipelineMetrics) -> list[str]:
    """
    One full detection cycle.  Returns candidate IDs for the plot pipeline.
    """
    triggered_beams = random.sample(range(N_BEAMS), k=random.randint(2, 5))
    log.info("Detection triggered on beams %s", triggered_beams)

    candidate_ids:      list[str] = []
    candidate_payloads: list[dict] = []
    t_start = time.monotonic()

    with client.start_run("chime.detection") as run:

        # ── Candidates ─────────────────────────────────────────────
        for beam_id in triggered_beams:
            payload = _candidate_payload(beam_id)
            candidate_payloads.append(payload)

            cid = run.submit_candidate(
                beam_id=beam_id,
                stage="pipeline",
                payload=payload,
            )
            candidate_ids.append(cid)
            pm.record_candidate(beam_id=beam_id, stage="pipeline")
            log.info("  candidate %s  beam=%-3d  DM=%.1f  SNR=%.1f  rfi=%.3f",
                     cid[:8], beam_id, payload["dm"], payload["snr"], payload["rfi_score"])

        # ── Event ───────────────────────────────────────────────────
        event_payload = _event_payload(candidate_payloads)
        provenance = [
            {
                "source_candidate_id": cid,
                "weight":             round(random.uniform(0.75, 1.0), 3),
                "association_method": "dm_coincidence",
            }
            for cid in candidate_ids
        ]
        event_id, action_span_ids = run.report_event(
            candidate_ids=candidate_ids,
            provenance_edges=provenance,
            payload=event_payload,
        )
        pm.record_event(
            classification=event_payload["classification"],
            duration_s=time.monotonic() - t_start,
        )
        log.info("Event %s  class=%s  DM=%.1f  confidence=%.2f",
                 event_id[:8],
                 event_payload["classification"],
                 event_payload["best_dm"],
                 event_payload["classification_confidence"])

        # ── Downstream actions ──────────────────────────────────────
        def _action(ok_prob, ok_result, error_msg):
            failed = random.random() > ok_prob
            return {**ok_result, "status": "failure" if failed else "success",
                    "error": error_msg if failed else ""}

        action_results = [
            _action(0.65, {
                "action": "data_dump",
                "output_uri": f"file:///data/chime/{event_id}/raw_voltages.npz",
                "bytes_written": random.randint(20_000_000, 800_000_000),
            }, "disk write timeout: /data/chime is full"),
            _action(0.80, {
                "action": "voevent_alert",
                "ivorn": f"ivo://chime-frb.ca/alerts#{event_id}",
            }, "broker connection refused: voevent.chime-frb.ca:8098"),
            _action(0.90, {
                "action": "db_write",
                "table": "chime_events",
                "record_id": event_id,
            }, "duplicate key violates unique constraint chime_events_pkey"),
        ]
        for span_id, result in zip(action_span_ids, action_results):
            run.report_downstream_result(event_id, span_id, result)
            pm.record_action(action=result["action"], status=result["status"])
            if result["status"] == "success":
                log.info("  action %-16s ✓", result["action"])
            else:
                log.error("  action %-16s ✗  error=%s", result["action"], result.get("error", "unknown"))

    # Return the event_id and candidate_ids so the plot pipeline can use them
    # (they persist after the context manager exits).
    return event_id, candidate_ids


def run_diagnostic_plot(client: TelemetryClient, event_id: str, candidate_ids: list[str]) -> None:
    """
    Separate pipeline run (new trace) that generates diagnostic plots
    after data collection is complete.

    The gateway records this trace_id in event_traces and creates OTel
    span links back to the detection trace, so Tempo shows them as related.
    """
    log.info("Generating diagnostic plots for event %s …", event_id[:8])

    with client.start_run("chime.diagnostic_plot") as run:
        run.update_event(event_id, patch={
            "diagnostic_plot_uri":    f"file:///plots/chime/{event_id}/waterfall.png",
            "plot_pipeline_version":  SERVICE_VER,
            "plot_generated_at_mjd":  round(_now_mjd(), 6),
        })
        log.info("  summary plot attached to event %s", event_id[:8])

        for cid in candidate_ids:
            run.update_candidate(cid, patch={
                "diagnostic_plot_uri": f"file:///plots/chime/{cid}/dedispersed.png",
            })
        log.info("  per-candidate plots attached (%d candidates)", len(candidate_ids))


# ── Main ──────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-20s %(levelname)s %(message)s",
    )

    setup_otel(
        service_name=f"telescope-{TELESCOPE}-pipeline",
        service_version=SERVICE_VER,
        otlp_endpoint=OTEL_ENDPOINT,
    )

    # Infrastructure metrics: CPU/RAM/disk gauges polled every 15 s.
    InfraMetrics(telescope=TELESCOPE, data_path="/")
    pm = PipelineMetrics(telescope=TELESCOPE)

    client = TelemetryClient(
        gateway_addr=GATEWAY_ADDR,
        telescope=TELESCOPE,
        service_version=SERVICE_VER,
    )
    log.info("CHIME mock pipeline started  gateway=%s  scan_interval=%.1fs",
             GATEWAY_ADDR, SCAN_INTERVAL)

    while True:
        try:
            event_id, candidate_ids = run_detection(client, pm)

            # Simulate data collection finishing before plot generation.
            # This creates the multi-trace scenario: detection trace A,
            # then plot trace B (span-linked to A via gateway event_traces).
            time.sleep(random.uniform(1.5, 4.0))
            run_diagnostic_plot(client, event_id, candidate_ids)

        except Exception:
            log.exception("Pipeline run failed")

        log.info("─── next detection in %.1fs ───", SCAN_INTERVAL)
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
