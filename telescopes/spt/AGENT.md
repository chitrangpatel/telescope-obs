# telescopes/spt/ — Agent Context

Mock SPT (South Pole Telescope) pipeline. 10-metre CMB telescope, 95/150/220 GHz.

## Instrument parameters

- Bands: 95, 150, 220 GHz
- Scan interval: 12 seconds (env `SCAN_INTERVAL`)
- Service name: `telescope-spt-pipeline`

## Payload schema (different from CHIME)

SPT detects CMB transients and galaxy clusters, not FRBs. Candidate payload:
`delta_t_uK`, `delta_t_uncertainty_uK`, `significance_sigma`, `ra_deg`, `dec_deg`,
`pointing_uncertainty_arcmin`, `band_ghz`, `detector_id`

This demonstrates that the telescope-agnostic schema works: same client library,
completely different science fields stored as opaque JSONB in the gateway.

## Pipeline structure

Same 4-phase structure as CHIME (candidates → event → actions → diagnostic plot run).
See `telescopes/AGENT.md` for the shared pattern.
