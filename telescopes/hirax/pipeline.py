"""
HIRAX mock pipeline — telescope-obs POC
=========================================
Hydrogen Intensity and Real-time Analysis eXperiment.
256-dish array, 400–800 MHz, co-optimised for FRBs and 21cm cosmology.

Similar science to CHIME but different beam geometry, baseline layout,
and imaging pipeline.  Payload fields reflect the interferometric
imaging stage rather than CHIME's formed-beam approach.
"""

import logging
import os
import random
import time

from telescope_obs import TelemetryClient, InfraMetrics, PipelineMetrics
from telescope_obs.otel import setup_otel

log = logging.getLogger("hirax.pipeline")

GATEWAY_ADDR  = os.getenv("GATEWAY_ADDR",  "gateway:50051")
OTEL_ENDPOINT = os.getenv("OTEL_ENDPOINT", "otel-collector:4317")
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL", "10.0"))

TELESCOPE   = "hirax"
SERVICE_VER = "0.1.0"

_MJD_UNIX_EPOCH = 40587.0
def _now_mjd() -> float:
    return _MJD_UNIX_EPOCH + time.time() / 86400.0

N_DISHES = 32   # using 32 of 256 for the mock


def _candidate_payload(dish_id: int) -> dict:
    """HIRAX-specific visibility / imaging parameters."""
    return {
        # Interferometric FRB parameters
        "dm":                 round(random.uniform(100.0, 2000.0), 2),
        "snr":                round(random.uniform(7.5, 35.0), 1),
        "arrival_time_mjd":   round(_now_mjd() + random.uniform(0, 0.001), 7),
        "pulse_width_ms":     round(random.uniform(0.5, 15.0), 2),
        # Imaging-specific
        "ra_deg":             round(random.uniform(0.0, 360.0), 5),
        "dec_deg":            round(random.uniform(-40.0, 10.0), 5),
        "localisation_area_deg2": round(random.uniform(0.01, 2.0), 4),
        # Interferometric quality
        "baseline_coverage":  round(random.uniform(0.3, 0.95), 3),
        "dish_id":            dish_id,
        "rfi_fraction":       round(random.uniform(0.0, 0.15), 3),
        "freq_center_mhz":    600.0,
        "bandwidth_mhz":      400.0,
    }


def _event_payload(candidate_payloads: list[dict]) -> dict:
    best = max(candidate_payloads, key=lambda c: c["snr"])
    return {
        "classification":            "frb_candidate",
        "classification_confidence": round(random.uniform(0.70, 0.98), 3),
        "clustering_algorithm":      "visibility_correlation",
        "best_dm":                   best["dm"],
        "best_snr":                  best["snr"],
        "best_ra_deg":               best["ra_deg"],
        "best_dec_deg":              best["dec_deg"],
        "best_localisation_area_deg2": best["localisation_area_deg2"],
        "best_arrival_time_mjd":     best["arrival_time_mjd"],
        "num_dishes":                len(candidate_payloads),
    }


def run_detection(client: TelemetryClient, pm: PipelineMetrics) -> tuple[str, list[str]]:
    triggered_dishes = random.sample(range(N_DISHES), k=random.randint(4, 10))
    log.info("Detection on %d HIRAX dishes", len(triggered_dishes))

    candidate_ids:      list[str] = []
    candidate_payloads: list[dict] = []

    with client.start_run("hirax.detection") as run:
        t_start = time.monotonic()
        for dish_id in triggered_dishes:
            payload = _candidate_payload(dish_id)
            candidate_payloads.append(payload)
            cid = run.submit_candidate(
                beam_id=dish_id,
                stage="imaging",
                payload=payload,
            )
            candidate_ids.append(cid)
            pm.record_candidate(beam_id=dish_id, stage="imaging")
            log.info("  candidate %s  dish=%-3d  DM=%.1f  SNR=%.1f  loc=%.4f deg²",
                     cid[:8], dish_id, payload["dm"], payload["snr"],
                     payload["localisation_area_deg2"])

        event_payload = _event_payload(candidate_payloads)
        provenance = [
            {"source_candidate_id": cid, "weight": round(random.uniform(0.7, 1.0), 3),
             "association_method": "visibility_correlation"}
            for cid in candidate_ids
        ]
        event_id, action_span_ids = run.report_event(
            candidate_ids=candidate_ids,
            provenance_edges=provenance,
            payload=event_payload,
        )
        pm.record_event(event_payload["classification"], time.monotonic() - t_start)
        log.info("Event %s  DM=%.1f  loc=%.4f deg²",
                 event_id[:8], event_payload["best_dm"],
                 event_payload["best_localisation_area_deg2"])

        def _action(ok_prob, ok_result, error_msg):
            failed = random.random() > ok_prob
            return {**ok_result, "status": "failure" if failed else "success",
                    "error": error_msg if failed else ""}

        action_results = [
            _action(0.75, {
                "action": "data_dump",
                "output_uri": f"file:///data/hirax/{event_id}/visibilities.h5",
            }, "HDF5 write error: file system quota exceeded on /data/hirax"),
            _action(0.80, {
                "action": "voevent_alert",
                "ivorn": f"ivo://hirax.ac.za/frb#{event_id}",
            }, "alert broker timeout after 30s: ivo://hirax.ac.za"),
            _action(0.90, {
                "action": "db_write",
                "table": "hirax_events",
                "record_id": event_id,
            }, "deadlock detected: transaction aborted"),
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
    log.info("HIRAX mock pipeline started  gateway=%s", GATEWAY_ADDR)

    while True:
        try:
            event_id, candidate_ids = run_detection(client, pm)
            time.sleep(random.uniform(2.0, 5.0))
            with client.start_run("hirax.diagnostic_plot") as run:
                run.update_event(event_id, patch={
                    "diagnostic_plot_uri": f"file:///plots/hirax/{event_id}/dirty_map.png",
                    "localisation_plot_uri": f"file:///plots/hirax/{event_id}/localisation.png",
                    "plot_generated_at_mjd": round(_now_mjd(), 6),
                })
                log.info("Plots attached to event %s", event_id[:8])
        except Exception:
            log.exception("Pipeline run failed")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
