"""
telescope_obs.metrics
──────────────────────
Two metric classes for telescope pipeline services:

  InfraMetrics    — CPU, RAM, disk gauges polled every OTel export interval.
                    Uses psutil; requires no threading — the SDK polls the
                    callbacks on its own schedule.

  PipelineMetrics — Science pipeline counters and histograms (candidates,
                    events, detection duration, action errors).  Call the
                    record_* methods from your pipeline loops.

Both export via the global MeterProvider set up by setup_otel().
Metrics flow: OTLP → OTel collector → Prometheus → Grafana.

Grafana note: all metrics carry a `telescope` label so dashboards can
panel-repeat across CHIME / SPT / HIRAX without duplication.
"""

import psutil
from opentelemetry import metrics
from opentelemetry.metrics import Observation


class InfraMetrics:
    """
    Node-level infrastructure gauges reported as OTel observable metrics.

    Gauges are registered once at construction; the SDK calls the
    callbacks each export interval (default 15 s) — no background thread
    needed.

    Args:
        telescope:  Telescope identifier, used as the `telescope` label.
        data_path:  Filesystem path to monitor for storage metrics.
                    Defaults to "/" (root volume inside the container).
    """

    def __init__(self, telescope: str, data_path: str = "/") -> None:
        self._attrs     = {"telescope": telescope}
        self._data_path = data_path
        meter = metrics.get_meter("telescope_obs.infra")

        meter.create_observable_gauge(
            name="telescope.node.cpu_percent",
            callbacks=[self._cpu],
            description="Node CPU utilisation averaged across all cores",
            unit="%",
        )
        meter.create_observable_gauge(
            name="telescope.node.memory_used_bytes",
            callbacks=[self._mem_used],
            description="Node physical memory in use",
            unit="By",
        )
        meter.create_observable_gauge(
            name="telescope.node.memory_available_bytes",
            callbacks=[self._mem_avail],
            description="Node physical memory available (free + reclaimable)",
            unit="By",
        )
        meter.create_observable_gauge(
            name="telescope.node.disk_used_bytes",
            callbacks=[self._disk_used],
            description="Data volume used space",
            unit="By",
        )
        meter.create_observable_gauge(
            name="telescope.node.disk_free_bytes",
            callbacks=[self._disk_free],
            description="Data volume free space",
            unit="By",
        )
        meter.create_observable_gauge(
            name="telescope.node.disk_used_percent",
            callbacks=[self._disk_pct],
            description="Data volume utilisation",
            unit="%",
        )

    # ── Callbacks ─────────────────────────────────────────────────
    # Each receives a CallbackOptions (unused here) and yields Observations.

    def _cpu(self, _options):
        # interval=None returns the value computed since the last call,
        # which is accurate enough at a 15-s export cadence.
        yield Observation(psutil.cpu_percent(interval=None), self._attrs)

    def _mem_used(self, _options):
        yield Observation(psutil.virtual_memory().used, self._attrs)

    def _mem_avail(self, _options):
        yield Observation(psutil.virtual_memory().available, self._attrs)

    def _disk_used(self, _options):
        yield Observation(psutil.disk_usage(self._data_path).used, self._attrs)

    def _disk_free(self, _options):
        yield Observation(psutil.disk_usage(self._data_path).free, self._attrs)

    def _disk_pct(self, _options):
        yield Observation(psutil.disk_usage(self._data_path).percent, self._attrs)


class PipelineMetrics:
    """
    Science pipeline throughput counters and timing histograms.

    Use alongside InfraMetrics to correlate infrastructure load with
    science pipeline activity in Grafana.

    Example
    -------
    pm = PipelineMetrics(telescope="chime")

    # in your detection loop:
    pm.record_candidate(beam_id=42, stage="pipeline")
    pm.record_event(classification="frb_candidate", duration_s=0.12)
    pm.record_action(action="data_dump", status="success")
    """

    def __init__(self, telescope: str) -> None:
        self._telescope = telescope
        meter = metrics.get_meter("telescope_obs.pipeline")

        self._candidates = meter.create_counter(
            name="telescope.pipeline.candidates_total",
            description="Beam candidates submitted to the gateway",
        )
        self._events = meter.create_counter(
            name="telescope.pipeline.events_total",
            description="Astrophysical events formed and reported",
        )
        self._duration = meter.create_histogram(
            name="telescope.pipeline.detection_duration_seconds",
            description="Wall time from first candidate to event formed",
            unit="s",
        )
        self._actions = meter.create_counter(
            name="telescope.pipeline.actions_total",
            description="Downstream action results reported (by action and status)",
        )

    def record_candidate(self, beam_id: int, stage: str = "") -> None:
        self._candidates.add(1, {
            "telescope": self._telescope,
            "beam_id":   str(beam_id),
            "stage":     stage,
        })

    def record_event(self, classification: str, duration_s: float) -> None:
        attrs = {"telescope": self._telescope, "classification": classification}
        self._events.add(1, attrs)
        self._duration.record(duration_s, attrs)

    def record_action(self, action: str, status: str) -> None:
        self._actions.add(1, {
            "telescope": self._telescope,
            "action":    action,
            "status":    status,
        })
