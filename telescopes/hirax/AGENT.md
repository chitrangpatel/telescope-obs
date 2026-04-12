# telescopes/hirax/ — Agent Context

Mock HIRAX (Hydrogen Intensity and Real-time Analysis eXperiment) pipeline.
256-dish interferometric array, 400–800 MHz, optimised for FRBs and 21cm cosmology.

## Instrument parameters

- Frequency: 400–800 MHz (same range as CHIME, different beam geometry)
- Dishes: 32 (subset of real 256-dish array)
- Scan interval: 10 seconds (env `SCAN_INTERVAL`)
- Service name: `telescope-hirax-pipeline`

## Payload schema (interferometric, vs CHIME's formed-beam)

Candidate payload: `dm`, `snr`, `arrival_time_mjd`, `pulse_width_ms`, `ra_deg`,
`dec_deg`, `localisation_area_deg2`, `baseline_coverage`, `dish_id`

Adds imaging-specific fields (`ra/dec` localisation, `localisation_area_deg2`,
`baseline_coverage`) that CHIME's formed-beam approach doesn't produce.

## Pipeline structure

Same 4-phase structure as CHIME (candidates → event → actions → diagnostic plot run).
See `telescopes/AGENT.md` for the shared pattern.
