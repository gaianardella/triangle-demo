# triangulation — machine-readable summary

Compact reference for tooling and coding agents. Source of truth for
behaviour is the code; this file is a structured index.

## Purpose

TDOA acoustic source localisation pipeline. Consumes per-drone
detection JSON (each row = one drone hearing one event), groups by
scenario, localises the source on a 2-D local plane, and emits a
sibling JSON with the source coordinates plus a Monte-Carlo
confidence cloud built from per-drone error fields already present
in the input.

No ground-truth source position is assumed at any stage. The
confidence cloud is derived from perturbations of the input
timestamps and drone positions.

## Entry points

| Form | Command / API |
|---|---|
| CLI pipeline | `python -m triangulation.locate --in <events.json> --out <localizations.json>` |
| CLI viewer | `python -m triangulation.viewer <localizations.json>` — opens http://127.0.0.1:8060/ |
| Library, one group | `triangulation.locate.localize_scenario(group_rows, *, mc_samples, confidence, cloud_format, rng)` |
| Library, whole file | `triangulation.locate.run(events_path, out_path, *, ...)` |

## Module map

| Path | Role |
|---|---|
| `triangulation/__init__.py` | package marker; exports `locate`, `viewer` names |
| `triangulation/locate.py` | CLI + pipeline orchestration; grouping, filtering, projection, MC, JSON write |
| `triangulation/projection.py` | equirectangular lat/lon ↔ local-plane (metres). Valid <~2 km |
| `triangulation/viewer.py` | Dash + Plotly OpenStreetMap viewer; scenario dropdown, no recomputation |
| `triangulation/core/io.py` | `relative_times(events, ts_field)` — ns-safe time conversion |
| `triangulation/core/solver.py` | `localize`, `localize_fast`; speed-of-sound `C = 343.0` |
| `triangulation/core/uncertainty.py` | `mc_confidence` (per-drone σ), `ellipse_xy`, `ellipse_axes` |

## Input contract (`events.json`)

Flat JSON list. Required fields per row consumed by this package:

- `path` (str) — scenario identifier; rows sharing a `path` form one event group
- `drone_id` (str) — unique within a group
- `event_time_ns` (int) — per-drone time of arrival, nanoseconds
- `position` (object) — `{lat: float, lon: float, alt_m: number?}`
- `relevant` (bool) — `false` rows cause the group to be skipped
- `time_prediction_error_ms` (float) — per-drone clock σ in ms; treated as σ_t = ms/1000 in seconds
- `position_error_m` (float) — per-drone drone-position σ in metres
- `label`, `label_human` (str | null) — pass-through to output
- `timestamp_ns` (int) — pass-through as `event_timestamp_ns` in output

Other fields in `events.json` (e.g. `confidence`, `window_counts`,
`toa_offset_ns`, `bearing`, `path`) are ignored by this package.

## Skip conditions (per group)

A group is skipped, with a one-line message, when any of:

- any row has `relevant != true`
- fewer than 3 distinct `drone_id`s
- any row missing `event_time_ns` or `position`
- the solver raises (caught, logged as `error: <msg>`)

## Output contract (`localizations.json`)

Flat JSON list, one entry per localised group. Field reference:

| Field | Type | Notes |
|---|---|---|
| `scenario` | str | basename of `path` |
| `label`, `label_human` | str \| null | from input |
| `event_timestamp_ns` | int | `timestamp_ns` of the first row |
| `drone_ids` | list[str] | sorted unique ids |
| `drones_used[]` | list[obj] | `{drone_id, lat, lon, event_time_ns, sigma_t_ms, sigma_pos_m}` |
| `source.lat`, `source.lon` | float | predicted source on WGS84 |
| `source.x_m_local`, `source.y_m_local` | float | same point in local-plane metres |
| `source.origin_lat`, `source.origin_lon` | float | projection origin = drone centroid |
| `cep50_m` | float | 50th-percentile MC radius around the mean |
| `cep95_m_approx` | float | ≈ `cep50_m * 2.08` (Rayleigh hint) |
| `zone_area_m2` | float | π · major · minor at `cloud_confidence` |
| `gdop` | float | ratio major/minor; ≥1 |
| `localization_confidence` | float | `1 / (1 + cep50_m / 25)` ∈ (0, 1] |
| `bearing_from_first_drone_deg` | float | clockwise from north |
| `distance_from_first_drone_m` | float | slant range in local plane |
| `cloud_format` | str | `ellipse` (default), `hull`, or `samples` |
| `cloud_confidence` | float | `0.95` by default |
| `cloud_latlon[]` | list[{lat, lon}] | closed polygon (last == first repeated) |
| `cloud_xy_local[]` | list[[x, y]] | same polygon in local-plane metres |
| `input_errors` | obj | `time_ms_max`, `position_m_max`, `time_s_per_drone[]`, `position_m_per_drone[]` |

## Algorithm (per group)

1. Project drone lat/lon to local plane with origin = drone centroid (equirectangular).
2. Build `dd_meas` from per-drone `event_time_ns` relative to the reference drone, × speed of sound.
3. Coarse grid (120 × 120, auto bbox = ±2 × drone spread) → argmin SSR → Levenberg-Marquardt refine.
4. Monte-Carlo (default n = 400) per draw:
   - timestamp[i] += N(0, σ_t[i] · 1e9) ns, where σ_t[i] = `time_prediction_error_ms` / 1000
   - if any σ_pos[i] > 0: drone position[i] += N(0, σ_pos[i] · I₂)
   - relocalise via `localize_fast` (LM only, seeded at the deterministic estimate)
5. Compute cloud mean and covariance; CEP50 = median of distances to mean.
6. Fit 95% (configurable) ellipse via χ² quantile on the eigendecomposition of the covariance.
7. Project estimate and ellipse polygon back to lat/lon for `cloud_latlon`.

## Constants / configurable knobs

| Location | Symbol | Default |
|---|---|---|
| `core/solver.py` | `C` | `343.0` m/s |
| `core/solver.py::localize` | `grid` | `120` |
| `core/uncertainty.py::mc_confidence` | `n` | `400` |
| `locate.py` | `POSITION_ERROR_FIELD` | `"position_error_m"` |
| `locate.py` | `TIME_ERROR_FIELD_MS` | `"time_prediction_error_ms"` |
| `locate.py::_confidence_score` | `scale_m` | `25.0` (sets the CEP50 at which confidence = 0.5) |
| `locate.py` CLI | `--cloud-format` | `ellipse` |
| `locate.py` CLI | `--confidence` | `0.95` |
| `locate.py` CLI | `--mc-samples` | `400` |

## Coordinate conventions

- Local plane: +x = east, +y = north, metres.
- Lat/lon: WGS84 decimal degrees.
- Equirectangular projection accurate to ~cm over ≤2 km; do NOT use over tens of km — switch to UTM.

## Dependencies

- `triangulation.locate` and `triangulation.core.*` — `numpy`, `scipy`
- `triangulation.viewer` — `dash`, `plotly` (only; the pipeline runs without these)

## Determinism

Both `run()` and `localize_scenario()` accept an `rng` parameter and
default to `np.random.default_rng(7)` for reproducibility across
invocations on the same input. Change the seed to vary the MC cloud
realisation.

## Out of scope (for the avoidance of doubt)

- No ground-truth comparison or RMSE; the pipeline never sees a truth track.
- No streaming / websocket integration; output is a static JSON file.
- No 3-D localisation; altitudes are read but discarded.
- The viewer does not re-run the MC interactively — re-invoke
  `locate.py` with `--mc-samples` / `--confidence` to change those.

## Provenance

Algorithm prototyped in `/Users/tuomastalasmaa/PycharmProjects/defensehackathon`
(left untouched as historical reference). Canonical copy for this
repo lives at `triangulation/core/`.
