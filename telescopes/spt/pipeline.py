"""
SPT mock pipeline — telescope-obs POC
=======================================
South Pole Telescope — 10-metre CMB telescope.
Observes at 95 / 150 / 220 GHz.  Detects CMB transients and galaxy clusters.

Payload schema is completely different from CHIME — same client library,
different science fields.  The gateway stores both as opaque JSONB.
"""

import logging
import os
import random
import time
import uuid

from telescope_obs import TelemetryClient, InfraMetrics, PipelineMetrics
from telescope_obs.otel import setup_otel

log = logging.getLogger("spt.pipeline")

GATEWAY_ADDR  = os.getenv("GATEWAY_ADDR",  "gateway:50051")
OTEL_ENDPOINT = os.getenv("OTEL_ENDPOINT", "otel-collector:4317")
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL", "12.0"))

TELESCOPE   = "spt"
SERVICE_VER = "0.1.0"

_MJD_UNIX_EPOCH = 40587.0
def _now_mjd() -> float:
    return _MJD_UNIX_EPOCH + time.time() / 86400.0

BANDS_GHZ = [95, 150, 220]


def _candidate_payload(detector_id: int) -> dict:
    """SPT-specific bolometer readout parameters."""
    band = random.choice(BANDS_GHZ)
    return {
        # CMB-specific: temperature fluctuation rather than DM
        "delta_t_uK":        round(random.uniform(-200.0, 200.0), 2),   # μK
        "delta_t_uncertainty_uK": round(random.uniform(5.0, 20.0), 2),
        "significance_sigma": round(random.uniform(4.0, 15.0), 1),
        # Pointing
        "ra_deg":            round(random.uniform(0.0, 360.0), 4),
        "dec_deg":           round(random.uniform(-90.0, -40.0), 4),
        "pointing_uncertainty_arcmin": round(random.uniform(0.1, 2.0), 3),
        # Instrument
        "band_ghz":          band,
        "detector_id":       detector_id,
        "sample_time_mjd":   round(_now_mjd(), 7),
        "pwv_mm":            round(random.uniform(0.1, 1.5), 2),  # precipitable water vapour
    }


def _event_payload(candidate_payloads: list[dict]) -> dict:
    best = max(candidate_payloads, key=lambda c: c["significance_sigma"])
    bands = list({c["band_ghz"] for c in candidate_payloads})
    return {
        "classification":            random.choice(["cmb_transient", "sz_cluster_candidate", "point_source"]),
        "classification_confidence": round(random.uniform(0.65, 0.97), 3),
        "clustering_algorithm":      "matched_filter",
        "best_significance_sigma":   best["significance_sigma"],
        "best_delta_t_uK":           best["delta_t_uK"],
        "best_ra_deg":               best["ra_deg"],
        "best_dec_deg":              best["dec_deg"],
        "bands_detected":            sorted(bands),
        "num_detectors":             len(candidate_payloads),
    }


def run_detection(client: TelemetryClient, pm: PipelineMetrics) -> tuple[str, list[str]]:
    triggered_detectors = random.sample(range(64), k=random.randint(3, 8))
    log.info("Detection on %d SPT detectors", len(triggered_detectors))

    candidate_ids:      list[str] = []
    candidate_payloads: list[dict] = []

    with client.start_run("spt.detection") as run:
        t_start = time.monotonic()
        for det_id in triggered_detectors:
            payload = _candidate_payload(det_id)
            candidate_payloads.append(payload)
            cid = run.submit_candidate(
                beam_id=det_id,
                stage="readout",
                payload=payload,
            )
            candidate_ids.append(cid)
            pm.record_candidate(beam_id=det_id, stage="readout")
            log.info("  candidate %s  det=%-3d  ΔT=%.1fμK  σ=%.1f",
                     cid[:8], det_id, payload["delta_t_uK"], payload["significance_sigma"])

        event_payload = _event_payload(candidate_payloads)
        provenance = [
            {"source_candidate_id": cid, "weight": round(random.uniform(0.6, 1.0), 3),
             "association_method": "matched_filter_coincidence"}
            for cid in candidate_ids
        ]
        event_id, action_span_ids = run.report_event(
            candidate_ids=candidate_ids,
            provenance_edges=provenance,
            payload=event_payload,
        )
        pm.record_event(event_payload["classification"], time.monotonic() - t_start)
        log.info("Event %s  class=%s  σ=%.1f",
                 event_id[:8], event_payload["classification"],
                 event_payload["best_significance_sigma"])

        def _action(ok_prob, ok_result, error_msg):
            failed = random.random() > ok_prob
            return {**ok_result, "status": "failure" if failed else "success",
                    "error": error_msg if failed else ""}

        action_results = [
            _action(0.70, {
                "action": "data_dump",
                "output_uri": f"file:///data/spt/{event_id}/timestream.fits",
            }, "NFS mount unavailable: /data/spt not reachable"),
            _action(0.85, {
                "action": "voevent_alert",
                "ivorn": f"ivo://spt.uchicago.edu/transients#{event_id}",
            }, "TLS handshake timeout: voevent broker unreachable"),
            _action(0.90, {
                "action": "db_write",
                "table": "spt_events",
                "record_id": event_id,
            }, "connection pool exhausted: max 10 connections reached"),
        ]
        for span_id, result in zip(action_span_ids, action_results):
            run.report_downstream_result(event_id, span_id, result)
            pm.record_action(result["action"], result["status"])
            if result["status"] == "success":
                log.info("  action %-16s ✓", result["action"])
            else:
                log.error("  action %-16s ✗  error=%s", result["action"], result.get("error", "unknown"))

    return event_id, candidate_ids


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-20s %(levelname)s %(message)s",
    )
    setup_otel(f"telescope-{TELESCOPE}-pipeline", SERVICE_VER, OTEL_ENDPOINT)
    InfraMetrics(telescope=TELESCOPE, data_path="/")
    pm = PipelineMetrics(telescope=TELESCOPE)
    client = TelemetryClient(GATEWAY_ADDR, telescope=TELESCOPE, service_version=SERVICE_VER)
    log.info("SPT mock pipeline started  gateway=%s", GATEWAY_ADDR)

    while True:
        try:
            event_id, candidate_ids = run_detection(client, pm)
            time.sleep(random.uniform(2.0, 5.0))
            with client.start_run("spt.diagnostic_plot") as run:
                run.update_event(event_id, patch={
                    "diagnostic_plot_uri": f"file:///plots/spt/{event_id}/map.fits",
                    "plot_generated_at_mjd": round(_now_mjd(), 6),
                })
                log.info("Map attached to event %s", event_id[:8])
        except Exception:
            log.exception("Pipeline run failed")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
