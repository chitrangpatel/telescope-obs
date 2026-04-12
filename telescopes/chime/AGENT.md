# telescopes/chime/ — Agent Context

Mock CHIME (Canadian Hydrogen Intensity Mapping Experiment) pipeline.

## Instrument parameters

- Frequency: 400–800 MHz (center 600 MHz, bandwidth 400 MHz)
- Beams: 16 (subset of real 1024-beam array)
- Scan interval: 8 seconds (env `SCAN_INTERVAL`)
- Service name: `telescope-chime-pipeline`

## Detection cycle (`run_detection`)

Each cycle picks 2–5 random beams and:
1. Submits one `BeamCandidate` per beam with CHIME-specific payload:
   `dm`, `dm_uncertainty`, `snr`, `rfi_score`, `arrival_time_mjd`, `pulse_width_ms`,
   `center_freq_mhz`, `bandwidth_mhz`, `beam_id`, `sub_band`
2. Clusters all candidates into one `AstrophysicalEvent` with payload:
   `classification` (always `"frb_candidate"`), `classification_confidence`,
   `clustering_algorithm` (`"dbscan"`), `best_dm`, `best_snr`, `dm_spread`, `num_sub_bands`
3. Reports 3 downstream actions with simulated success probabilities:
   - `data_dump` (65% success) — writes to `file:///data/chime/<event_id>/raw_voltages.npz`
   - `voevent_alert` (80% success) — posts to CHIME VOEvent broker
   - `db_write` (90% success) — inserts into `chime_events` table

## Diagnostic plot pipeline (`run_diagnostic_plot`)

Runs 1.5–4s after detection (simulating data collection delay).
Creates a **new trace** span-linked to the detection trace via the gateway's
`event_traces` table. Updates the event with `diagnostic_plot_uri` and each
candidate with a `dedispersed.png` URI.
