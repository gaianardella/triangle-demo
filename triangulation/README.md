 # `triangulation/` — TDOA acoustic source localisation

Takes the per-drone detection JSON produced by `detection/`, runs
time-difference-of-arrival (TDOA) on every relevant scenario, and
writes a sibling `localizations.json` containing source coordinates +
a 95% Monte-Carlo confidence cloud + CEP statistics.

The pipeline never knows or assumes a true source position. The
confidence cloud is built by perturbing the input timestamps and
drone positions using the per-drone `time_prediction_error_ms` and
`position_error_m` fields already present in each event.

## Install

```bash
pip install numpy scipy        # required for the pipeline
pip install dash plotly         # required for the viewer only
```

## Run the pipeline

```bash
python -m triangulation.locate \
  --in  detection/output/events.json \
  --out detection/output/localizations.json \
  --pretty
```

Options:

| flag | default | meaning |
|---|---|---|
| `--in` | `detection/output/events.json` | input detection JSON |
| `--out` | `detection/output/localizations.json` | output JSON |
| `--mc-samples N` | `400` | Monte-Carlo sample count |
| `--confidence X` | `0.95` | confidence level for the cloud |
| `--cloud-format` | `ellipse` | `ellipse` / `hull` / `samples` |
| `--pretty` | off | pretty-print the output |
| `--quiet` | off | suppress per-scenario log |

## Run the viewer

```bash
python -m triangulation.viewer detection/output/localizations.json
# opens http://127.0.0.1:8060/
```

A scenario dropdown switches between localised events. Each shows
drones, the source estimate, and the 95% cloud on an OpenStreetMap
basemap (no API key needed). Side panel gives CEP50 / GDOP / zone
area and the per-drone σ values that fed into the MC.

## Input schema (`events.json`)

Flat list of per-(scenario × drone) detections. The fields the
pipeline reads:

```json
{
  "path":                     "data/scenarios/<scene>.wav",
  "label":                    "gunshot",
  "label_human":              "Gunfire",
  "relevant":                 true,
  "timestamp_ns":             1778935184893934848,
  "drone_id":                 "drone_1",
  "position": {"lat": 62.412, "lon": 25.748, "alt_m": 42},
  "event_time_ns":            1778935184893934848,
  "time_prediction_error_ms": 6.6,
  "position_error_m":         11.8
}
```

A "scenario" is the group of all events sharing the same `path`.
Scenarios are skipped (with a one-line log message) unless:

- every row in the group has `relevant: true`
- there are detections from at least 3 distinct `drone_id`s
- every row carries `event_time_ns` and `position`

`time_prediction_error_ms` is treated as the per-drone clock-σ
(divided by 1000 → seconds). `position_error_m` is treated as the
per-drone σ for drone position uncertainty. Both can be 0 — in which
case that source of MC noise is switched off for that drone.

## Output schema (`localizations.json`)

```json
[
  {
    "scenario": "scenario_gunshot_mix.wav",
    "label": "gunshot",
    "label_human": "Gunfire",
    "event_timestamp_ns": 1778935184893934848,
    "drone_ids": ["drone_1", "drone_2", "drone_3"],
    "drones_used": [
      {"drone_id": "drone_1", "lat": 62.412, "lon": 25.748,
       "event_time_ns": 1778935184893934848,
       "sigma_t_ms": 6.6, "sigma_pos_m": 11.8},
      ...
    ],
    "source": {
      "lat": 62.4100, "lon": 25.7500,
      "x_m_local": -135.11, "y_m_local": -184.56,
      "origin_lat": 62.4117, "origin_lon": 25.7527
    },
    "cep50_m": 18.73,
    "cep95_m_approx": 38.96,
    "zone_area_m2": 4224.0,
    "gdop": 2.30,
    "localization_confidence": 0.572,
    "bearing_from_first_drone_deg": 154.6,
    "distance_from_first_drone_m": 245.3,
    "cloud_format": "ellipse",
    "cloud_confidence": 0.95,
    "cloud_latlon": [{"lat": ..., "lon": ...}, ...],
    "cloud_xy_local": [[x, y], ...],
    "input_errors": {
      "time_ms_max": 9.8,
      "position_m_max": 15.6,
      "time_s_per_drone": [...],
      "position_m_per_drone": [...]
    }
  }
]
```

Coordinate system notes:

- `source.lat / lon` is the predicted source on the WGS84 globe.
- `source.x_m_local / y_m_local` is the same point in metres on a
  local equirectangular plane whose origin (`origin_lat`,
  `origin_lon`) is the centroid of the three drone positions for
  that scenario. The local plane has +x = east, +y = north.
- Two parallel cloud representations are emitted because consumers
  may want either: `cloud_latlon` (drop onto a real map) or
  `cloud_xy_local` (plot in a metric reference frame).

### Accuracy metrics explained

**`cep50_m`** — Circular Error Probable at 50%. The radius of a circle
centred on the source estimate within which 50% of the Monte-Carlo
samples landed. This is the primary accuracy number: there is a 50%
chance the real source lies within this many metres of the predicted
point.

**`cep95_m_approx`** — Same idea at 95% confidence, approximated as
`cep50 × 2.08` (Rayleigh distribution hint). There is a ~95% chance
the real source is within this radius.

**`zone_area_m2`** — Area of the 95% confidence ellipse in square
metres (π × major × minor). Gives a sense of the total search area
implied by the uncertainty.

**`gdop`** — Geometric Dilution of Precision. Ratio of the ellipse's
major axis to its minor axis (always ≥ 1). A value of 1.0 means the
uncertainty is a perfect circle; higher values mean the error is
stretched preferentially in one direction. GDOP is driven by the
geometry of the drone formation relative to the source — drones
spread evenly around the source give low GDOP; drones clustered on
one side give high GDOP.

**`localization_confidence`** — A single 0–1 score computed as
`1 / (1 + CEP50/25m)`. At 25 m CEP50 the score is 0.5; sub-metre
approaches 1.0; 100 m gives ~0.2. Tune the 25 m scale constant at
the top of `locate.py` if a different curve suits your use case.

**`bearing_from_first_drone_deg`** — Direction from the
lexicographically first drone to the estimated source, clockwise from
north (0° = north, 90° = east, 180° = south).

**`distance_from_first_drone_m`** — Slant distance in the local plane
from that same drone to the source estimate, in metres.

**`cloud_latlon` / `cloud_xy_local`** — 72-point closed polygon
tracing the confidence ellipse (last point repeats the first).
`cloud_latlon` is ready to drop onto any map as a polygon layer;
`cloud_xy_local` is the same shape in local-plane metres for metric
calculations. The ellipse level is set by `--confidence` (default
0.95).

## Layout

```
triangulation/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── io.py              # ns-safe time conversion
│   ├── solver.py          # grid-init + LM TDOA localizer
│   └── uncertainty.py     # Monte-Carlo cloud with per-drone σ
├── projection.py          # lat/lon ↔ local-plane metres
├── locate.py              # CLI pipeline: events.json → localizations.json
├── viewer.py              # standalone Dash viewer for the output
└── README.md              # this file
```

## How the maths works (one paragraph)

Three drones hear the same impulse at slightly different times. Each
pairwise time difference defines a hyperbola of constant
distance-difference on the ground; the source lies where the three
hyperbolas meet. The solver does a coarse grid search (to avoid local
minima from the hyperbola branch ambiguity) followed by a
Levenberg-Marquardt refinement. With exactly three drones the system
is exactly determined and the LS fit returns zero residual, so the
confidence cloud cannot be read off the fit — it has to come from a
Monte-Carlo over the input uncertainty. Each MC draw perturbs every
drone's timestamp by Normal(0, σ_t[i]) and (when enabled) its
position by Normal(0, σ_pos[i]·𝐈), relocalises, and stacks the
result; the 50th-percentile radius of the resulting cloud is CEP50,
and a 95% ellipse fitted to the same cloud is the confidence zone
reported back in `cloud_latlon`.

## Algorithm origin / acknowledgement

The TDOA core was prototyped in
`/Users/tuomastalasmaa/PycharmProjects/defensehackathon` and vendored
into this repo. The vendored copy in `triangulation/core/` is the
canonical version for this project — the defensehackathon copy is
left untouched as historical reference.
