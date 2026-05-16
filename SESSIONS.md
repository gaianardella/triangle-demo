# Implementation Sessions — Defense Hackathon

Unified reference — all 18 sessions in one place, in order.

---

## Common architecture

### What already exists (don't recreate)

- **`triangulation/locate.py`** runs the pipeline. Reads
  `detection/output/events.json`, writes
  `detection/output/localizations.json`. Each entry has a `source`,
  `cep50_m`, `gdop`, `zone_area_m2`, `localization_confidence`,
  `cloud_latlon`, etc.
- **`triangulation/core/`** is the algorithm layer. Per-drone σ MC is
  already implemented in `uncertainty.mc_confidence`.
- **`ui/index.html`** is the tactical map. Single-file canvas + DOM
  overlay. Has a four-phase playback engine
  (`transit → listen → localize → hold`) defined by `PHASE_ORDER`,
  `PHASE_MS`, `PHASE_LABEL`. `buildFrames(entries)` turns
  `localizations.json` rows into playable frames.
- **`triangulation/viewer.py`** is the engineering Dash viewer. **Do
  not touch it for demo work** — it's for validating the JSON, not
  pitching.

### Conventions

- **Backend writes JSON; frontend reads JSON.** No new IPC. The
  contract `localizations.json` already exists; sessions extend the
  schema by adding fields, never by removing or renaming.
- **All new pipeline fields are additive.** A consumer that ignores the
  new field still works.
- **Phases of the demo timeline are first-class.** Adding a new step
  to the kill chain means adding to `PHASE_ORDER`, `PHASE_MS`,
  `PHASE_LABEL` in `ui/index.html` and a branch in `tickPlayback(dt)`.
  This is the canonical extension point.
- **Animation reads, doesn't compute.** The frontend must not re-derive
  CEP50, confidence, or any policy decision. It reads them from the JSON.
- **One canonical localizations.json.** If we need pre-baked variants
  (clean vs jammed), they live under different filenames in the same
  directory; the UI gets a button to swap. Never two files of the same
  name at different paths.
- **Coordinate system in the UI is normalized 0–1.** The
  `latLonToNorm(lat, lon, bounds)` helper does the conversion; never
  hand-roll it.

### Visual / styling conventions (already established in CSS vars)

| Token | Use |
|---|---|
| `--accent` (`#4fd87a` green) | friendly UAV, OK status, sonar pings |
| `--warn` (`#e8a838` amber) | degraded mode, jamming, low confidence |
| `--hostile` (`#e85c4a` red) | classified hostile, cloud, target pin |
| `--text-dim` (`#6a9a78`) | secondary text, log non-events |

Reuse these. Do not introduce new palette colours unless an entirely
new entity class is added (and even then, prefer to reuse + opacity).

---

---

## Session 1 — ROE Policy Engine (Backend)

### Goal

Every entry in `localizations.json` carries a `recommended_action` and
the policy that produced it. Adds MGRS grid coords for operator
realism. Pure Python, no UI changes.

### Files touched

- New: `triangulation/policy.py`
- Modified: `triangulation/locate.py` (call `policy.decide`, emit new
  fields)
- Modified: `requirements.txt` if needed (`mgrs` optional, see below)
- Tests: re-run pipeline on existing `events.json`, schema check

### Architecture

```
locate.py::localize_scenario()
        │
        ├── (existing) compute estimate, MC cloud, CEP50, GDOP
        │
        ├── NEW: policy.decide(cep50_m, gdop, label,
        │                       localization_confidence)
        │            → {action, reason, severity, weapons_release_required}
        │
        ├── NEW: mgrs_from_latlon(source.lat, source.lon)
        │            → "35VML123456789" (or None if mgrs not installed)
        │
        └── (existing) write JSON entry, now with three new fields
```

`policy.py` is a pure module — no I/O, no random, just a `decide()`
function. This makes it unit-testable and trivially swappable.

### New JSON fields per entry

```json
{
  "recommended_action": "STRIKE",          // STRIKE | RECON | HOLD
  "recommended_action_reason": "CEP50 4.2m within strike envelope",
  "recommended_action_severity": "high",   // high | medium | low
  "weapons_release_required": true,        // STRIKE always true; RECON false
  "source_mgrs": "35VML123456789"          // null if mgrs missing
}
```

### Tasks

1. **Create `triangulation/policy.py`**
   - Define `Action = Literal["STRIKE", "RECON", "HOLD"]`
   - Define `@dataclass class Decision: action, reason, severity, weapons_release_required`
   - Implement `decide(cep50_m, gdop, label, confidence) -> Decision`
   - Logic skeleton (see thresholds below):
     - `HOLD` if `confidence < HOLD_CONFIDENCE_FLOOR` (truly unusable fix)
     - `STRIKE` if `cep50_m < STRIKE_CEP_MAX` AND `gdop < STRIKE_GDOP_MAX`
       AND `label in STRIKE_ELIGIBLE_LABELS`
     - `RECON` otherwise

2. **Wire into `locate.py`**
   - Import `policy.decide`
   - In `localize_scenario`, after MC, call it and add four fields to
     the output dict
   - Add `_mgrs_or_none(lat, lon)` helper that tries `import mgrs`,
     falls back to `None` gracefully

3. **Update `triangulation/__init__.py`** to export `policy`

4. **Update `AGENTS.md`** schema reference with the four new fields

5. **Regenerate `detection/output/localizations.json`** and verify

### Considerations

- **💡 NOTE: thresholds belong in `policy.py` as module-level
  constants**, not buried in `decide()`. The pitch may need to live-tune
  them.
- **💡 NOTE: `decide()` must remain a pure function.** No randomness, no
  I/O. Unit tests will rely on determinism.
- **💡 NOTE: don't gate on `confidence` alone.** Confidence is derived
  from CEP50, so gating on both is double-counting. Use CEP50 + GDOP +
  label class.
- mgrs library: optional dep. Don't crash if missing.

### ⚠ HUMAN INPUT NEEDED

1. **Threshold values** — Sonnet must ask before hardcoding:
   - `STRIKE_CEP_MAX` (suggested: 10 m)
   - `STRIKE_GDOP_MAX` (suggested: 3.0)
   - `HOLD_CONFIDENCE_FLOOR` (suggested: 0.10)
   - `STRIKE_ELIGIBLE_LABELS` (suggested: `["gunshot", "missile_launch", "tank"]`)
2. **Label severity mapping** — what counts as "high" vs "medium"
   severity? Suggested: `missile_launch / tank` = high; `gunshot` =
   medium; `drone` = low. Confirm.
3. **MGRS precision** — 10 m or 1 m grid square? Suggested: 10 m.

### Acceptance criteria

- `python -m triangulation.locate --pretty` runs without error.
- Every entry has `recommended_action`, `recommended_action_reason`,
  `recommended_action_severity`, `weapons_release_required`.
- `source_mgrs` present (string or null).
- `pytest triangulation/tests/test_policy.py` passes (test the
  threshold edges; create the test file).
- AGENTS.md updated to list new fields.

---

---

## Session 2 — Jamming Mode Support (Backend)

### Goal

The pipeline can emit a **paired pre-baked dataset** for the same
events: a clean variant and a jammed variant where one drone's
`position_error_m` is amplified. The UI later toggles between them.

### Files touched

- New: `triangulation/jam.py`
- Modified: `triangulation/locate.py` (CLI flags)
- Modified: `detection/output/` (new file: `localizations_jammed.json`)
- Tests: schema and value comparison between clean & jammed outputs

### Architecture

```
CLI: python -m triangulation.locate \
       --in detection/output/events.json \
       --out detection/output/localizations.json
     python -m triangulation.locate \
       --in detection/output/events.json \
       --out detection/output/localizations_jammed.json \
       --jam-drone drone_2 \
       --jam-position-mult 5.0 \
       --jam-time-mult 2.0
```

`triangulation/jam.py` is a tiny module: `apply_jamming(events, target_drone_id,
pos_mult, time_mult, label)` returns a new event list with that drone's
error fields scaled and a `jam_status` field added per row for UI display.

### New JSON fields (per scenario when --jam-* used)

```json
{
  "scenario_variant": "jammed-drone_2",      // null or "clean" when not jammed
  "jam_status_per_drone": {                  // present only in jammed variants
    "drone_1": "clean",
    "drone_2": "gps_jammed",
    "drone_3": "clean"
  }
}
```

### Tasks

1. **Create `triangulation/jam.py`**
   - `apply_jamming(events, target_drone_id, *, pos_mult, time_mult, jam_label)`
   - Walks events; for any row matching `target_drone_id`, multiplies
     its `position_error_m` and `time_prediction_error_ms` by the given
     factors
   - Returns the new event list (does not mutate input)

2. **Add CLI flags to `locate.py`**
   - `--jam-drone <id>` — drone to "jam"
   - `--jam-position-mult <float>` — default 5.0
   - `--jam-time-mult <float>` — default 1.0
   - `--jam-label <str>` — default `"gps_jammed"`

3. **Per-scenario `jam_status_per_drone`** in the output
   - Add to `localize_scenario` signature: `jammed_drone_ids: set[str]`
   - Build the per-drone dict from that set

4. **`scenario_variant` field** on each entry
   - Set from a CLI flag: `--variant-tag <str>` (default null)
   - Lets the UI know it's looking at a "clean" vs "jammed-drone_2" dataset

5. **Convenience script** `scripts/generate_demo_datasets.sh`:
   ```sh
   python -m triangulation.locate --out detection/output/localizations.json --variant-tag clean
   python -m triangulation.locate --out detection/output/localizations_jammed.json \
       --jam-drone drone_2 --jam-position-mult 5.0 --variant-tag jammed-drone_2
   ```

### Considerations

- **💡 NOTE: do not implement "live" jamming as a UI toggle that
  re-runs the pipeline.** Pre-bake the variants and serve them as
  static files. Live re-runs add latency and a Python dependency in
  the browser path.
- **💡 NOTE: jamming amplifies σ but the math still runs.** A jammed
  fix is not a *failed* fix; it's a *low-confidence* fix. The ROE
  engine downstream will pick that up via the larger CEP50.
- **💡 NOTE: jam ONE drone at a time for the demo.** Multi-drone
  jamming is a "future work" remark.

### ⚠ HUMAN INPUT NEEDED

1. **Which drone to jam in the demo?** Suggested: `drone_2` (middle of
   the formation, has biggest TDOA impact). Confirm.
2. **Jamming factor** — 5× position σ feels right. Confirm — or pick a
   value that makes CEP50 cross the STRIKE threshold cleanly (i.e.
   demonstrates the policy switch).
3. **Should the time error also be amplified?** Suggested: no (1.0×).
   GPS jamming primarily corrupts position. Time error is more about
   clock sync. Confirm to keep things conceptually clean.

### Acceptance criteria

- Two output files exist: `localizations.json` and
  `localizations_jammed.json`.
- The jammed variant has visibly larger `cep50_m` on every scenario.
- ROE recommendation flips from STRIKE → RECON for at least one
  scenario when jamming is applied (this is the demo moment).
- `scenario_variant` and `jam_status_per_drone` present on jammed
  entries, absent (or null) on clean entries.
- The convenience script produces both files in one invocation.

---

---

## Session 3 — Response Animation Phase (Frontend)

### Goal

After the existing `localize → hold` phases, add a `respond` phase that
animates a "response drone" arcing from the nearest sensor drone toward
the target pin. Visual differs by `recommended_action`: STRIKE = red
trail + impact flash; RECON = amber trail + circling pattern.

### Files touched

- Modified: `ui/index.html` only (single-file UI is the convention)

### Architecture

The existing phase engine in `index.html` is the right extension point:

```js
PHASE_ORDER = ["transit", "listen", "localize", "hold"]
PHASE_MS    = { transit: 4200, listen: 2200, localize: 2800, hold: 5200 }
PHASE_LABEL = { transit: "...", listen: "...", localize: "...", hold: "..." }
```

Add a new phase between `localize` and the (existing) `hold`:

```js
PHASE_ORDER = ["transit", "listen", "localize", "respond", "hold"]
PHASE_MS    = { ..., respond: 4500, hold: 3500 }   // shorten hold
PHASE_LABEL = { ..., respond: "WEAPONS / RECON" }
```

Then a new branch in `tickPlayback(dt)`:

```js
} else if (pb.phase === "respond") {
   // 1. find nearest sensor drone (already on map)
   // 2. interpolate a "responder" entity along an arc from drone → target
   // 3. render with action-specific style
   // 4. emit pulses; on action="STRIKE" final frame -> impact flash
}
```

The responder is a single new entity (id `responder-<scenario>`) drawn
via the same `upsertEntity()` mechanism — new icon in `ICONS` table.

### Tasks

1. **Add new icons to `ICONS` table**
   - `responder_strike` (red FPV / kamikaze drone silhouette)
   - `responder_recon` (amber sensor drone silhouette)
   - Reuse existing SVG style — small (28×28), match existing palette

2. **Extend phase machinery**
   - Append `"respond"` to `PHASE_ORDER` before `"hold"`
   - Add to `PHASE_MS` (suggest 4500 ms) and `PHASE_LABEL`
     ("WEAPONS / RECON")

3. **Implement `tickPlayback`'s `respond` branch**
   - Read `recommended_action` from `cur.entry`
   - Identify nearest sensor drone to the target (distance in
     normalized coords)
   - Compute arc waypoints: start at nearest drone, midpoint offset
     perpendicular by ~10% of map width, end at target
   - Interpolate position along arc by `t = phaseT / PHASE_MS.respond`
   - Render via `state.targets.push({id: "responder-...", ...})` OR a
     new state slot `state.responders[]` (preferred — see below)

4. **Add `state.responders[]` slot**
   - Why a new slot: responders are not targets. They have separate
     icons, separate styling, and shouldn't show up in legends as
     hostile.
   - Rendered in `renderEntities()` analogously to drones/targets

5. **STRIKE-specific effects** (when `recommended_action === "STRIKE"`)
   - On arrival (last 15% of phase): emit a red impact pulse
   - Briefly enlarge the target pin (scale 1.3× for 250ms)
   - Add log line: `"STRIKE · target engaged · ..."` at impact moment
   - On final 5% of phase: replace target icon with "neutralized"
     marker (faded out, grey)

6. **RECON-specific effects** (when `recommended_action === "RECON"`)
   - On arrival: orbit around the target pin (2-3 small loops)
   - Show a 'CAMERA ON' marker on the responder
   - Add log line: `"RECON · imagery acquired · target verified"`
   - Target stays visible, gets a yellow border (positive ID hint)

7. **HOLD effects** (when `recommended_action === "HOLD"`)
   - No responder dispatched. Phase still plays for symmetry but just
     shows a "STANDBY — INSUFFICIENT CONFIDENCE" banner
   - Shorter duration acceptable (skip to next scenario sooner)

8. **Action banner** above the map
   - A new overlay div in `.map-wrap` showing
     `ROE: STRIKE AUTHORIZED — RAVEN-1 → 62.41001, 25.75004`
   - Appears at the start of `respond`, fades out at the end of `hold`
   - Styled with action colour: STRIKE=red, RECON=amber, HOLD=grey

9. **Update the timeline UI**
   - `updateTimelineUI()` already shows phase + event progress —
     extend the readout so the operator sees the ROE outcome:
     `"FIX 4.2m → STRIKE"` next to the existing CEP readout

### Considerations

- **💡 NOTE: the responder is purely visual.** The math (decision,
  CEP, etc.) is already baked into `localizations.json`. Don't
  re-derive anything in the browser.
- **💡 NOTE: arc, not straight line.** Real drones don't fly in
  straight lines and the curve reads better visually. Use a quadratic
  Bezier with perpendicular offset.
- **💡 NOTE: prefer additive state.** Don't repurpose
  `state.targets`/`state.drones`. Add `state.responders` for clarity.
- The "neutralized" target swap (STRIKE) is what makes the scene
  feel like it concluded. Don't skip it.

### ⚠ HUMAN INPUT NEEDED

1. **Visual style of the responder** — Sonnet should generate a
   responder icon, then ask the user to confirm or paste a preferred
   SVG. (FPV-drone-with-warhead vs reconnaissance-quadcopter — your
   call.)
2. **Phase duration** — 4500 ms feels right for one-scenario demos but
   may be too slow for multi-scenario chains. Confirm.
3. **STRIKE iconography** — a literal "X over target" or a fade-to-grey
   "neutralized" treatment? Suggested: fade-to-grey. Confirm.
4. **Should the action banner be persistent (full hold + respond) or
   only during respond?** Suggested: appears at respond start, persists
   through hold.

### Acceptance criteria

- The five-phase playback runs end-to-end without flicker.
- For a STRIKE scenario: responder arcs from drone to target,
  impact flash, target neutralized icon, log line written.
- For a RECON scenario: responder arcs to target, orbits, target
  gets positive-ID border, log line written.
- For a HOLD scenario: no responder; banner says "STANDBY".
- Action banner shows the right colour/text per ROE action.
- 60 fps maintained (check `statFps` in footer).

---

---

## Session 4 — Recon Imagery + Telemetry Log (Frontend)

### Goal

When RECON is dispatched, a "camera feed" thumbnail pops in (placeholder
image with HUD chrome). At the same time, a structured telemetry log
streams synchronised with the phase: `ENGAGING → IMAGING → POSITIVE ID
→ STRIKE AUTHORIZED → IMPACT`.

### Files touched

- Modified: `ui/index.html`
- New: `ui/assets/recon-placeholder-1.jpg` and 2-3 more
- The placeholder JPGs can be sourced from public-domain aerial /
  thermal imagery, or hand-drawn schematics — anything that reads as
  "from a drone camera".

### Architecture

```
respond phase begins
   │
   ├── if action == STRIKE:
   │       telemetry: ENGAGING → ARMED → IMPACT
   │
   └── if action == RECON:
           telemetry: APPROACHING → IMAGING (popup) → ID CONFIRMED → REPORT
           imagery popup shown at t = 0.4 .. 0.95 of phase
```

The telemetry stream is just a scheduled list of log lines emitted via
`addLog()` at specific phase progress fractions:

```js
const TELEMETRY = {
  STRIKE: [
    { at: 0.05, msg: "Responder dispatched · weapons hot",   lvl: "warn" },
    { at: 0.50, msg: "Final approach · target locked",        lvl: "warn" },
    { at: 0.90, msg: "IMPACT · target neutralized",           lvl: "hostile" }
  ],
  RECON: [
    { at: 0.05, msg: "Recon dispatched · approach inbound",   lvl: "warn" },
    { at: 0.40, msg: "On-target · imaging now",               lvl: "warn" },
    { at: 0.70, msg: "POSITIVE ID · hostile combatant",       lvl: "hostile" },
    { at: 0.92, msg: "Report sent · awaiting authority",      lvl: "ok" }
  ],
  HOLD: [
    { at: 0.10, msg: "STANDBY · insufficient confidence",     lvl: "warn" },
    { at: 0.50, msg: "Repositioning swarm for next fix",      lvl: "ok" }
  ]
};
```

The imagery popup is a fixed-position `<div>` that animates in from
the right; container has HUD chrome (crosshair, "CAM 1", recording dot)
overlaid on the image.

### Tasks

1. **Add imagery popup DOM** to `index.html`
   - A `.recon-feed` container, hidden by default
   - Inside: `<img>` for the placeholder, plus overlay divs for
     crosshair, top-right "REC ●", bottom timestamp
   - CSS animation: slide in from right with a 200ms ease-out

2. **Add `TELEMETRY` schedule constant** as above

3. **Implement scheduler in `tickPlayback`'s respond branch**
   - Track which telemetry messages have been fired (`pb.telemetryFired:
     Set<int>`)
   - At each tick, check if `t > entry.at` and that index not yet fired

4. **Wire imagery popup show/hide**
   - On RECON respond start: pick an image from
     `ui/assets/recon-*.jpg` (round-robin by scenario index)
   - Show at `t = 0.4`, hide at end of `hold` phase
   - For STRIKE: do not show (the strike doesn't need recon imagery)

5. **Add `ui/assets/recon-*.jpg`** — 3-4 small (~600×450) placeholder
   images. Could be:
   - A satellite-style top-down green forest with a small red square
   - A thermal blob with crosshairs
   - A grainy aerial view

6. **Style the popup**
   - 320×240 px, positioned bottom-right of the map, above the footer
   - Border in `--accent` with HUD chrome
   - Subtle scan-line CSS overlay for "video feed" feel

### Considerations

- **💡 NOTE: imagery is decoration, not data.** Don't try to actually
  retrieve a real image based on coordinates. A fixed pool is fine.
- **💡 NOTE: telemetry timing is relative to phase, not wall clock.**
  Use `phaseT / PHASE_MS.respond` as the progress value.
- **💡 NOTE: don't fire the same telemetry twice.** Track fired indices.
- The popup must not block clicks anywhere on the map (use
  `pointer-events: none` on the wrapper, `auto` on a close button if
  added).

### ⚠ HUMAN INPUT NEEDED

1. **Imagery source.** Sonnet cannot ship real surveillance imagery.
   Either: user provides 3-4 placeholder images, OR Sonnet generates
   procedural SVG "fake camera views" inline. Suggested: ask for user
   to dump 3 images into `ui/assets/`, fall back to inline SVG if not.
2. **Telemetry copy/wording.** The strings above are first drafts —
   confirm tone and whether to use ALL-CAPS military style or normal
   sentence case. Suggested: ALL-CAPS short phrases for status, normal
   case for descriptive log lines.
3. **Sound effects?** Optional — out of scope unless the user pushes.

### Acceptance criteria

- During a RECON respond phase, the imagery popup slides in at
  `t ≈ 0.4` and closes when scenario advances.
- Telemetry log lines stream in at the right phase fractions, with
  correct severity colours.
- No telemetry line repeats within one phase.
- STRIKE scenarios never show the imagery popup.
- HOLD scenarios show only "STANDBY" telemetry.

---

---

## Session 5 — Multi-Threat Priority Stack (Backend + Frontend)

### Goal

When multiple scenarios are localised, the UI shows a ranked target
list (highest priority first). The swarm allocates response to the top
threat. Pipeline emits a numeric priority per scenario.

### Files touched

- Modified: `triangulation/policy.py` (add `priority(...)` function)
- Modified: `triangulation/locate.py` (emit `threat_priority` field)
- Modified: `ui/index.html` (add target stack panel + selection logic)

### Architecture

```
Backend
─────
policy.py::priority(label, recommended_action, cep50_m, severity)
   → integer (higher = more urgent)

   Default formula:
   base = SEVERITY_BASE[severity]              // high=100, med=50, low=20
   bonus = ACTION_BONUS[recommended_action]    // STRIKE=20, RECON=10, HOLD=0
   penalty = max(0, cep50_m - 10) * 0.3        // less confident → lower prio
   priority = base + bonus - penalty

Frontend
─────
A new left-rail panel: "TARGET STACK" listing scenarios sorted by
threat_priority descending, with current highlight on the one
currently in `pb.index`.

Each row shows: label, MGRS, CEP50, recommended action chip.
```

### Tasks

1. **Backend: `policy.py::priority()`**
   - Pure function as defined above
   - Constants at module top: `SEVERITY_BASE`, `ACTION_BONUS`

2. **Backend: emit `threat_priority`** in each `localizations.json`
   entry

3. **Backend: emit a sort hint** — `priority_rank` (0 = top) computed
   over the whole list before writing JSON. Trivial to compute in
   `run()`.

4. **Frontend: replace `Legend` panel with `Target stack` panel** OR
   add it as a new section above legend.
   - Read `state.frames`, sort by `entry.threat_priority` descending
   - Render compact rows, click to jump playback to that scenario

5. **Frontend: visual highlight of current scenario**
   - The row matching `state.playback.index` gets `--accent` border

6. **Frontend: action chip colours** in each row
   - STRIKE = red bg, RECON = amber bg, HOLD = grey bg

### Considerations

- **💡 NOTE: priority is computed once at pipeline time, not in the
  browser.** The browser must not re-rank scenarios — that would mean
  the math lives in two places.
- **💡 NOTE: priority_rank is for the UI's convenience.** A consumer
  that ignores rank can still re-sort by threat_priority.
- The target stack panel is what makes the demo feel like an
  *operator's* console rather than a single-event toy.

### ⚠ HUMAN INPUT NEEDED

1. **Severity-to-base mapping** — suggested `high=100, medium=50,
   low=20`. Confirm or override.
2. **Action bonus values** — confirm `STRIKE=20, RECON=10, HOLD=0`. The
   numbers don't matter individually, only their relative order; but
   they need to come from somewhere.
3. **What scenarios to demo simultaneously?** The current
   `events.json` has multiple scenarios at different timestamps. The
   target stack only makes sense if they're treated as "all active at
   once". Decide whether to:
   - (a) treat them as simultaneous (forced for demo)
   - (b) only show in the stack those within a sliding time window
     (more realistic but more complex)
   Suggested: (a). Confirm.

### Acceptance criteria

- Every entry has `threat_priority` (float) and `priority_rank` (int).
- Frontend renders a target-stack panel sorted by priority.
- Clicking a row jumps playback to that scenario.
- The current scenario is visually highlighted in the list.
- Action chips colour-coded correctly.

---

---

## Session 6 — Mesh Network Narrative (Docs)

### Goal

A one-page architecture diagram + page of prose explaining what
depends on the Kova mesh layer for this system to work end-to-end. No
code. Goes on a pitch slide.

### Files touched

- New: `docs/MESH_ARCHITECTURE.md`
- New: `docs/assets/mesh-dependencies.svg` (or mermaid in markdown)

### Architecture

The document has three sections:

1. **What this system does.** One paragraph plus a flow diagram.
2. **What it depends on the mesh for.** Three callouts, each with a
   data-rate / latency / criticality estimate:
   - Time-synced timestamps between drones (low data, low latency, high criticality)
   - Target coordinates + confidence (low data, low latency, medium criticality)
   - Recon imagery on the return path (high data, medium latency, low criticality)
3. **Graceful degradation matrix.** A 2D table: mesh-loss × GPS-loss →
   what the system can still do.

### Tasks

1. **Write the prose.** ~300-400 words. Honest about scope:
   "Our system is the application that needs a mesh; we did not build
   the mesh, but our design assumes the kind of resilience Kova's
   tactical mesh promises."

2. **Build the diagram.** Either:
   - Mermaid flowchart inline in markdown (preferred, version-controllable)
   - OR a hand-built SVG in `docs/assets/`

3. **Add the dependency table** — bandwidth/latency/criticality
   estimates with order-of-magnitude numbers (no precision).

4. **Add the degradation matrix.**

5. **Link from main README.md** to MESH_ARCHITECTURE.md.

### Considerations

- **💡 NOTE: don't oversell.** The point is to show this team
  understands what the mesh layer is for, not to claim mesh
  capability we didn't build.
- **💡 NOTE: mention multi-hop relay explicitly.** Drone_3 reaching
  the operator via drone_2 as a relay is what mesh *is*; that
  scenario should appear in the prose.

### ⚠ HUMAN INPUT NEEDED

1. **Tone.** Pitch-y or sober? Suggested: sober, technical, three
   pages max.
2. **Should this be its own slide deck (PDF)?** Suggested: no, just a
   markdown doc; the team can screenshot from it for slides.

### Acceptance criteria

- `docs/MESH_ARCHITECTURE.md` exists, ~300-400 words.
- Includes a system flow diagram (mermaid or SVG).
- Includes the dependency table with bandwidth/latency/criticality.
- Includes the degradation matrix.
- README.md links to it.

---

---

## Session 7 — Scenario sidebar + phase stepper

### Goal

Replace `tickPlayback`'s auto-advance with a step-based playback engine
driven by user clicks. Five scenarios live in a left sidebar; the
currently selected one drives the map. Phases advance only on
▶ NEXT (or auto-play toggle).

### Files touched

- Modified: `ui/index.html` only

### Architecture

Rework the state machine around a single source of truth: `state.step`:

```js
state = {
  ...,
  scenarioIndex: 0,        // which of 5 scenarios is active
  step: 0,                 // 0 = PATROL, 1 = DETECT, ..., 5 = COMPLETE
  stepProgress: 0,         // 0..1 within the current step's animation
  autoplay: false,         // if true, steps advance themselves
  scenarios: [...]         // 5 entries; each has frames-like data
}

PHASES = [
  { id: "patrol",    label: "PATROL · standby",         dur: 0 },
  { id: "detect",    label: "DETECT · audio event",     dur: 1500 },
  { id: "localize",  label: "LOCALIZE · TDOA cloud",    dur: 2200 },
  { id: "decide",    label: "DECIDE · ROE evaluation",  dur: 1000 },
  { id: "respond",   label: "RESPOND · action exec",    dur: 4500 },
  { id: "complete",  label: "COMPLETE · contact held",  dur: 0 },
]
```

`tickPlayback(dt)` becomes phase-aware: it animates *within* a phase
(progress 0→1) but **never advances to the next phase on its own**
unless `state.autoplay` is true. The whole transit-blend code goes
away; map view always centres on the current scenario's drone bounds.

### Tasks

1. **HTML scaffolding for the sidebar**
   - 1.1 Add a `<div class="scenarios">` panel inside the existing left
         column, above the legend. Five cards.
   - 1.2 Each card: scenario thumbnail (small SVG of geometry), label,
         live CEP50 readout, action chip.
   - 1.3 Click a card → `setScenario(i)`.

2. **HTML scaffolding for the step controls**
   - 2.1 Add a `<div class="phase-controls">` at the bottom of the map
         wrap, just above the existing `footer`.
   - 2.2 Buttons: `⏪ PREV`, `▶ NEXT`, `⏵ AUTO`, `⟲ RESET`.
   - 2.3 Disable PREV/NEXT at boundaries; AUTO toggles the autoplay
         boolean.

3. **State migration**
   - 3.1 Add `state.scenarios[]`. On `loadLocalizations()`, populate
         from the loaded JSON. Cap to 5 by default (or all if fewer).
   - 3.2 Drop the existing `state.playback` (transit / listen /
         localize / hold logic) and replace with the phase-step state.
   - 3.3 Migrate the existing visuals (cloud, drones, targets) into a
         phase-driven renderer.

4. **Phase renderers**
   - 4.1 `renderPatrol()` — drones in formation, gentle patrol wobble.
   - 4.2 `renderDetect()` — drones light yellow as audio arrives;
         emit pulses at each drone with a small timestamp label
         (`+0 ms`, `+145 ms`, …).
   - 4.3 `renderLocalize()` — cloud fades in (alpha grows 0→1), target
         pin lands.
   - 4.4 `renderDecide()` — ROE banner appears: red/amber/grey by
         action; text from `entry.recommended_action_reason`.
   - 4.5 `renderRespond()` — responder animation (existing Session 3
         work). Multi-drone variant for SEARCH (Session 9).
   - 4.6 `renderComplete()` — frozen final state; log summary.

5. **NEXT / PREV / AUTO logic**
   - 5.1 NEXT: snap to end of current phase (progress = 1) if not yet
         there; if already at 1, advance to next phase.
   - 5.2 PREV: snap to start of current phase if progress > 0;
         otherwise step back.
   - 5.3 AUTO: when on, advance to next phase the moment progress = 1.
   - 5.4 RESET: scenarioIndex unchanged; step = 0; progress = 0; clear
         transient state (cloud alpha, target pins).

6. **Sidebar live updates**
   - 6.1 Each card's CEP50 and action chip update when σ sliders change
         (Session 8 dependency — show defaults until then).
   - 6.2 Active card gets `--accent` border.
   - 6.3 Hovering a card shows a tooltip with the per-drone σ values.

7. **URL state (nice-to-have)**
   - 7.1 `?scenario=2&step=3&sigma_t=12&sigma_pos=20` deep-links a
         specific state. Useful for rehearsing the pitch.

### Considerations

- **💡 NOTE: the existing `transit` phase blend (camera pans between
  scenarios) is being deleted.** This is intentional — operator
  controls the map, not the playback. Don't try to preserve it.
- **💡 NOTE: scenarios array is bounded.** Limit to 5 in the UI; if
  the JSON has more, show the top 5 by `priority_rank`. Anything
  more clutters the sidebar.
- **💡 NOTE: phase durations are *animation* durations, not pacing.**
  The presenter advances at their own speed; durations only matter
  during the animation itself.
- **⚠ Keyboard shortcuts.** Add `→` for NEXT, `←` for PREV, `space`
  for AUTO toggle. Pitch flow uses keyboard, not mouse.

### ⚠ HUMAN INPUT NEEDED

1. **Which 5 scenarios?** The current `events.json` has scenarios like
   `scenario_gunshot_mix`, `scenario_gunshot_preprocessed`,
   `scenario_tank_preprocessed`, `scenario_missile_mix`. Suggested
   curated set:
   - ① clean gunshot (low σ_t, low σ_pos) → STRIKE
   - ② degraded-timing gunshot (mid σ_t, low σ_pos) → borderline
   - ③ GPS-denied gunshot (low σ_t, high σ_pos) → wide ellipse
   - ④ tank (high severity) → STRIKE even at mid CEP
   - ⑤ missile launch (high severity, high priority) → STRIKE
   The team probably needs to either select 5 from the existing
   `events.json` or generate 5 synthetic ones for clarity. Confirm.
2. **Card thumbnail style.** Mini topdown of drones + dot? Just a
   geometric symbol per geometry class? Suggested: a 60×60 SVG
   showing three drone dots + a marker dot at the source.
3. **Keyboard shortcuts.** Confirm `→ / ←` for NEXT/PREV.

### Acceptance criteria

- 5 scenario cards render in the sidebar.
- Clicking a card switches the map to that scenario at phase 0.
- ▶ NEXT advances one phase at a time; PREV steps back.
- ⏵ AUTO advances phases automatically with the dialled-in durations.
- ⟲ RESET returns the current scenario to phase 0.
- Each card's CEP50 + action chip reflect the scenario's defaults.

---

---

## Session 7B — Ambient & Event Audio (Frontend)

> Originally 'Session 7' in SESSIONS.md; renumbered 7B to avoid conflict
> with the phase-stepper Session 7 above.

### Goal

Play synchronised audio during the demo. Three layered channels:

1. **Forest ambient** — background crickets, birdsong and forest atmos at
   low volume, running continuously from the first frame.
2. **Drone patrol buzz** — looping UAV sound during `transit` and `listen`
   phases; cross-fades to silence as `localize` begins (acoustic silence
   sells the "we just found the source" moment).
3. **Event detonation** — plays the detected-event sound **once** at the
   moment the target pin appears (`localize`, `t = 0.4`); the specific
   clip is chosen by `entry.label`.

No external libraries. Use the browser **Web Audio API** only
(`AudioContext`, `GainNode`, `AudioBufferSourceNode`).

### Audio files

All paths are relative to the repo root and served by
`python3 -m http.server 8080`. The UI fetches them via relative URLs
(`../data/...` from `ui/index.html`).

| Role | File |
|---|---|
| Drone patrol (loop) | `data/samples/drone/uas_drone_pass_dcpoke.wav` |
| Event — tank | `data/samples/tank/kakaist-tank-moving-sfx-319878.mp3` |
| Event — gunshot | `data/samples/gunshot/demo_gunshot_128293.wav` |
| Event — missile_launch | `data/samples/missile_launch/ucas_launch_x47b_qubodup.flac` |
| Ambient — forest atmos | `data/samples/missile_launch/forest/730223_klankbeeld_forest-in-the-netherlands-320-pm-230328_572.wav` |
| Ambient — birds | `data/ESC-50/audio/1-100038-A-14.wav` |
| Ambient — crickets | `data/ESC-50/audio/1-57316-A-13.wav` |

**Drone clip must loop seamlessly** — set `AudioBufferSourceNode.loop = true`
and optionally set `loopStart`/`loopEnd` to trim any click at the tail.

**FLAC note**: `ucas_launch_x47b_qubodup.flac` plays natively in
Chromium/Chrome and Safari (macOS). Firefox on Windows may not decode it.
If the demo machine is Windows + Chrome, no action needed. If cross-browser
support is required, convert the FLAC to WAV offline before the demo:
```
ffmpeg -i ucas_launch_x47b_qubodup.flac ucas_launch_x47b_qubodup.wav
```

### Architecture

```
AudioEngine (singleton in index.html)
│
├── ctx          AudioContext (lazy-created on first user gesture)
│
├── ambientGain  GainNode  ← birds + crickets + forest atmos play here
│                            target volume: 0.18
│
├── droneGain    GainNode  ← looping drone clip
│                            fade in on transit/listen, out on localize
│                            target volume: 0.55 while active, 0 while silent
│
└── eventGain    GainNode  ← one-shot event sound
                             plays once per scenario at localize t=0.4
                             target volume: 1.0 (no fade — it should punch)
```

All three `GainNode`s connect to `ctx.destination`.

Volume changes use `gain.linearRampToValueAtTime()` with a 0.8 s ramp
so there are no clicks.

### Files touched

- Modified: `ui/index.html` only

### Tasks

1. **`AudioEngine` object** — add as a module-level singleton at the top
   of the `<script>` block:
   ```js
   const AudioEngine = {
     ctx: null,
     buffers: {},         // label → AudioBuffer
     ambientSources: [],  // running ambient source nodes
     droneSource: null,
     ambientGain: null,
     droneGain: null,
     eventGain: null,
   };
   ```

2. **`AudioEngine.init()`** — call once on first user interaction
   (attach to any existing button click; reuse the first `btnPause`,
   `btnDemo`, or `btnLocalize` listener):
   - `AudioEngine.ctx = new AudioContext()`
   - Create the three `GainNode`s and wire to `ctx.destination`
   - Fetch and decode **all** audio files via `fetchBuffer(url)`
   - Start the three ambient clips (birds, crickets, forest atmos) on
     `ambientGain` with `loop = true`
   - Start the drone clip on `droneGain` with `loop = true`,
     initial gain `0` (silent until transit starts)

3. **`fetchBuffer(url)`** helper:
   ```js
   async function fetchBuffer(url) {
     const res = await fetch(url);
     const ab  = await res.arrayBuffer();
     return AudioEngine.ctx.decodeAudioData(ab);
   }
   ```
   Wrap in try/catch; log a warning to the event log if a file 404s —
   don't crash the whole UI.

4. **Phase-linked volume changes** — hook into the existing
   `advancePlaybackPhase()` function (already called on each phase
   transition):
   ```js
   // In advancePlaybackPhase(), after pb.phase is updated:
   AudioEngine.onPhase(pb.phase, cur.entry.label);
   ```

   ```js
   AudioEngine.onPhase = function(phase, label) {
     if (!this.ctx) return;
     const now = this.ctx.currentTime;
     const RAMP = 0.8;   // seconds
     if (phase === "transit" || phase === "listen") {
       this.droneGain.gain.linearRampToValueAtTime(0.55, now + RAMP);
     } else {
       this.droneGain.gain.linearRampToValueAtTime(0.0, now + RAMP);
     }
   };
   ```

5. **Event sound trigger** — in the `localize` branch of
   `tickPlayback(dt)`, at the moment `pb.localizeLogged` transitions
   from false to true (same gate already used for the sonar pulse):
   ```js
   AudioEngine.playEvent(cur.entry.label);
   ```

   ```js
   AudioEngine.playEvent = function(label) {
     if (!this.ctx) return;
     const MAP = {
       gunshot:        "gunshot",
       missile_launch: "missile",
       tank:           "tank",
       drone:          "drone",   // hostile drone — reuse clip
     };
     const key = MAP[label];
     if (!key || !this.buffers[key]) return;
     const src = this.ctx.createBufferSource();
     src.buffer = this.buffers[key];
     src.connect(this.eventGain);
     src.start();
   };
   ```

6. **Demo-mode audio** — when the user hits DEMO:
   - Drone buzz fades in immediately (drone is on screen).
   - No event sounds (demo has no real `entry.label`).

7. **Mute / unmute button** — add a small `🔇 MUTE` button to the
   left panel (below the WebSocket section). Toggling it sets
   `ctx.destination.gain` to 0 / 1, or simply suspends / resumes the
   `AudioContext`. Label it `MUTE` / `UNMUTE`. Keep it minimal.

8. **Graceful degradation** — if `AudioContext` is not available
   (old browser) or any `fetch` fails, the UI must continue working.
   Wrap all audio code in try/catch. Log failures to the event log at
   `warn` level: `"Audio: failed to load <file>"`.

### Considerations

- **💡 NOTE: AudioContext requires a user gesture.** Browsers block
  audio autoplay. Do not call `new AudioContext()` on page load. Call
  it inside an existing button handler (`btnLocalize`, `btnDemo`, or
  `btnPause`) — the first click is enough. After `init()`, subsequent
  phase changes can trigger audio freely.
- **💡 NOTE: loop the drone clip, not the event clips.** The drone
  clip (`uas_drone_pass_dcpoke.wav`) is a short pass-by recording.
  `loop = true` makes it continuous. Event clips (gunshot, missile,
  tank) are one-shot — do not loop them.
- **💡 NOTE: keep the three ambient sources running at all times.**
  Starting and stopping them per-phase causes audible clicks. Instead,
  vary only the gain. The ambient gain can be nudged lower during
  `localize` and `hold` to let the event punch through, then back up
  for `transit`.
- **💡 NOTE: the forest atmos file is long.** Use it as the primary
  ambient layer. Birds and crickets from ESC-50 are short loops (~5 s);
  they provide variation on top.
- **💡 NOTE: the FLAC will decode fine in Chrome.** Don't pre-convert
  unless the demo machine is confirmed non-Chromium.
- Keep total added JS under ~80 lines. This is glue code, not a DAW.

### ⚠ HUMAN INPUT NEEDED

1. **Volume balance** — the suggested levels (ambient 0.18, drone 0.55,
   event 1.0) are starting points. Tune by ear before the pitch.
   Ask the user to confirm after a first listen.
2. **Should the drone buzz play in LOCALIZE mode even when the current
   scenario label is `"drone"` (hostile drone)?** Ambiguous — a hostile
   drone sounds similar to a sensor drone. Suggested: yes, play it
   regardless, since the acoustic scene is "drones are in the air".
   Confirm.
3. **Mute button placement** — suggested: bottom of the left panel.
   If the panel is already crowded, a small icon-only button in the
   header is acceptable.
4. **Should the ambient layer also fade during the event sound?** A
   brief ambient duck (−6 dB for 1 s) makes the event punchier.
   Suggested: yes. Confirm before implementing.

### Acceptance criteria

- Clicking any button for the first time initialises the audio context
  without errors.
- Forest ambient (at least one of the three clips) is audible within
  2 s of the first button press.
- Drone buzz is audible during `transit` and `listen` phases, silent
  during `localize` and `hold`.
- The correct event sound plays once when the target pin appears, with
  no repeats within the same scenario.
- MUTE button silences all audio; pressing it again restores sound.
- A 404 or decode error on any audio file shows a `warn` log line
  but does not crash the UI or block the animation loop.
- `statFps` stays ≥ 55 — audio work is off the main thread via Web
  Audio; it must not drop frames.

---

---

## Session 8 — Live error sliders + backend recompute

### Goal

Two sliders (σ_t, σ_pos) in the right rail. Dragging them re-runs the
TDOA localisation in real time and updates the cloud, CEP50, action
chip, and ROE banner. The math runs in Python via a small Flask backend
so there's a single source of truth.

### Files touched

- New: `triangulation/server.py` (Flask app)
- Modified: `ui/index.html` (sliders + fetch logic)
- Modified: `triangulation/locate.py` (expose `localize_scenario` with
  override sigmas)
- Modified: `triangulation/__init__.py` (export `server`)

### Architecture

```
Browser slider drag
       │ debounce ~120 ms
       ▼
   fetch /api/scenarios/{id}?sigma_t_ms=X&sigma_pos_m=Y
       │
       ▼
   Flask backend (triangulation/server.py)
       │
       ├── reads events.json (cached in memory)
       │
       ├── overrides per-event sigma_t_ms / position_error_m
       │    on every row of the requested scenario
       │
       ├── calls localize_scenario(group, ..., mc_samples=120)
       │    with the modified events (note: mc=120 for live, not 400)
       │
       └── returns the recomputed entry as JSON
       │
       ▼
   Browser updates state.scenarios[i] in place, redraws
```

The backend reuses **the exact same `localize_scenario` function** that
the offline pipeline uses, so there's no risk of the live recompute
disagreeing with the JSON on disk.

### New endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/scenarios` | list of all scenarios with default sigmas |
| `GET /api/scenarios/<id>` | single scenario with default sigmas |
| `GET /api/scenarios/<id>?sigma_t_ms=X&sigma_pos_m=Y` | live recompute |
| `GET /api/events?scenario=<id>` | raw events for that scenario (debug) |
| `GET /` | serves `ui/index.html` directly (no separate http server) |
| `GET /<file>` | serves `ui/<file>` (CSS, JS, images, etc.) |

### Right-rail slider visuals

```
┌─ Timing error σ_t ────────────────────────────────────┐
│  ●─────────●─────────────────●─────────────● 20 ms   │
│  0.1 µs   1 µs    100 µs   1 ms          20 ms      │
│  GPS/PTP  good    NTP      cheap         unsynced   │
│  ┃                ┃         ┃              ┃        │
│  └─ current: 6.6 ms ────────────────┘               │
└──────────────────────────────────────────────────────┘

┌─ Position error σ_pos ────────────────────────────────┐
│  ●─────●──────────●──────────●─────────────● 50 m    │
│  0 m   1 m       5 m        15 m            50 m    │
│  GPS   RTK       IMU/30s    IMU/2min   sustained    │
└──────────────────────────────────────────────────────┘
```

Each slider is **log-scaled** for σ_t (sub-µs to ms span isn't useful
linearly) and **linear** for σ_pos (0 to 50 m is fine linear). Marker
positions correspond to operational regimes — these are pitch-bait
because a defense judge knows immediately what they mean.

A tiny inline SVG chart below the sliders shows the **error vs σ
curve** for the current scenario — the "money curve" from the
defensehackathon prototype, scaled down. A red dot marks the current
operating point. As the slider moves, the dot moves; as σ goes
inside/outside the STRIKE zone, a coloured band lights up.

### Tasks

1. **Backend: `triangulation/server.py`**
   - 1.1 Flask app with the endpoints above.
   - 1.2 Load `events.json` once at startup; cache `_group_by_scenario`
         result in memory.
   - 1.3 `_apply_sigma_overrides(events, sigma_t_ms, sigma_pos_m)`
         helper: returns a new list where every row in the scenario
         has the σ values overridden (if not 0/null).
   - 1.4 Endpoint handler calls `localize_scenario(modified_events,
         mc_samples=120)` and returns the resulting dict.
   - 1.5 Cache recent recomputes (LRU, max 50 entries) so the same
         slider value doesn't recompute twice.
   - 1.6 CLI: `python -m triangulation.server --port 5050 [--host
         0.0.0.0]`. Default port 5050 to avoid clashing with the
         existing Dash viewer on 8060.

2. **`localize_scenario` argument additions**
   - 2.1 Add `sigma_t_override_ms: float | None = None` and
         `sigma_pos_override_m: float | None = None` to the function
         signature.
   - 2.2 When non-None, apply to the events before MC. Document the
         interaction with existing per-row σ values: override
         *replaces*, not adds.

3. **Frontend: slider components**
   - 3.1 Two `<input type="range">` inputs in the right rail. Log
         scale for time; linear for position. Show numeric readout.
   - 3.2 Reference-regime markers under each slider (tick marks with
         labels).
   - 3.3 Debounce slider input to ~120 ms before firing fetch.
         Drag-while-fetching is fine; the last fetch wins.

4. **Frontend: fetch + state update**
   - 4.1 `async function recomputeCurrentScenario()` fires
         `/api/scenarios/<id>?...` and patches `state.scenarios[i]`
         with the response.
   - 4.2 The renderer automatically picks up the new cloud, target,
         CEP, action chip on the next animation frame.
   - 4.3 Loading indicator: a 1 px shimmer along the slider track.
   - 4.4 Fetch errors silently revert to the last good value; one
         log line per failure.

5. **Money-curve inline chart**
   - 5.1 100 × 60 SVG below the sliders. Log-log axes (clock σ vs
         error). Pre-baked points: backend has another endpoint
         `/api/scenarios/<id>/sweep` returning ~15 (σ, CEP) pairs at
         the current geometry.
   - 5.2 Red dot at the current operating point updates with the
         slider.
   - 5.3 Coloured background bands for STRIKE / RECON / SEARCH zones
         (using the same thresholds as the policy module).

6. **Reset to defaults**
   - 6.1 A small "↺ default σ" button next to each slider. Clicks
         restore the slider to the scenario's original per-drone
         maximum from the JSON.

### Considerations

- **💡 NOTE: when σ overrides are applied, they're applied to *every
  drone* in the scenario.** Per-drone override is more flexible but
  far worse for UI clarity. The slider is asking "what if all the
  drones had this much error?" not "what if drone_2 specifically did?"
- **💡 NOTE: MC=120 for the live recompute, not 400.** Quality stays
  fine (CEP estimate is stable within a few % at 120). Latency drops
  to ~15–30 ms per call. Saves the demo from feeling sluggish.
- **💡 NOTE: cache recent recomputes** (LRU by `(id, σ_t, σ_pos)`)
  — sliders often retrace the same path during a pitch.
- **⚠ Same backend, same Python process** serves the UI HTML. Don't
  make people start two services. Flask `send_from_directory`
  handles the static files.
- **⚠ CORS isn't an issue** if the static files are served by the
  same Flask app (preferred). If running the UI under a separate
  http.server, add a permissive `Access-Control-Allow-Origin`.

### ⚠ HUMAN INPUT NEEDED

1. **Slider ranges and scale.** Suggested: σ_t **log** from 0.1 µs to
   20 ms; σ_pos **linear** from 0 to 50 m. Confirm.
2. **Regime markers.** Suggested labels:
   - σ_t: `GPS/PTP (1 µs)`, `good NTP (500 µs)`, `cheap NTP (3 ms)`,
     `unsynced (≥ 10 ms)`
   - σ_pos: `GPS (1 m)`, `RTK (0.1 m)`, `IMU 30 s (5 m)`,
     `IMU 2 min (15 m)`, `sustained denial (50 m)`
   Confirm wording (especially for the IMU drift bands).
3. **MC sample count for live.** Suggested 120 (target 30 ms). The
   tradeoff is "ellipse jitters slightly as σ moves vs slider feels
   sluggish". Confirm preference.
4. **Should the money-curve inline chart be in v1?** Adds ~2 hours.
   Suggested: yes — judges read it instantly and it's the single
   most credibility-building element. Confirm.

### Acceptance criteria

- `python -m triangulation.server` starts on port 5050.
- `http://localhost:5050/` serves the existing tactical UI.
- σ_t and σ_pos sliders in the right rail update CEP50, cloud, target
  position, action chip within ~150 ms of slider stop.
- Reset button restores the scenario default.
- Money-curve mini-chart present; red dot tracks slider.
- ROE banner colour and text update live as the action flips.

---

---

## Session 9 — SEARCH action + multi-drone area sweep

### Goal

When CEP50 is too large for a point-target response, the ROE engine
emits a new `SEARCH` action. The respond phase then dispatches **all
three drones** to spread across the confidence ellipse in a grid /
spoke pattern. Visual: three responders fanning out, each sweeping
their assigned subzone. Telemetry mentions "SEARCH PATTERN initiated ·
sweeping XXX m²".

### Files touched

- Modified: `triangulation/policy.py` (extend `decide()`)
- Modified: `triangulation/locate.py` (no functional change; output
  field values change)
- Modified: `ui/index.html` (multi-responder rendering in `respond`
  phase, telemetry strings)
- Modified: `SESSIONS.md` (mark Session 1 acceptance criteria as
  extended)

### Architecture

```
policy.decide(cep50, gdop, label, conf):
  if conf < HOLD_FLOOR:        return HOLD
  if cep50 < STRIKE_CEP_MAX
     and gdop < STRIKE_GDOP_MAX
     and label in STRIKE_ELIGIBLE:
                                return STRIKE
  if cep50 < SEARCH_FLOOR:     return RECON
                                return SEARCH        ← NEW
```

For the visuals:

```
For SEARCH action, the respond phase animates:

  1. All three drones break formation simultaneously.
  2. Compute a 3-point sweep pattern inside the 95% ellipse:
       - drone_1 → ellipse centre
       - drone_2 → ellipse centre + 0.6·major_axis along +axis
       - drone_3 → ellipse centre + 0.6·major_axis along −axis
     (or a spoke pattern with the three points equiangular around
      the centre — pick the more visually obvious layout)
  3. Animate all three arcing to their sweep points.
  4. On arrival, each drone shows a small "scanning" pulse for ~1 s.
  5. Final state: three responders parked at sweep points, plus an
     overlay polyline tracing the sweep coverage.
```

The existing `state.responders[]` slot already supports multiple
responders — Session 3 designed it as a list. SEARCH just populates
three entries instead of one.

### Tasks

1. **Backend: extend `policy.decide()`**
   - 1.1 Add `SEARCH` to the `Action` literal type.
   - 1.2 Add `SEARCH_FLOOR` constant (suggested: CEP50 > 50 m → SEARCH).
   - 1.3 Add `severity = "low"` for SEARCH (not actionable, just
         searching).
   - 1.4 Add `weapons_release_required = false` for SEARCH.

2. **Backend: expose search pattern in JSON**
   - 2.1 When action is SEARCH, add a `search_pattern` field to the
         output entry: `[{lat, lon, role}, ...]` with 3 sweep points
         derived from the ellipse axes.
   - 2.2 Add `search_pattern_xy_local` for completeness.
   - 2.3 New helper in `policy.py`: `search_points(center_xy,
         major_axis_xy, minor_axis_xy, n=3) -> list[(x, y)]`.

3. **Backend: extend test cases**
   - 3.1 Verify a high-σ scenario triggers SEARCH.
   - 3.2 Verify `search_pattern` has 3 entries within the ellipse.

4. **Frontend: action chip colour**
   - 4.1 Add `--search` colour (suggested: `#1e9af0` blue — distinct
         from STRIKE red and RECON amber).
   - 4.2 Update chip styling switch in `renderDecide()` and the
         sidebar cards.

5. **Frontend: multi-responder rendering**
   - 5.1 In `respond` phase, when action is SEARCH:
         - Read `entry.search_pattern_xy_local`
         - Spawn 3 responders simultaneously (vs 1 for STRIKE/RECON)
         - Animate each on its own arc to its sweep point
   - 5.2 On arrival, each shows a "scanning" pulse (existing
         `emitPulse` helper).
   - 5.3 Optional overlay: a faint dashed polyline drawing the sweep
         coverage between the three points.

6. **Frontend: SEARCH telemetry strings**
   - 6.1 Add to the `TELEMETRY` table (from Session 4):
       ```js
       SEARCH: [
         { at: 0.05, msg: "SEARCH PATTERN initiated · 3 drones deploying",  lvl: "warn" },
         { at: 0.35, msg: "Drones on station · sweep underway",              lvl: "warn" },
         { at: 0.70, msg: "No contact at primary point · expanding search",  lvl: "warn" },
         { at: 0.92, msg: "Search incomplete · requesting more sensors",     lvl: "hostile" }
       ]
       ```
       (Adjust wording with user — see Human Input.)

7. **Frontend: HOLD-vs-SEARCH chip**
   - 7.1 HOLD remains for "no usable fix" (confidence floor).
   - 7.2 SEARCH replaces HOLD for "fix is real but too imprecise".
   - 7.3 Make sure the action chip clearly distinguishes them
         visually so judges don't conflate them.

### Considerations

- **💡 NOTE: 3 drones spreading vs 1 drone arcing is a 3× richer
  visual.** Don't water it down to "one drone moving more slowly".
  The multiple bodies are the point.
- **💡 NOTE: ellipse-aware sweep**, not grid. The sweep points must
  align with the major axis of the ellipse — that's *why* the
  visual is interesting. A square grid in a long thin ellipse looks
  wrong; a spoke pattern aligned to the axis looks right.
- **💡 NOTE: SEARCH is recoverable.** Don't render it as "the
  system failed". The narrative is "the system knew it didn't have
  a confident fix and dispatched proportionate resources to gather
  more information." That's a feature, not a bug.
- **⚠ Backwards compatibility.** Existing consumers of
  `recommended_action` will see a new enum value (`SEARCH`). They
  shouldn't crash — but if any downstream code assumes a closed
  set, it must be updated.

### ⚠ HUMAN INPUT NEEDED

1. **CEP50 threshold for SEARCH.** Suggested: > 50 m → SEARCH; ≤ 50 m
   and > 10 m → RECON; ≤ 10 m → STRIKE. Confirm or override.
2. **Sweep pattern.** Suggested: 3-point spoke aligned with ellipse
   major axis. Alternative: equilateral triangle around centre. Pick
   one.
3. **Number of sweep drones.** Suggested: 3 (every drone). Could be
   more if more drones exist. Confirm "all drones, every time" vs
   "at least 2".
4. **Sweep telemetry copy.** The draft above is generic. Lean more
   military ("SECTOR SEARCH · ZONE BRAVO"), more operational
   ("Drones on station, sweep underway"), or more diagnostic ("Cloud
   area exceeds 5000 m², expanding search radius"). Confirm tone.

### Acceptance criteria

- `policy.decide(cep50=80, gdop=2, label="gunshot", confidence=0.4)`
  returns `Decision(action="SEARCH", ...)`.
- `localizations.json` entries with high CEP50 have
  `recommended_action == "SEARCH"` and a 3-element
  `search_pattern_xy_local`.
- UI in `respond` phase shows three responders spreading to the
  sweep points when action is SEARCH.
- Action chip is distinct from STRIKE/RECON/HOLD in colour and
  label.
- Telemetry log for SEARCH plays the SEARCH-specific strings.

---

## Cross-session conventions (Part 2)

- **All sliders are log-scale where the operational regimes span
  decades.** Linear sliders compress the interesting region. σ_t is
  log; σ_pos is linear (0–50 m is one decade, fine linear).
- **All recomputes go through the existing Python pipeline.** Don't
  port the math to JS. The backend is cheap; latency is fine.
- **Phase advances are operator-driven by default**, autoplay is a
  toggle, never the default. The pitch needs pacing control.
- **Action chips and ROE banner are the same colour-coding everywhere**:
  STRIKE red, RECON amber, SEARCH blue, HOLD grey. Don't deviate
  in any rendering.
- **Telemetry copy stays consistent across actions.** Each action's
  4 lines hit similar beats (dispatch / arrival / progress / closure)
  so the judge's eye learns the pattern.

## What this leaves on the table

- **Hand-drawn sweep paths inside the ellipse.** SEARCH currently
  shows 3 points; a fuller demo would show curves traced by each
  drone. ~2 hours of extra work; nice but not essential.
- **Replay history.** When σ has been moved and recomputed many
  times, there's no way to step back through the trajectory. Could
  add an undo stack — out of scope here.
- **Cross-scenario priority elevation.** With 5 scenarios in the
  sidebar, dragging σ to extreme on scenario 1 doesn't change
  scenario 4's priority. That'd be more realistic but is gold-plating
  beyond the demo budget.

---

## Session 10 — Sandbox tab                             (≈4 h, spec: `SESSIONS_INTERACTIVE.md` §10)

The sixth tab `🧪 Sandbox`: drag drones and source anywhere, tune σ
with the same sliders, watch the cloud + estimate update in real
time. Truth (user-placed source) is visible; estimate is computed;
the distance between them is the actual error, drawn as a dashed
line with a metres label.

**Key tasks:**

- New `triangulation/sandbox.py`: `build_events(drones, source,
  sigma_t_ms, sigma_pos_m) -> events_list` synthesises a JSON event
  group from a geometry config.
- New `POST /api/sandbox` endpoint that calls `build_events` then
  `localize_scenario`.
- Pointer-drag handlers on the entity DOM layer; throttled fetch
  during drag.
- "OPEN IN SANDBOX" button on scenario tabs to copy their geometry.

**Why it matters:** hand the laptop to a judge. Hackathons are won by
the team whose demo the judge *plays with* rather than just *watches*.

---

## Session 11 — 2-drone bearing-only localization

**Goal:** When only 2 drones detect an event, produce an honest fix
— a hyperbola curve + an uncertainty wedge — and surface it through
the same JSON contract as 3-drone fixes.

**Files touched:**

- New: `triangulation/core/solver_2drone.py`
- Modified: `triangulation/locate.py` (route 2-drone groups here)
- Modified: `triangulation/policy.py` (auto-downgrade 2-drone → SEARCH)
- Modified: `triangulation/AGENTS.md` (schema update)
- New: `triangulation/tests/test_2drone.py`

**Architecture:**

A 2-drone fix is fundamentally different from a 3-drone fix. The
JSON output stays the same shape (`source`, `cloud_*`, `cep50_m`,
`recommended_action`, etc.) but the semantics shift:

```
3-drone fix:
   source         = point estimate (lat, lon)
   cloud_latlon   = closed 95% ellipse polygon (~72 points)
   cep50_m        = radius
   fix_kind       = "point"          ← NEW field

2-drone fix:
   source         = midpoint of hyperbola arc (still lat, lon —
                    a representative point on the curve)
   hyperbola_latlon = list of (lat, lon) along the curve     ← NEW
   cloud_latlon   = wedge polygon — outer boundary of the
                    swept-uncertainty band on each side
   cep50_m        = null (undefined for a curve)
   cep50_perp_m   = perpendicular-to-curve half-width        ← NEW
   fix_kind       = "bearing"        ← NEW field
   bearing_axis_deg = orientation of the curve at midpoint   ← NEW
```

The hyperbola is parameterised: given drones at `p1, p2` and
`Δd = c · (t2 - t1)`, the locus is the set of points where
`||x - p1|| - ||x - p2|| = Δd`. Closed-form parameterisation in
canonical coordinates (origin at midpoint, major axis along p1-p2
direction): `x(t) = a · cosh(t)`, `y(t) = b · sinh(t)` with
`a = Δd/2`, `b = sqrt(c² - a²)` where `c` is half the inter-drone
distance. Then rotate and translate into the local plane.

The wedge is computed by Monte-Carlo: for each (σ_t, σ_pos) draw,
recompute the hyperbola; take the convex hull of all sampled
hyperbola points to get the swept boundary; output the boundary as
a closed polygon.

**Subtasks:**

- 11.1 `solver_2drone.hyperbola(p1, p2, dd, n_pts=64)` — return
       N points along the hyperbola arc, clipped to a reasonable
       extent (±2× inter-drone distance).
- 11.2 `solver_2drone.mc_wedge(events, drone_positions,
       clock_sigma_s, pos_sigma_m, n=400)` — MC sweep returning
       a list of hyperbola polylines; the wedge boundary is the
       convex hull of all points.
- 11.3 Route in `locate.py`: when a group has exactly 2 distinct
       drones, call `solver_2drone` instead of skipping; emit the
       new schema fields.
- 11.4 `policy.decide()`: when `fix_kind == "bearing"`, action is
       always SEARCH (never STRIKE or RECON). Add a reason string:
       "2-sensor bearing fix; insufficient for point engagement".
- 11.5 UI rendering: in `localize` phase, when `fix_kind ==
       "bearing"`, draw the hyperbola as a solid red curve and the
       wedge as a translucent red band. Skip the cross marker (no
       point estimate).
- 11.6 Tests: synthetic 2-drone event → hyperbola passes through
       the true source; wedge width scales with σ.

**⚠ HUMAN INPUT NEEDED:**

1. Hyperbola clipping extent. Suggested ±2× inter-drone distance
   so the curve stays on-screen for typical drone separations.
2. Should the UI label the curve "bearing locus" or "hyperbola"?
   Suggested "bearing locus" — non-specialists understand.

**Acceptance criteria:**

- A 2-drone group in `events.json` produces a `localizations.json`
  entry with `fix_kind == "bearing"`, a `hyperbola_latlon` polyline,
  a `cloud_latlon` wedge polygon, and `recommended_action ==
  "SEARCH"`.
- UI renders the curve + band correctly when this entry plays.
- 3-drone groups continue to produce `fix_kind == "point"` and
  ellipse clouds (no regression).

---

## Session 12 — Multi-scene narrative tab

**Goal:** Replace `① Gunshot · clean` with a 4-scene operational arc
that walks PATROL → STRIKE → drone-lost → DEGRADED DETECTION →
2-drone SEARCH. Tells the whole defense story in one tab.

**Files touched:**

- Modified: `ui/index.html` (tab state model, scene transition logic)
- New: `detection/output/narrative_gunfire.json` (scene-sequence data)
- Modified: `triangulation/server.py` (endpoint to load narratives)
- Modified: `triangulation/locate.py` (optional: a CLI flag to
  generate narrative scene data from synthetic events)

**Architecture:**

Each tab in the UI is now `{id, label, scenes: Scene[]}` where a
plain single-scenario tab has `scenes.length == 1` and a narrative
tab has `scenes.length > 1`. A `Scene` is an extended scenario:

```json
{
  "scene_index": 0,
  "title": "Initial detection — clean geometry",
  "narrative_text": "Three sensor drones holding patrol. Acoustic
                     event detected. Triangulation produces a tight
                     fix. ROE: STRIKE.",
  "drone_roster": ["drone_1", "drone_2", "drone_3"],
  "drones_lost_before_scene": [],
  "drones_lost_during_scene": [],
  "scenario": { ... full localization entry ... },
  "outcome": {
    "drone_lost": null,                 // or e.g. "drone_3"
    "next_scene_intro": "Drone 3 lost during engagement."
  }
}
```

The phase machinery from Session 7 applies WITHIN a scene. When the
last phase (`complete`) finishes, ▶ NEXT advances to the next scene
and restarts at `patrol`. ⏪ PREV at scene start jumps to the prior
scene's `complete`.

Drone-loss is **scripted**, not computed. The narrative file says
"after scene 2's strike, drone_3 is lost". The UI honours this by:
- Drawing drone_3 with a red ☓ overlay and dimmed fill from scene 3
  onwards.
- Leaving drone_3's icon at its scene-2 final position.
- Excluding drone_3 from any TDOA calculations in scenes 3+.

The four scenes:

```
Scene 1 — PATROL + DETECT + LOCALIZE + DECIDE + RESPOND + COMPLETE
  Drones: all 3.
  Fix: 3-drone ellipse, low CEP. Action: STRIKE.
  Outcome: drone_3 lost during strike (scripted).

Scene 2 — DRONE LOST (a single phase: COMPLETE)
  Visual: drone_3 ☓-marked. Banner: "ASSET LOST · roster reduced 3→2".
  No new detection. Operator clicks NEXT to continue.

Scene 3 — DETECT + LOCALIZE + DECIDE
  Drones: 2 (drone_1, drone_2). drone_3 dimmed and ignored.
  Fix: bearing-only hyperbola + wedge (item 6 math).
  Action: SEARCH (forced — can't STRIKE a curve).

Scene 4 — RESPOND + COMPLETE
  Two responders break formation and sweep along the hyperbola wedge.
  Each takes one half (use ellipse-aware sweep math adapted to a
  curve: 2 sweep points spaced along the wedge centreline).
  Outcome: "SEARCH PATTERN ACTIVE — awaiting next event".
```

Scene transitions don't need elaborate animation; a 500 ms cross-fade
of the banner is enough. The novelty is the narrative arc itself,
not the transition graphics.

**Subtasks:**

- 12.1 Tab state shape: `state.tabs[i].scenes[]` instead of a single
       scenario per tab. Update Session 7's NEXT/PREV logic to bridge
       scene boundaries.
- 12.2 Narrative file generator: `triangulation/locate.py
       --narrative gunfire --out detection/output/narrative_
       gunfire.json` produces a 4-scene JSON from a hand-crafted
       events sub-set. (Or hand-write the JSON for the demo.)
- 12.3 Scene-aware loader: `/api/narratives/<id>` returns the full
       scene list; the UI loads it once and stores in
       `state.tabs[NARRATIVE_TAB_ID].scenes`.
- 12.4 Drone roster rendering: dim drone_3 in scenes 3+ with a ☓
       overlay; exclude from `state.drones` for phase math; show in
       legend as "LOST".
- 12.5 Scene-boundary UI: banner during the inter-scene transition
       shows `narrative_text` of the next scene. Operator must click
       NEXT to enter.
- 12.6 Scene 4 sweep math: for a bearing fix, sweep points are
       (centre of wedge) ± 0.5 × wedge half-length along the
       hyperbola tangent. Adapt `policy.search_points` to accept
       either an ellipse or a wedge.
- 12.7 Replace tab ① with the narrative tab in the default tab list.
       Tabs ② – ⑤ remain single-scene presets so the team can still
       contrast.

**⚠ HUMAN INPUT NEEDED:**

1. Tab label. Suggested `① Gunshot · operational arc` to signal
   it's the storied one. Confirm.
2. Drone-loss visualisation. Suggested ☓ overlay + dimmed icon at
   last-known position. Alternative: full removal from map. Confirm.
3. Scene-2 duration. With no detection, it's just a banner + log
   line. Suggested ~3 s auto-advance OR wait-for-NEXT. Probably the
   latter to let the presenter dwell.
4. Should drones_used for scene 3 actually start the scene already
   missing one, or should there be a "drone reposition" phase first?
   Suggested: start already missing one — keeps the focus on the
   degradation result, not the bookkeeping.

**Acceptance criteria:**

- Tab `①` shows "scene 1 of 4" in the title strip.
- ▶ NEXT walks through every phase of every scene and ends after
  scene 4's `complete`.
- ⏪ PREV walks back across scene boundaries.
- Scene 2 visualises drone_3 as LOST.
- Scene 3 renders a hyperbola + wedge (no ellipse).
- Scene 3's action chip is SEARCH (blue).
- Scene 4's responder count is 2, not 3.
- Other tabs (② – ⑤, sandbox) are unaffected.

---

## Session 13 — Kill-drone button (live resilience)

**Goal:** Persistent UI control to drop any drone from the current
scene at any time. Triggers a live re-localization with the reduced
roster. Surfaces the graceful-degradation story as a reactive,
audience-driven moment rather than a scripted scene. Doubles as the
implementation that Session 12's scene-2 drone-loss beat invokes.

**Files touched:**

- Modified: `ui/index.html` (kill pills + reset button, kill state,
  re-render on change)
- Modified: `triangulation/server.py` (accept `killed` query/body
  param on all localize/sandbox endpoints)
- Modified: `triangulation/locate.py` (filter events by killed
  roster before localizing)
- Modified: `triangulation/policy.py` (new `INSUFFICIENT_SENSORS`
  action when < 2 alive drones)
- Modified: `triangulation/AGENTS.md` (schema additions:
  `killed_drone_ids`, action enum extension)

**Architecture:**

```
state.tabs[i].killedDrones : Set<string>     // per-tab kill state
state.tabs[i].defaultDrones : list<string>   // restored on RESET KILLS

UI fires recompute on every kill/revive:

  /api/scenarios/<id>?sigma_t_ms=X&sigma_pos_m=Y
                     &killed=drone_2          ← NEW

Backend (locate.localize_scenario):
  group_filtered = [e for e in group
                    if e['drone_id'] not in killed_set]
  remaining = len({e['drone_id'] for e in group_filtered})
  if remaining >= 3:    use existing 3-drone ellipse fix
  if remaining == 2:    use Session 11 hyperbola+wedge fix
  if remaining == 1:    emit {action: "INSUFFICIENT_SENSORS",
                              fix_kind: "none",
                              reason: "single-sensor fix not
                                       available without RSSI mesh"}
  if remaining == 0:    emit no-fix sentinel; UI shows pure patrol
```

Switching tabs resets the kill set to that tab's defaults (so a
kill on `①` doesn't poison `②`). The narrative tab's scene-2 beat
invokes the kill mechanism programmatically (no separate code path).

**Subtasks:**

- 13.1 Right-rail UI: a row of `💀 drone_<id>` pills (one per drone
       in the current roster) + a `🔄 RESET KILLS` button. Pills
       toggle: pressed = killed (red ☓ on the icon).
- 13.2 Frontend state: `state.tabs[i].killedDrones`. Mirror to the
       drone-render path: killed drones get the `lost` CSS class
       (dimmed icon + red ☓ overlay). Excluded from `state.drones`
       for any phase math.
- 13.3 Wire kill → `recomputeActiveTab(killed=[...])`. Debounce
       the same as σ sliders (~120 ms).
- 13.4 Backend: `localize_scenario` gains a `killed_drone_ids:
       set[str] | None = None` kwarg. Filters the group before
       running the math; routes 3 → 3-drone, 2 → 2-drone (Session
       11), <2 → graceful no-fix.
- 13.5 Flask endpoint changes: parse `killed=a,b,c` from query
       string into a set; pass through.
- 13.6 `policy.decide()`: add `INSUFFICIENT_SENSORS` to the Action
       enum; returns when `fix_kind == "none"`. Severity = "low",
       weapons_release_required = false.
- 13.7 New action chip colour for INSUFFICIENT_SENSORS (suggested
       `--insufficient: #6a737d` slate-grey).
- 13.8 UI banner when `INSUFFICIENT_SENSORS`: "SENSOR LOSS — fix
       unavailable · expanding patrol".
- 13.9 Tab-switch reset: when `setActiveTab(j)` runs, clear
       `state.tabs[j].killedDrones` to defaults. (Don't touch
       OTHER tabs' kill sets — they may be mid-demo too.)
- 13.10 Keyboard shortcut: `k` cycles through drones to kill the
       next live one. Useful for fast presenter input.

**Considerations:**

- **💡 NOTE: kill is pure UI state.** events.json on disk is never
  modified. Each render call passes the killed set explicitly to
  the backend.
- **💡 NOTE: works in every tab, including sandbox.** In sandbox,
  a killed drone stays at its dragged position with ☓ overlay; it
  just doesn't contribute to the math.
- **💡 NOTE: Session 12 reuses this.** Scene 2's drone-loss beat is
  just a programmed kill call at scene start. No parallel code
  path for "scripted" vs "ad-hoc" loss.
- **💡 NOTE: revival is instant.** Click the pill again to revive;
  fix re-tightens within ~120 ms.
- **⚠ Edge case: σ sliders + kill must not race.** Both fire fetches
  on change. Use a single async function `recomputeActiveTab()` that
  reads both states at the moment of fetch and last-fetch-wins.

**⚠ HUMAN INPUT NEEDED:**

1. Button placement. Right-rail pill row (suggested) vs top-bar
   dropdown (more screen real estate). Confirm.
2. Should kill state persist across browser refresh? Suggested
   **no** — each demo starts clean.
3. Audio cue on kill? Suggested **no** — competes with scenario
   sounds.
4. Keyboard shortcut for the kill cycle. Suggested `k` (mnemonic).
   Confirm.

**Acceptance criteria:**

- Kill `drone_2` in a 3-drone scenario → ellipse collapses to
  hyperbola+wedge (Session 11) within ~200 ms; action chip flips
  from STRIKE/RECON to SEARCH live.
- Kill `drone_2` + `drone_3` → action chip becomes
  INSUFFICIENT_SENSORS; banner appears; no fix is drawn.
- `🔄 RESET KILLS` → all drones restored; original fix returns
  within ~200 ms.
- Tab switch resets kills for the target tab to that tab's defaults.
- Works in the sandbox tab (drag remaining drones, see hyperbola
  follow the geometry).
- Session 12 scene 2 transitions invoke this mechanism rather
  than duplicating logic.

### Add audio — atmospheric rotor loop + event sound cues

*Not a numbered session — sprinkled into whatever UI work is happening
that day. Adds ~1.5 h total. Hooks into the phase machine from
Session 7. Can ship at any point after Session 7 lands.*

**Goal:** make the demo feel like a sound-detection system by
actually playing sound. Two layers:

1. **Atmospheric rotor loop.** Drone rotor WAV looping in the
   background while drones are on screen. OFF by default (it's
   annoying after 30 s of pitch); toggle pill in the top bar.
2. **Event audio cues.** When a scenario reaches DETECT phase, the
   classified sound plays — gunshot for `label=="gunshot"`, tank
   engine for `"tank"`, etc. Timed to land **0.5 s before** the
   target dot appears in LOCALIZE, so the audience hears the event
   first, then sees the system register it.

**Files touched:**

- Modified: `ui/index.html` (audio elements, phase-hook trigger,
  rotor toggle pill)
- Modified: `triangulation/server.py` (one new static route to
  serve `data/samples/` so the browser can fetch the WAVs)

**Architecture:**

```
ui/index.html
  AUDIO = { rotor, gunshot, tank, missile_launch, drone }
    each one a new Audio(src)
  AUDIO.rotor.loop = true
  AUDIO.rotor.volume = 0.25
  for each event sound: volume = 0.75

  Phase machine hook (in tickPlayback):
    on DETECT phase, when progress crosses (dur - 500ms) / dur:
       play AUDIO[entry.label] from currentTime = 0
       (fires exactly 500 ms before LOCALIZE phase starts)

  Top bar: <button id="rotor-toggle">🔊 ROTOR · off</button>
    click: AUDIO.rotor.play() / pause(); toggle label

triangulation/server.py
  Add a single Flask route:
    @app.route("/audio/<path:rel>")
    def serve_audio(rel):
        return send_from_directory(REPO_ROOT / "data/samples", rel)
  Browser URLs: /audio/gunshot/demo_gunshot_128293.wav, etc.
```

**Tasks:**

- A.1 In `triangulation/server.py`, add the `/audio/<path:rel>`
      route serving `data/samples/`.
- A.2 In `ui/index.html`, declare an `AUDIO` map keyed by event
      label, each value a `new Audio("/audio/<path>")` preloaded
      with `preload="auto"`. Map labels:
      `gunshot → demo_gunshot_128293.wav`,
      `tank → 169743__qubodup__m1-abrams-tank-engine-and-shots-wombzerncci.flac`
      (or the shorter `dennish18-tank-moving-143104.mp3`),
      `missile_launch → ucas_launch_x47b_qubodup.flac`,
      `drone → uas_drone_pass_dcpoke.wav` (rotor loop).
- A.3 Hook the phase machine: in `tickPlayback`'s DETECT branch,
      track whether the event audio has fired for the current
      phase entry (avoid double-fire). Fire when
      `phaseT >= (PHASE_MS.detect - 500)`. Reset the fired flag on
      phase advance.
- A.4 Top-bar rotor toggle. State stored in
      `state.rotorEnabled: bool`, default `false`. Click handler
      toggles state, plays/pauses `AUDIO.rotor`, updates button
      label `🔊 ROTOR · on` / `🔇 ROTOR · off`.
- A.5 Mute-all keyboard shortcut: `m` toggles a master mute that
      pauses all sounds. Useful for the presenter if a phone rings.
- A.6 Kill-drone interaction (Session 13): when ALL drones killed,
      auto-pause the rotor (no drones → no rotor sound). When any
      drone revived, resume if `rotorEnabled` is true.

**Considerations:**

- **💡 NOTE: browser autoplay policy.** Audio cannot play until the
  user clicks something. The first ▶ NEXT click counts. No special
  handling required; first audio just plays from then on.
- **💡 NOTE: file paths via Flask, not relative.** The UI is served
  by Flask anyway (Session 8); use absolute `/audio/...` URLs.
- **💡 NOTE: scene-2 of the narrative tab (drone lost).** If you
  want a "drone lost" sound effect for the narrative arc, add a
  short static / explosion WAV under a new label like
  `drone_lost`. Optional — silence works fine too.
- **💡 NOTE: rotor loop must be seamless.** Some WAVs have a click
  at the loop boundary. If you hear it, trim the WAV with
  `ffmpeg -i in.wav -t 4 -af afade=t=out:st=3.9:d=0.1 out.wav` to
  fade out the last 100 ms cleanly.
- **⚠ Multi-event overlap.** Clicking NEXT fast can fire two event
  WAVs simultaneously. Acceptable — it actually reads as realistic.
  But if you want to enforce one-at-a-time: pause the previous
  event audio before playing the new one.
- **⚠ Sandbox tab.** Sandbox has no scenarios advancing through
  phases, so it has no DETECT phase to hook into. Skip event audio
  in sandbox. Rotor still works if enabled.

**⚠ HUMAN INPUT NEEDED:**

1. Rotor default: off (suggested) vs on. Confirm off.
2. Volume balance: rotor `0.25`, events `0.75`. Confirm.
3. Lead time before LOCALIZE: 500 ms (suggested). Could be 300 ms
   or 800 ms — confirm what feels right.
4. Tank WAV: short loop (`dennish18-tank-moving-143104.mp3`, ~3 s)
   or longer atmospheric (`169743__qubodup`, ~30 s)? Suggested
   the short one — fires once per scenario, doesn't drag.
5. Mute-all keyboard shortcut on `m`? Confirm.

**Acceptance criteria:**

- Rotor toggle pill in the top bar; clicking it plays/pauses a
  looped drone WAV at `0.25` volume.
- Default state on page load: rotor OFF.
- When a scenario reaches DETECT phase and crosses the
  `(dur - 500ms)` mark, the matching event WAV plays once at
  `0.75` volume.
- LOCALIZE phase starts ~500 ms later; dot appears.
- Mute-all key (`m`) silences everything; press again to restore.
- Sandbox tab: rotor works; no event audio (no phases).
- Kill all drones → rotor auto-pauses (until revival).

---

## Bridge specifications (referenced above)

These are short specs for the bridge sessions referenced in items
17, 22, 24, 27. They're not in any other doc — included here so the
plan is self-contained.

### Bridge: Recon imagery actually traverses the mesh (item 22)

**Files:** `ui/index.html`, `triangulation/server.py`,
`mesh/imagery.py`.

**Change:** in Session 4's telemetry handler, when the RECON action
hits the `IMAGING NOW` beat, instead of `popup.show()`, the UI:

1. `POST /api/recon-imagery` with `{scenario_id}`.
2. Open SSE to `/api/recon-imagery/stream/<id>`.
3. Render the image incrementally as chunks arrive.
4. On SSE close: log "imagery complete via mesh".
5. On 5 s timeout without first chunk: show the static placeholder
   so the demo never appears broken.

**Acceptance:** the recon popup that used to appear instantly now
animates a progress bar from 0% to 100% as the mesh delivers chunks.
First-thumb under 250 ms in sim.

### Bridge: Mesh-NTP corrects acoustic timestamps (item 24)

**Files:** `triangulation/core/io.py`, new
`triangulation/clock.py`, `triangulation/server.py`.

**Change:** `core/io.relative_times` gains an optional
`clock_offsets: dict[drone_id, ns] | None = None` argument. When
provided, it adds the offset to each event's timestamp before
differencing. `triangulation/clock.py` exposes
`register_mesh(node)` and `get_offsets()`. The Flask server, when
mesh-aware mode is enabled (env `MESH_MODE=1`), registers the
mesh node at startup and passes `get_offsets()` to every localize
call.

**Acceptance:** with mesh on, injecting 1 ms drift on a drone
keeps CEP50 within 5% of un-drifted; with mesh off, CEP50 visibly
blows up.

### Bridge: Demo orchestrator (item 17)

**Files:** new `scripts/demo.py`, README update.

**Change:** `scripts/demo.py` reads `mesh/topology.yaml`, spawns
`python -m mesh.node --id <id>` for each drone + `python -m
triangulation.server` for the operator backend, polls the server
port, opens the browser. Ctrl-C SIGTERMs all children and waits.

**Acceptance:** `./scripts/demo.sh` brings up everything, opens
the browser, Ctrl-C cleans up, no zombie processes.

### Bridge: Mesh events in the operator event log (item 19)

**Files:** `ui/index.html`, `mesh/operator.py`,
`triangulation/server.py`.

**Change:** existing `#eventLog` in the UI now also displays
mesh events (route changes, NTP convergence, frame counts) with a
distinct `.entry.mesh` CSS class (cyan tint). Source is a polled
`/api/mesh/events?since=<ts>` endpoint.

**Acceptance:** running the demo, the event log interleaves
acoustic telemetry (red/amber) and mesh telemetry (cyan).
"BLOCK drone_2" in the UI produces a `[ROUTE]` line in the log
within 500 ms.

### Bridge: ROE aware of mesh health (item 27)

**Files:** `triangulation/policy.py`, `triangulation/server.py`.

**Change:** `policy.decide(...)` gains kwargs `mesh_health_score:
float = 1.0`, `clock_sync_quality_us: float = 0`. When the score
drops below 0.9 OR `clock_sync_quality_us > 100`, the action chip
drops one tier. UI shows a "mesh health" pill next to the chip.

**Acceptance:** triggering "BLOCK drone_2" causes the active tab's
ROE action to visibly downgrade (STRIKE → RECON, or RECON →
SEARCH) within one polling tick.

---

## Time budget at a glance

| Tier | Sessions | Hours |
|---|---|---|
| Tier 1 — Essential | 1–13 | ≈ 43 h |
| Tier 2 — Strong (mesh) | 14–24 | ≈ 27 h |
| Tier 3 — Nice to have | 25–28 | ≈ 17 h |

If you have **≤ 43 h**: ship Tier 1, done. Demo is complete,
operator-paced, every phase visually self-explanatory, includes the
narrative arc, has the sandbox, lets you live-kill drones, contrasts
threat vs ambient classification, ships with the bandwidth-budget
slide, has a permanent live bandwidth side panel showing the
mesh compression numbers, AND has a fully reactive Live Ops tab
where events are dropped on the map and the system responds in
real time.

If you have **43–70 h**: Tier 1 + Tier 2. Full integrated demo with
a working mesh underneath everything, including the toggleable
packet-flight FX animation.

If you have **70+ h**: pick from Tier 3, hardware bring-up first if
anyone on the team is signed up for it.

## Repository layout after all of Tier 1 + Tier 2

```
Junction_Defence_Hackathon/
├── triangulation/
│   ├── core/
│   │   ├── io.py, solver.py, uncertainty.py
│   │   └── solver_2drone.py        ← new (item 6)
│   ├── locate.py, policy.py, projection.py
│   ├── viewer.py, sandbox.py       (sandbox: item 4)
│   ├── server.py                   ← new (item 3)
│   ├── clock.py                    ← new (item 18 bridge)
│   ├── jam.py
│   └── tests/
├── mesh/                           ← new (items 9–17)
│   ├── transport/{base.py, sim.py, real.py?}
│   ├── frame.py, security.py
│   ├── routing.py, priority.py
│   ├── ntp.py, imagery.py
│   ├── operator.py, node.py
│   ├── topology.yaml
│   └── tests/
├── ui/index.html                   (tabs + sliders + sandbox + narrative + mesh panel)
├── scripts/demo.py                 ← new (item 12 bridge)
├── docs/MESH_ARCHITECTURE.md       (Session 6, item 8)
├── detection/output/
│   ├── events.json
│   ├── localizations.json
│   ├── localizations_jammed.json
│   └── narrative_gunfire.json      ← new (item 7)
├── SESSIONS.md, SESSIONS_INTERACTIVE.md, MESH_PLAN.md
└── ROADMAP.md                      ← this file
```

## Decision checklist before starting

Before kicking off the next Sonnet session, confirm:

1. **Hours remaining?** Determines which tiers you build.
2. **Sim-only at the venue, or hardware in scope?** If sim-only,
   skip item 22.
3. **Pitch length?** 60 s / 2 min / 5 min — affects how many tabs +
   scenes you can showcase. (The narrative tab alone fills ~90 s.)

Once those are answered, follow the numbered list above. Don't
skip ahead within a tier — each item ends with the demo strictly
better, not half-broken.

---

## Session 14 — Source icon + acoustic emission visuals

**Goal:** Replace the current "drones spontaneously light up from
nothing" visual with a coherent cause→effect chain. At DETECT phase
start, a small classifier-coloured icon (rifle / tank / missile /
bird) appears at the true source position, blinks, and emits
concentric "sound wave" rings outward. Drone-light-up timing ties to
ring-arrival timing. At LOCALIZE, rings stop; cloud fades in *around*
where the system thinks the source is — the gap between the visible
icon position and the cloud center is the visible localization
error. Includes a phase-narration subtitle bar at the bottom of the
map so the presenter doesn't have to narrate every beat.

**Files touched:**

- Modified: `ui/index.html` only

**Architecture:**

Three new visual layers stack on top of the existing entity layer:

```
existing render path (top → bottom):
   entity-layer (DOM)  : drones, target dots
   pulse layer (canvas): existing emitPulse() rings
   cloud layer (canvas): 95% confidence cloud
   terrain layer       : forest, grid

new additions:
   source-icon (DOM, entity-layer): classifier-coloured icon at true
                                     source position; spawned on
                                     DETECT, persists thereafter.
                                     Blink animation during DETECT.
   acoustic emission (canvas)     : concentric rings emanating from
                                     source position; emitted every
                                     ~200 ms during DETECT; expand
                                     to ~viewport radius over 1.5 s
                                     while stroke fades to 0.
   phase subtitle (DOM, footer)   : one line of plain English
                                     describing what's happening now.
```

Classification colour map (single source of truth):

```js
const CLASS_COLOR = {
  // threat
  gunshot:       '#e85c4a',   // existing --hostile red
  tank:          '#e85c4a',
  missile_launch:'#e85c4a',
  drone_hostile: '#e8a838',   // amber — hostile but lower severity
  // ambient (Session 15)
  bird:          '#4fd87a',   // existing --accent green
  dog:           '#4fd87a',
  crickets:      '#4fd87a',
  deer:          '#4fd87a',
  // unknown
  unknown:       '#e8a838',   // amber
};
```

Phase-by-phase responsibilities:

| Phase | Source icon | Rings | Cloud | Subtitle |
|---|---|---|---|---|
| PATROL | hidden | none | none | "Drones holding formation" |
| DETECT | spawn, blink | emit every 200 ms | none | "Acoustic signature detected by N sensors" |
| LOCALIZE | steady, no blink | stop, fade | fade in | "Triangulating source — CEP50 reducing" |
| DECIDE | pulse with action colour | none | held | "ROE evaluated — STRIKE / RECON / SEARCH / MONITOR" |
| RESPOND | held | none | held | "Responder dispatched / Sweep underway / …" |
| COMPLETE | held (or "neutralized" for STRIKE) | none | held | "Target neutralized / Recon complete / Sector cleared" |

**Subtasks:**

- 14.1 Icon library additions in the existing `ICONS` map for the
       new labels needed by Session 15: `bird` (small avian
       silhouette), `dog`, `crickets` (small dotted glyph), `deer`
       (antlers silhouette). Reuse existing styling.
- 14.2 New entity type `source` rendered through the existing
       `upsertEntity()` path. Position from
       `entry.source.{lat,lon}` (production tabs) or true source
       (sandbox / narrative scene with scripted truth).
- 14.3 CSS `@keyframes blink-source { 0%, 100% { opacity: 1.0;
       transform: scale(1.0) } 50% { opacity: 0.7; transform:
       scale(1.08) } }` — 0.5 s period, applied via `.source.blink`
       class. Removed at LOCALIZE start.
- 14.4 Phase hook in `tickPlayback`'s DETECT branch: at progress = 0,
       spawn source entity + start blink. Every ~200 ms thereafter
       (track via `pb.lastRingEmit`), call `emitPulse(sourceX,
       sourceY, color)` using the existing canvas pulse machinery
       — just call it from the source instead of from drones.
- 14.5 Tune ring expansion in `drawPulses` so a ring reaches the
       furthest drone at roughly DETECT-end. (Current animation
       constants probably need a small tweak — verify with the
       triangle preset.)
- 14.6 Tie drone-light-up to ring-arrival: instead of lighting all
       drones simultaneously at DETECT start, light each drone the
       moment its distance from source matches the leading-ring
       radius. Smooth fade-up over ~120 ms.
- 14.7 Phase subtitle DOM: new `<div class="phase-subtitle">` inside
       the existing `.map-wrap`, bottom-centred. Updated by a
       `PHASE_SUBTITLE` lookup keyed on `(phase, action)`.
- 14.8 Action-classification pulse on the source icon during DECIDE:
       brief colour flash matching the recommended action.
- 14.9 RESPOND outcome handling on the source icon:
       - STRIKE: at impact (RESPOND progress ≈ 0.9), source icon
         gets a "neutralized" overlay (red ☓ + 50 % opacity)
       - RECON: small camera-icon badge appears next to source
       - SEARCH: source dims slightly to suggest "still under
         investigation"
       - MONITOR (Session 15): no change; icon stays as-is
       - HOLD: no responder, no change
- 14.10 Z-order: source icon ABOVE drones ABOVE cloud ABOVE rings
        ABOVE terrain. Verify rendering order in `renderEntities()`.

**Considerations:**

- **💡 NOTE: source icon shows the DETECTED label, not true type.**
  Even when the system misclassifies (a deer flagged as "gunshot"),
  the icon shows the *classifier's* output. In sandbox, the icon
  shows the true type the user selected. In narrative tab scenes,
  the icon shows the scripted label.
- **💡 NOTE: rings emit from source, not from each drone.** Sound
  physically radiates from the source. Existing
  `emitPulse(droneX, droneY)` calls in the DETECT path can be
  removed or kept as "drone hears" secondary visuals — your call.
  Cleaner to remove and let the source-emitted rings be the only
  rings during DETECT.
- **💡 NOTE: cloud center vs icon position is the *visible
  error*.** This is the educational moment: judges see exactly how
  off the system is. Don't try to "snap" the cloud to the icon —
  the gap is the point.
- **💡 NOTE: subtitle bar is content-driven, not narration.** Use
  data from the scenario (drone count, action, CEP50) to fill in
  blanks: `"Acoustic signature detected by {N} sensors"` where N
  is the current alive count. Reads as a real system console.
- **⚠ Performance.** Ring emission every 200 ms × 1.5 s lifetime =
  up to ~8 rings concurrent. Plus existing sonar pulses. Should be
  fine on a modern laptop but worth a frame-rate check on the demo
  machine. Cap to 12 concurrent pulses if needed.

**⚠ HUMAN INPUT NEEDED:**

1. Ring emission rate (suggested 200 ms). Faster (100 ms) feels more
   urgent; slower (400 ms) feels more deliberate. Confirm.
2. Ring expansion duration (suggested 1.5 s). Tied to DETECT phase
   length (currently 1500 ms) so they align. Confirm if DETECT
   duration changes.
3. STRIKE outcome visual: red ☓ overlay (suggested) vs fade-to-grey
   vs explosion icon. Confirm.
4. Subtitle styling: monospace JetBrains Mono (existing pitch font)
   at ~14 px, dim accent colour. Confirm or tweak.

**Acceptance criteria:**

- PATROL phase: source icon hidden; no rings; subtitle says "Drones
  holding formation".
- DETECT phase start: source icon spawns at true position with
  classifier-coloured blink; rings begin emitting every 200 ms.
- Drone lights now match ring-arrival timing (visibly sequential,
  not simultaneous).
- LOCALIZE phase start: rings stop expanding (existing ones fade
  out); cloud begins fading in; source icon stops blinking.
- Gap between source icon position and cloud center is visibly
  the localization error.
- DECIDE phase: source icon pulses once in the action colour.
- RESPOND phase:
  - STRIKE → red ☓ overlay on source at progress ≈ 0.9
  - RECON → camera-icon badge appears
  - SEARCH → source dims
- Phase subtitle updates at every phase change with content-driven
  text.
- 60 fps maintained throughout (check `statFps`).

---

---

## Session 15 — Ambient (wildlife) triangulation tab

**Goal:** A dedicated tab `🐺 Wildlife · ambient` runs a bird /
crickets / dog scenario through the same pipeline as a threat, with
all green visuals and a new `MONITOR` action chip. Demonstrates
discriminative classification: the system localizes everything it
hears but only engages the right things.

**Files touched:**

- Modified: `triangulation/policy.py` (new MONITOR action,
  AMBIENT_LABELS constant)
- Modified: `triangulation/locate.py` (allow ambient via flag)
- Modified: `triangulation/AGENTS.md` (schema additions)
- Modified: `ui/index.html` (new tab, ambient color path)

**Architecture:**

Backend changes:

```
triangulation/policy.py:
  AMBIENT_LABELS = ("bird", "dog", "crickets", "deer")

  decide(cep50, gdop, label, confidence) -> Decision:
      # existing branches: HOLD, STRIKE, RECON, SEARCH
      if label in AMBIENT_LABELS:
          return Decision(action="MONITOR",
                          reason=f"{label} classified as ambient — "
                                 "non-threat",
                          severity="low",
                          weapons_release_required=False)

triangulation/locate.py:
  localize_scenario(..., triangulate_ambient=False):
      if all(e['relevant'] is False) and not triangulate_ambient:
          skip (existing behavior)
      if all(e['relevant'] is False) and triangulate_ambient:
          if label in AMBIENT_LABELS:
              localize as usual; output['classification'] = 'ambient'
          else:
              skip ('relevant=false' but not a recognised ambient)

  CLI flag: --ambient   (when set, processes ambient scenarios too)
```

New JSON field:

```
classification : "threat" | "ambient"      ← NEW
```

`threat_priority` for ambient = 0 (never competes with threats in
the priority stack).

Frontend:

- One of the 6 tabs is repurposed (or a 7th added):
  `🐺 Wildlife · ambient` with a bird/crickets/dog scenario loaded.
- All Session 14 visuals apply with green colour (bird/dog/crickets/
  deer all map to green in `CLASS_COLOR`).
- Action chip shows "MONITOR" in green.
- RESPOND phase is **skipped** (no engagement). After DECIDE, skip
  to COMPLETE. (Or run RESPOND with zero responders, just a
  subtitle "No engagement — logged.")
- Animal icon persists on the map after the scenario completes (a
  logged observation).
- Bird WAV plays during DETECT (already wired by the audio addon
  if `entry.label == "bird"` matches `AUDIO["bird"]`).

**Subtasks:**

- 15.1 `AMBIENT_LABELS` constant and `MONITOR` action in
       `policy.py`. Distinct chip colour:
       `--monitor: #4fd87a` (green; reuses --accent).
- 15.2 `decide()` returns MONITOR for any label in AMBIENT_LABELS,
       regardless of CEP/GDOP (a confident ambient is still
       ambient).
- 15.3 `_localizable()` in `locate.py` accepts a `triangulate_ambient`
       flag. When True, scenarios with `relevant: False` AND
       `label in AMBIENT_LABELS` are localised; others still
       skipped.
- 15.4 New JSON field `classification` ∈ {"threat", "ambient"}.
- 15.5 CLI flag `--ambient` on `python -m triangulation.locate`.
- 15.6 Add wildlife scenario data: pick `scenario_bird_mix.wav`
       from existing `events.json`. (May need to set
       `label: "bird"` on its rows; the input currently has
       `label: null`. One-line edit to events.json.)
- 15.7 Re-run pipeline with `--ambient` to populate
       `localizations.json` with at least one ambient entry.
- 15.8 Frontend tab: `🐺 Wildlife · ambient`. Loads the ambient
       entry. Uses Session 14's render path with green
       `CLASS_COLOR` for `label == "bird"` etc.
- 15.9 Action chip styling: green pill for MONITOR, label
       `"MONITOR · ambient signal"`.
- 15.10 RESPOND phase handling: skip outright or run-with-no-
        responders. Suggested: skip and jump to COMPLETE.
        Subtitle says "No engagement — observation logged."
- 15.11 Icon persistence: animal icon stays visible after COMPLETE
        (same as threat icon, but no neutralized overlay).
- 15.12 Audio: confirm bird WAV plays during DETECT. Tank/missile
        WAVs should NOT fire for ambient scenarios (different
        label).
- 15.13 Document: AGENTS.md schema additions for `classification`
        and the new MONITOR action.

**Considerations:**

- **💡 NOTE: don't auto-show ambient in OTHER tabs' backgrounds.**
  The temptation to "always render ambient detections faintly in
  every tab" is real and wrong. Clutters every other demo. Ambient
  belongs in its own tab so it gets attention when it's the focus
  and gets out of the way when it isn't.
- **💡 NOTE: MONITOR is operationally distinct from HOLD.** HOLD
  means "low confidence, can't act"; MONITOR means "high
  confidence, deliberate non-engagement". Different chip colour,
  different language.
- **💡 NOTE: classifier isn't real.** The "bird" label in the JSON
  comes from a CSV-style hardcoded classifier upstream, not an ML
  model. The pitch should be honest: we *display* the
  classification result; we don't *build* the classifier here.
  Add this caveat to the slide if a judge asks.
- **💡 NOTE: ambient scenarios still produce a green cloud.** It's
  tempting to skip the cloud ("we know it's a bird, why localize
  it?"). Render the cloud anyway — it shows that the system *did*
  the math and chose not to engage. That's the entire point.
- **⚠ The events.json bird scenarios have `relevant: false` and
  `label: null`.** Session 15 needs them to have `label: "bird"`
  (etc.) at minimum. Either patch the events.json directly or
  teach `_localizable` to infer the label from the scenario path
  (`scenario_bird_*` → `bird`). The path-inference approach is
  cleaner (no data edit) but adds magic.

**⚠ HUMAN INPUT NEEDED:**

1. Which animal scenario to feature? Suggested **bird** — most
   distinct from threat sounds aurally. Alternatives: dog
   (richer audio), crickets (more "ambient" feel).
2. Path-inference vs events.json edit for the label? Suggested
   **events.json edit** (one-line change, no hidden magic).
3. RESPOND phase: skip outright (suggested) or play with zero
   responders + "no engagement" subtitle? Both are valid.
4. Should ambient detections also have their own `cloud_format`
   default? Suggested: same as threat (ellipse, 95%).

**Acceptance criteria:**

- `python -m triangulation.locate --ambient` writes
  `localizations.json` with at least one entry where
  `classification == "ambient"` and `recommended_action ==
  "MONITOR"`.
- `🐺 Wildlife · ambient` tab is selectable in the UI.
- All visuals (source icon, rings, cloud, chip) are green.
- Action chip text reads "MONITOR · ambient signal".
- RESPOND phase is either skipped or shows no responder
  animation.
- Bird WAV plays during DETECT (audio addon already wired).
- After COMPLETE, the animal icon stays on the map.
- Switching to a threat tab and back to ambient correctly
  re-renders everything green.

---

---

## Session 16 — Mesh bandwidth side panel

**Goal:** Permanent top-bar strip showing live mesh bandwidth
telemetry, with click-to-inspect hex dump for the engineer-judge.
Surfaces the compression work in `mesh/` so the bandwidth
efficiency story is visible during the pitch instead of hidden in
a benchmark CLI.

**Files touched:**

- New: `triangulation/server.py` adds `/api/mesh/bandwidth` endpoint
  (wraps `mesh.metrics.get_metrics().summary()` and adds per-event
  hex dumps from `mesh.payload`)
- Modified: `ui/index.html` (top-bar strip + popover)
- Modified: `mesh/publish.py` or new `mesh/live_publisher.py` —
  small helper to fire a single tactical event from a live UI
  trigger and return the metrics delta

**Architecture:**

```
ui/index.html (top bar)
   │
   │ 1 s polling
   ▼
   GET /api/mesh/bandwidth
   │
   ▼
   triangulation/server.py
   │
   ├── on first call: load events.json + localizations.json,
   │   pre-compute per-row tactical and per-entry loc-summary
   │   sizes via mesh.payload.event_row_to_tactical / pack_loc_summary
   │
   ├── on scenario tab activation: bump running totals
   │   (the UI sends a hint when active scenario changes)
   │
   └── return {
         total_mesh_bytes: int,
         total_json_bytes: int,
         saved_bytes: int,
         saved_pct: float,
         last_packet: {
           kind: "tactical" | "loc_summary",
           bytes_mesh: int,
           bytes_json: int,
           hex_mesh: "e0 01 01 02 …",
           hex_json: "{\"label\":\"gunshot\",…}",
         },
         extrapolation: { events_per_hour: 1000, kb_per_day_mesh: 3, kb_per_day_json: 38 }
       }
```

Top-bar layout (matches existing JetBrains Mono tactical style):

```
┌──────────────────────────────────────────────────────────────┐
│ MESH 64 B   /   JSON 392 B   /   SAVED 84%        ⓘ click   │
│ TOTAL 2.4 KB sent · 28 KB saved · est 26 MB/day              │
└──────────────────────────────────────────────────────────────┘
```

Click anywhere on the strip → popover with side-by-side hex/JSON
dump and a small bar-chart of cumulative savings over the pitch.

**Subtasks:**

- 16.1 Backend: `/api/mesh/bandwidth` endpoint. Reads
       `detection/output/events.json` and `localizations.json` once
       at startup, computes per-row tactical sizes and per-entry
       loc-summary sizes via the existing `mesh.payload` helpers.
- 16.2 UI session state tracks "totals so far" — accumulates on each
       scenario tab activation, never resets unless presenter hits
       a "↺ reset bandwidth counters" button.
- 16.3 Frontend top-bar strip: monospace, two lines, click → popover.
- 16.4 Popover: side-by-side hex dump (left = mesh, right = JSON),
       small bar chart underneath showing cumulative bytes over time.
- 16.5 Extrapolation footer text — formula `events/hour × per-event
       saving × 24`, displayed as "est 26 MB/day per swarm at scale".
- 16.6 Reset button (for rehearsals). Bottom-right of the popover.
- 16.7 Performance: poll endpoint at 1 Hz, not faster. The number
       shouldn't update mid-narration.

**Considerations:**

- **💡 NOTE: this panel is read-only.** It only displays what
  `mesh.metrics` and `mesh.payload` already produce. Don't
  re-implement the compression in JS — fetch numbers from Python.
- **💡 NOTE: per-scenario delta vs running total.** Showing both is
  the right call: per-scenario for the immediate event, total for
  cumulative impact. Don't only show one.
- **💡 NOTE: extrapolation is a *talking point*, not a measured
  number.** The "26 MB/day at scale" line is calculated from
  assumed events/hour. Caveat it in the popover footer: "extrapolated
  at 1000 events/hour, your mileage may vary".
- **⚠ Don't update faster than 1 Hz.** Animation noise distracts
  from the main map. Bandwidth numbers are calm and authoritative,
  not flashing.

**⚠ HUMAN INPUT NEEDED:**

1. Extrapolation rate: 1000 events/hour suggested. Confirm or
   override (defense-realistic might be 50–200/hour during active
   engagement).
2. Top-bar strip placement: above the existing header (suggested)
   vs below it. Confirm.
3. Reset button visible always vs only in popover? Suggested
   only in popover (presenter doesn't accidentally reset mid-pitch).

**Acceptance criteria:**

- `/api/mesh/bandwidth` returns correct per-event tactical size
  (32 B + 32 B frame = 64 B on wire) and per-loc summary size
  (24 B + 32 B frame = 56 B on wire).
- Top-bar strip renders the live numbers in the tactical theme.
- Click → popover shows correct side-by-side hex dump from
  `mesh.payload`.
- Reset button zeroes the totals without breaking the page.
- Page still 60 fps with the panel polling every 1 s.

---

---

## Session 17 — Packet flight FX (toggleable)

**Goal:** Toggleable atmospheric polish on top of Session 18's mesh
topology panel. When `BANDWIDTH FX: on` in the top bar, every mesh
transmission spawns a small coloured capsule that travels along
the relevant edge in the topology view over ~400 ms. Capsule
colour by payload kind, width ∝ byte count, hover → tooltip with
packet details. Default **off** — turn on when narrating the mesh,
off otherwise.

**Files touched:**

- Modified: `ui/index.html` (toggle, animation system, hover tooltip)
- Modified: `triangulation/server.py` (add SSE `/api/mesh/events`
  stream — or extend the existing polling endpoint with a
  recent-events tail)

**Architecture:**

```
state.bandwidthFx : bool        // top-bar toggle
state.flyingCapsules : list     // active animations, each:
  { src_node, dst_node, kind, bytes, t, color, started_ms }

Animation loop:
   each frame:
     for each capsule c:
       c.t += dt / 400ms
       if c.t >= 1:    remove from list
       else:           lerp position along edge(src, dst)
                        draw rect of width ∝ bytes, color ∝ kind

Event source:
  GET /api/mesh/events?since=<ts>   (polled at 200 ms)
  → [{src_id, dst_id, kind, bytes, ts}, ...]

  When received and state.bandwidthFx == true:
    push each event onto state.flyingCapsules
```

Visual style:

| Kind | Colour | Width per byte | Note |
|---|---|---|---|
| tactical (32 B) | green `#4fd87a` | small dot (~2 px/B) | acoustic detection event |
| loc_summary (24 B) | blue `#4faec8` | small dot | localization fix |
| frame overhead (HMAC, 16 B) | purple fringe | thin trailing edge | shown as glow trail |

**Subtasks:**

- 17.1 Top-bar toggle pill `🔁 BANDWIDTH FX: off`. Click toggles
       state.bandwidthFx; pill colour reflects state.
- 17.2 Backend: SSE endpoint `/api/mesh/events` OR polled tail.
       Emits packet-sent events with src/dst/kind/bytes/ts. Easiest:
       reuse Session 18 bridge's `/api/mesh/events` if that exists,
       just filter for `kind in ("tactical","loc_summary")`.
- 17.3 Frontend animation: per-frame update of state.flyingCapsules,
       lerping along edge positions from the topology panel's
       layout.
- 17.4 Capsule rendering: rounded rectangle with a small dot, width
       ∝ bytes, colour ∝ kind. Tooltip on hover.
- 17.5 Cap concurrent animations to 20 — drop oldest if exceeded.
- 17.6 Auto-fade in last 20% of travel for smoother visual end.
- 17.7 Default state: off. Persist toggle state in `localStorage`
       across reloads (presenter sets it once, demo machine
       remembers).

**Considerations:**

- **💡 NOTE: compression happens once at source, not at every hop.**
  Animate the SAME capsule along multiple hops, not a new one at
  each. The capsule label `32 B` stays 32 B for the whole flight —
  it doesn't shrink at relay nodes.
- **💡 NOTE: this is decoration.** Session 16's side panel is the
  credibility. If a judge asks "are those packets real?", point at
  the panel numbers; the capsules are just visualization of what
  the numbers report.
- **💡 NOTE: default off matters.** Most of the demo isn't about
  the mesh. Constant capsule animation during triangulation /
  kill-button / sandbox moments would distract.
- **⚠ Performance.** Cap concurrent capsules. With 20 simultaneous
  rounded-rect renders at 60 fps the GPU is fine; with 200 it isn't.

**⚠ HUMAN INPUT NEEDED:**

1. Default state: off (suggested) vs on. Confirm.
2. Capsule shape: small rounded rect (suggested), pill, or glowing
   dot. Confirm.
3. Where capsules render: only in the mesh topology panel
   (suggested, less clutter) vs also overlaid on the main map.
4. Persist toggle state in localStorage? Suggested yes.

**Acceptance criteria:**

- Toggle pill in top bar; click flips state.
- When on, every mesh send spawns a capsule that lerps along the
  correct edge in the topology panel.
- Capsule colour and width reflect payload kind and byte count.
- Hover shows tooltip with packet details.
- 60 fps maintained with up to 20 concurrent capsules.
- When off, no animation but the side panel (Session 16) keeps
  updating numbers.
- Toggle state persists across page reload.

---

---

## Session 18 — Live Ops tab (live event injection)

### Goal

A new tab `🎮 LIVE OPS` where the demo runs as a continuous, live,
reactive simulation. **N drones** patrol the map at all times.
The operator drops events from a sidebar — `🔫 GUNSHOT`, `🚜 TANK`,
`🚀 MISSILE`, `🦌 WILDLIFE` — by clicking a button then clicking
the map. The backend computes per-drone detection times from real
geometry, selects the **3 closest alive drones**, runs the full
pipeline (math → ROE → response), and the UI animates the result
in real time. Kill-drone works in this tab too; when 2 drones are
left, the system gracefully falls back to the 2-drone hyperbola
fix (Session 11) and forces a SEARCH. No scripted scenarios. No
pre-baked JSON. This is the "system is actually live" proof.

### Why this matters

Scripted scenario tabs are rehearsed pitch content. Live Ops is the
"hand-the-laptop-to-the-judge" tab. A judge dropping a gunshot at a
random location and watching the system react in real time is
qualitatively different from a replay. It also lights up three
otherwise abstract stories at once: the **discriminative classifier
story** (drop a wildlife event → green MONITOR action), the
**graceful-degradation story** (kill drones mid-engagement → see
ellipse collapse to hyperbola+wedge → ROE downgrades to SEARCH live),
and the **N-drone scaling story** (the system picks the best 3 of N,
not the only 3 it has).

### Dependencies

Hard prerequisites (this session won't function correctly without):

- **Session 7** — Tab bar + phase stepper (this is a new tab in the
  existing bar; the phase machine drives the live animation too)
- **Session 8** — Live error sliders + Flask backend (this session
  extends the Flask backend; the sliders work in Live Ops too)
- **Session 11** — 2-drone bearing-only localization (this session's
  graceful-degradation depends on the hyperbola fix existing)
- **Session 13** — Kill-drone button (this session reuses the
  killed-drones state machine)
- **Session 14** — Source icon + acoustic cones (this session reuses
  the cone-emission visual machinery)

Soft prerequisites (nice-to-have but Live Ops works without):

- **Session 15** — Ambient (wildlife) triangulation tab (Live Ops
  ships its own wildlife dropper; if Session 15 is in, the colour
  conventions match exactly)
- **Session 12** — Multi-scene narrative tab (independent; both can
  coexist as separate tabs)

### Files touched

- New: `triangulation/live_ops.py` — server-side live state + helpers
- New: `triangulation/classifier.py` — Classifier protocol + impls
- Modified: `triangulation/server.py` — adds `/api/live/*` endpoints
- Modified: `ui/index.html` — new tab, drop sidebar, click handling
- Modified: `triangulation/locate.py` — minor: nothing functional
  changes; `localize_scenario` already handles N-row groups

### Architecture

The live state lives **server-side** in a singleton in
`triangulation/server.py`. The UI is a thin reactive client.

```
┌─ User clicks 🔫 GUNSHOT, then clicks map at (lat, lon) ───────────────┐
│                                                                       │
│ POST /api/live/event { label: "gunshot", lat, lon }                  │
│   │                                                                   │
│   ▼                                                                   │
│ LiveOpsState.handle_event(label, lat, lon)                           │
│   │                                                                   │
│   ├── 1. snapshot drone roster: positions + alive flags              │
│   │                                                                   │
│   ├── 2. for each drone, compute event_time_ns:                      │
│   │        t = base_ns                                                │
│   │          + ||drone_xy - event_xy|| / C       (real geometry)     │
│   │          + N(0, sigma_t_s)                   (jitter)            │
│   │                                                                   │
│   ├── 3. classifier.classify(truth_label, audio?)                    │
│   │        → (predicted_label, confidence)                            │
│   │      [PerfectClassifier returns truth; MLClassifier is a stub]   │
│   │                                                                   │
│   ├── 4. select 3 closest ALIVE drones (or 2, or 1, or 0)            │
│   │                                                                   │
│   ├── 5. branch by alive count:                                      │
│   │     ─ ≥ 3 alive → localize_scenario (existing 3-drone math)      │
│   │     ─ 2 alive  → solver_2drone (Session 11 hyperbola+wedge)      │
│   │     ─ 1 alive  → return INSUFFICIENT_SENSORS                     │
│   │     ─ 0 alive  → return INSUFFICIENT_SENSORS (no drones)         │
│   │                                                                   │
│   └── 6. return localization-shape entry + per-drone arrival times   │
│           + predicted_label + true_label                              │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘

┌─ UI receives the result ──────────────────────────────────────────────┐
│ - immediately play audio for the event (Session 7's AudioEngine)     │
│ - build an in-memory "frame" from the result (same shape as the      │
│   pre-baked frames from buildFrames())                                │
│ - feed the frame to the existing tickPlayback phase machine          │
│ - per-drone "light up" pulses fire at their real computed arrival    │
│   times (so you visibly see the wavefront reach drones sequentially) │
│ - source icon, cloud, action chip, banner — all reuse Session 14    │
└───────────────────────────────────────────────────────────────────────┘
```

### State model (server)

```python
# triangulation/live_ops.py

@dataclass
class Drone:
    id: str                                # "drone_1" .. "drone_N"
    base_lat: float                        # patrol centre
    base_lon: float
    alive: bool = True
    # current patrol position (updated when /api/live/state is polled)

@dataclass
class LiveEvent:
    id: str                                # uuid hex
    label: str                             # "gunshot" | "tank" | "missile_launch" | "wildlife"
    lat: float
    lon: float
    t_drop_ns: int
    result: dict | None = None             # localizations.json-shape entry

@dataclass
class LiveOpsState:
    drones: list[Drone]
    classifier: Classifier
    sigma_t_ms: float = 6.0
    sigma_pos_m: float = 12.0
    events: list[LiveEvent] = field(default_factory=list)
    t_start_ns: int = field(default_factory=lambda: time.time_ns())

    def alive_drones(self) -> list[Drone]:
        return [d for d in self.drones if d.alive]
```

Single in-process singleton. No persistence to disk (live ops is
session-scoped). Reset endpoint zeroes the events list and revives
all drones.

### Classifier abstraction (future-proof for the ML team)

```python
# triangulation/classifier.py

class Classifier(Protocol):
    def classify(self, truth_label: str,
                 audio: bytes | None = None) -> tuple[str, float]:
        """Return (predicted_label, confidence_in_0_to_1)."""
        ...

class PerfectClassifier:
    """v1 default. Always returns the truth with high confidence."""
    def classify(self, truth, audio=None) -> tuple[str, float]:
        return truth, 0.95

class MLClassifier:
    """v2 stub. When the ML team ships a model, plug it in here.

    Should accept a synthesised or real audio sample, run inference,
    return (predicted_label, confidence). The Live Ops backend
    doesn't care what's inside this class — it just calls classify().
    """
    def __init__(self, model_path: str):
        # load ONNX / PyTorch / whatever
        raise NotImplementedError("ML classifier not yet shipped")
    def classify(self, truth, audio) -> tuple[str, float]:
        raise NotImplementedError
```

Switching is via env var or query string:
`?detection_mode=perfect` (default) | `?detection_mode=ml`.

The UI shows a small badge `DETECTION: perfect` (green) or
`DETECTION: ML` (blue). The misclassification narrative — drop a
tank, classifier guesses "drone" → recon dispatched → reclassified
correctly as "tank" → ROE escalates — naturally lights up once
`MLClassifier` is real. v1 ships perfect-only.

### Drone roster + patrol

Default: 5 drones in a loose pentagon centred on
`(62.412, 25.752)`, radius ~150 m. Patrol motion is a slow
sinusoidal drift (~30 m amplitude, ~20 s period) computed
server-side; the UI polls position every 500 ms and lerps for
smoothness.

```python
def patrol_position(drone: Drone, t_now_ns: int) -> tuple[float, float]:
    """Slow sinusoidal drift around base. Visual only."""
    t_s = t_now_ns / 1e9
    seed = int(hashlib.md5(drone.id.encode()).digest()[0])
    dlat = 0.0003 * math.sin(t_s / 5.0 + seed)        # ~30 m
    dlon = 0.0003 * math.cos(t_s / 6.5 + seed * 2)
    return drone.base_lat + dlat, drone.base_lon + dlon
```

Drone count is configurable via `/api/live/config` (3..10 supported).

### N-drone selection: pick best 3

```python
def select_drones_for_event(event_lat, event_lon,
                            alive_drones: list[Drone],
                            max_drones: int = 3) -> list[Drone]:
    """Return the 3 (or fewer) alive drones closest to the event.

    Distance computed in the local plane (equirectangular projection
    around the event point, accurate enough for ~1 km).
    """
    if not alive_drones:
        return []
    def dist(d: Drone) -> float:
        return distance_m(d.last_lat, d.last_lon, event_lat, event_lon)
    return sorted(alive_drones, key=dist)[:max_drones]
```

The existing `localize_scenario(group)` already accepts any list of
event rows; it doesn't hardcode "3 drones". So this selection step
is the only new logic between live drone roster and the math.

### 2-drone fallback

When `len(alive_drones) == 2`:

- The 3-drone solver would crash (or return garbage).
- Instead, call `solver_2drone.hyperbola_fix(events, drone_positions)`
  from Session 11.
- ROE policy automatically forces SEARCH (you can't STRIKE on a
  curve — Session 11 already enforces this).
- UI renders the hyperbola curve + wedge band instead of an ellipse,
  using Session 11's frontend renderer.

### 1-drone or 0-drone fallback

- 1 alive: return an output entry with `fix_kind: "none"`,
  `recommended_action: "INSUFFICIENT_SENSORS"`, `source: null`,
  `cloud_latlon: []`. UI shows a banner: `SENSOR LOSS — fix
  unavailable. Single-sensor bearing requires RSSI mesh.`
- 0 alive: same banner; no detection animation at all.

These are not error states — they're real operational outcomes.
Don't crash; render them honestly.

### Endpoints

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/live/event` | `{label, lat, lon}` | localization entry + arrival_times_per_drone + predicted_label + true_label |
| `POST /api/live/kill_drone` | `{drone_id}` | `{ok: true, alive_count: N}` |
| `POST /api/live/revive_drone` | `{drone_id}` | `{ok: true, alive_count: N}` |
| `POST /api/live/reset` | `{}` | `{ok: true}` (clears events, revives all drones) |
| `GET /api/live/state` | — | `{drones: [{id, lat, lon, alive}], events_count, classifier_mode}` |
| `POST /api/live/config` | `{sigma_t_ms?, sigma_pos_m?, detection_mode?, drone_count?}` | `{ok: true, config}` |

All endpoints return JSON. All accept JSON bodies (or query strings
for `GET`).

### UI: drop UX

Sidebar **replaces** the scenario list when the LIVE OPS tab is
active. Layout:

```
┌─ DROP EVENT ──────────────────────────┐
│  🔫 GUNSHOT          (threat)         │
│  🚜 TANK             (threat)         │
│  🚀 MISSILE LAUNCH   (threat)         │
│  🦌 WILDLIFE         (ambient)        │
├───────────────────────────────────────┤
│  💀 KILL DRONE       (per-drone pill) │
│  ❤️ REVIVE ALL                         │
│  🔄 RESET ALL                          │
├───────────────────────────────────────┤
│  DETECTION: perfect ⓘ                 │
│  ALIVE: 5/5                            │
│  EVENTS: 3                             │
└───────────────────────────────────────┘
```

Interaction flow:

1. User clicks `🔫 GUNSHOT`. The button enters "armed" state
   (highlighted border, cursor changes to crosshair over the map).
2. User clicks anywhere on the map. The screen → lat/lon
   conversion uses the existing `bounds` projection inverse.
3. `POST /api/live/event` fires. Cursor reverts. Button un-arms.
4. Backend responds. UI plays audio + starts phase animation.
5. Old fix (if any) fades out as the new one starts.

Cancellation:

- `ESC` cancels armed mode.
- Clicking the same button again cancels.
- Right-click on map also cancels.

KILL DRONE works the same way: click `💀 KILL DRONE`, then click
a drone icon. The icon gets the existing red-☓ overlay from
Session 13.

### No concurrent events (v1)

Per user direction: when a new event drops, the previous fix is
cleared (fades out over ~500 ms). Only one active fix at a time.
v2 could add a queue or parallel pipelines; not for v1.

### Audio (fires at drop time)

Per user direction: audio fires the instant the event is dropped,
not when the wavefront reaches each drone. Reuses Session 7's
`AudioEngine.playEvent(label)` directly. No new audio code.

The per-drone wavefront-arrival timing still drives the **visual**
drone-light-up animation (Session 14's acoustic cones radiate from
the drop point at sound speed). The audio just doesn't sync to it —
audio fires now, cones radiate physically. Acceptable tradeoff for
simplicity.

### Tab-switch behaviour

- Switching from LIVE OPS to another tab: **state preserved**
  server-side. Events list, drone roster, kill states all hold.
- Switching back: pick up where you were. No animation replays.
- The kill-drone state from Session 13's other-tabs is **separate**:
  killing a drone in scripted-tab-1 doesn't kill it in LIVE OPS.
  LIVE OPS uses `LiveOpsState.drones`; other tabs use their own.

### Default scene

On first activation of LIVE OPS tab (or after RESET ALL):

- 5 drones spawn in a loose pentagon around `(62.412, 25.752)`.
- Patrol drift starts.
- Events list is empty.
- Classifier is `PerfectClassifier`.
- σ_t = 6.0 ms, σ_pos = 12 m (same as scripted scenario defaults).

### Phase machine reuse

Live Ops doesn't add new phases. It builds a synthetic "frame"
from the backend response and feeds it to the existing phase loop:

```js
// On /api/live/event response:
const fakeFrame = {
  drones: result.drones_used,            // for entity rendering
  source: result.source,                 // for source icon
  cloud_latlon: result.cloud_latlon,     // for cloud rendering
  cep50_m: result.cep50_m,
  recommended_action: result.recommended_action,
  // ... full localizations.json-entry shape ...
  arrival_times_ms: result.arrival_times_per_drone,  // NEW: for cone timing
};
state.activeFrame = fakeFrame;
state.step = 0;       // PATROL — drones in place
state.stepProgress = 0;
state.autoplay = true; // auto-advance through phases for live ops
```

The phase tick driver does the rest. Phases run at their normal
durations.

### Per-drone wavefront arrival visualisation

When the cones radiate from the source (Session 14's renderPhase
`DETECT` branch), each drone lights up when the **leading cone
radius** crosses its position. With Live Ops, the "arrival time"
isn't synthetic — it's the real distance/C computed by the backend.
So the drone-light-up sequence faithfully mirrors physics.

Per-drone label appears: `drone_3   t = +146 ms` (relative to first
detection), reusing the same machinery as scripted tabs.

### Subtasks

Backend (Python):

- 18.1 `triangulation/classifier.py` with `Classifier` Protocol +
       `PerfectClassifier` + `MLClassifier` stub (raises NotImplemented).
- 18.2 `triangulation/live_ops.py` defining `Drone`, `LiveEvent`,
       `LiveOpsState`. Includes `patrol_position()`,
       `select_drones_for_event()`, `compute_event_arrivals()`,
       `handle_event()`.
- 18.3 `LiveOpsState.handle_event()` orchestrates: snapshot, jitter,
       select, branch by alive count, call `localize_scenario()` or
       `solver_2drone.hyperbola_fix()` or return INSUFFICIENT_SENSORS.
- 18.4 Server endpoint `POST /api/live/event` (returns full
       localization entry + arrival_times_per_drone).
- 18.5 Server endpoints `POST /api/live/kill_drone`,
       `POST /api/live/revive_drone`, `POST /api/live/reset`.
- 18.6 Server endpoint `GET /api/live/state` for UI polling
       (drones with patrol positions, event count, alive count).
- 18.7 Server endpoint `POST /api/live/config` for sigma and
       detection-mode tuning.
- 18.8 Singleton instantiation at server startup with 5 drones in
       default pentagon.
- 18.9 Unit tests: pick-best-3 logic, 2-drone fallback, 1-drone
       INSUFFICIENT_SENSORS, classifier swap.

Frontend (`ui/index.html`):

- 18.10 New tab `🎮 LIVE OPS` in the existing tab bar (Session 7's
        tab framework).
- 18.11 Sidebar swap: when LIVE OPS active, render drop-event buttons
        instead of scenario list.
- 18.12 Cursor-crosshair drop mode; map click → backend POST.
- 18.13 ESC / right-click / same-button cancels drop mode.
- 18.14 Poll `GET /api/live/state` at 2 Hz; lerp drone positions
        client-side for smoothness.
- 18.15 Render N drones (no hardcoded `drone_1/2/3`); reuse existing
        drone entity rendering.
- 18.16 On event POST response, build synthetic frame, feed to
        phase machine, start animation.
- 18.17 Previous fix fades out (~500 ms) when new event arrives.
- 18.18 `DETECTION: perfect` badge + alive-count + event-count
        readouts in sidebar.
- 18.19 INSUFFICIENT_SENSORS banner when fix_kind == "none".
- 18.20 Kill-drone integration: kill button works in LIVE OPS tab;
        affects `LiveOpsState.drones[i].alive`.

ML hookup (deferred to when classifier ships):

- 18.21 (FUTURE) Implement `MLClassifier.__init__` to load model.
- 18.22 (FUTURE) Implement `MLClassifier.classify()` with real
        inference. Synthesize audio from truth_label or use
        recorded clip.
- 18.23 (FUTURE) Misclassification narrative: when predicted ≠ truth
        AND confidence is low, ROE downgrades to RECON; recon drone
        captures imagery; "reclassification" event upgrades back to
        STRIKE. UI shows a badge: `RECLASSIFIED: drone → tank`.

### Considerations

- **💡 NOTE: Backend is the source of truth for drone positions
  during Live Ops.** The UI just renders. Don't compute patrol
  positions client-side — they'd drift out of sync with what the
  backend uses for arrival-time computation.
- **💡 NOTE: `localize_scenario()` doesn't change.** It already
  takes a list of N event rows. The N-drone change happens
  upstream in `select_drones_for_event()`.
- **💡 NOTE: ML classifier is FUTURE-PROOF, not built.** v1 ships
  `PerfectClassifier` only. The abstraction exists so the ML team
  can drop in their model later without touching `live_ops.py`.
- **💡 NOTE: audio fires at drop time, not per-drone arrival.**
  Per user direction. Simpler. Visual cone radiation still uses
  real arrival times.
- **💡 NOTE: tab switching preserves backend state.** The
  `LiveOpsState` singleton lives across tab switches. Reset is
  explicit (button).
- **💡 NOTE: pick-best-3 uses Euclidean distance in local
  metres.** Project the event lat/lon to local plane around the
  event, project each drone too, sort by distance. Existing
  `projection.py` helpers do this.
- **💡 NOTE: 2-drone fallback is automatic.** Don't add a "switch
  to 2-drone mode" toggle. The system picks the math by alive
  count. That's the demo: kill a drone, watch the math change
  itself.
- **⚠ Concurrency: out of scope for v1.** New event clears the
  previous fix. If the user clicks two events in rapid succession,
  only the second is rendered. v2 could add a queue.
- **⚠ Patrol rate cap.** UI polls at 2 Hz, backend computes patrol
  on each poll. If many concurrent UI clients connect, scale via
  caching (one position snapshot per 500 ms). v1 is single-client
  so this doesn't matter.

### ⚠ HUMAN INPUT NEEDED

1. **Default drone count.** Suggested 5 (loose pentagon). Confirm
   or override (3 / 5 / 7).
2. **Patrol radius / drift speed.** Suggested ~30 m amplitude
   over ~20 s period (slow, atmospheric). Confirm.
3. **Drop UX.** Suggested click-button-then-click-map. Alternative
   is drag-drop (icon from sidebar onto map). Click-then-click is
   simpler; drag-drop is sexier. Confirm.
4. **Audio at drop vs at first-arrival.** Confirmed by user: at
   drop. Locked in.
5. **Default detection mode.** Suggested `perfect`. Confirm.
6. **Should the source icon at drop position remain visible
   indefinitely (as a "logged contact")?** Suggested: fade out
   after the COMPLETE phase ends, ~10 s post-drop. Confirm.
7. **Wildlife event label.** Suggested `"bird"` (uses Session 15's
   green styling). Alternative: a generic `"wildlife"` label that
   the policy maps to AMBIENT. Confirm.
8. **N-drone upper limit.** Suggested 10 (sidebar pills get crowded
   beyond that). Confirm.

### Acceptance criteria

- New tab `🎮 LIVE OPS` is selectable; sidebar swaps to drop-events
  panel.
- Default scene: 5 drones drift in a pentagon. UI updates positions
  at ~2 Hz, animation smooth (no jitter).
- Click `🔫 GUNSHOT`, click map → event drops, audio plays
  immediately, source icon appears, cones radiate, 3 closest drones
  light up at real wavefront-arrival times, cloud fades in, action
  chip + banner appear, responder animation plays.
- Click `🦌 WILDLIFE` → all visuals green, MONITOR action, no
  responder dispatch.
- Kill 1 drone → next event still triangulates with the closest 3
  of the remaining 4 (uses ellipse fix).
- Kill 2 drones (down to 3 alive) → still uses 3-drone math.
- Kill 3 drones (down to 2 alive) → next event renders a hyperbola
  + wedge (Session 11), action chip SEARCH (Session 9).
- Kill 4 drones (down to 1) → next event shows
  INSUFFICIENT_SENSORS banner; no fix drawn.
- RESET ALL → all drones revived, events cleared.
- Switching tabs and back → state preserved.
- `DETECTION: perfect` badge always visible.
- `PerfectClassifier` returns the dropped label with confidence
  0.95; UI's classifier-mode hook is wired and ready for ML swap.

---

---

## Out of scope (for the avoidance of doubt)

These come up naturally in discussion but are deliberately excluded
from these sessions:

- **ML-based audio classifier ON THE CRITICAL PATH.** Session 18
  ships the abstraction (`Classifier` protocol) so the ML team can
  drop in `MLClassifier` later, but v1 always uses `PerfectClassifier`.
  No demo timeline depends on ML existing.
- **Moving / tracked target reconstruction.** Bursts from the same
  location compound nicely, but a Kalman tracker is a multi-day
  build. Skip.
- **Actual Kova mesh integration.** Documentation only (Session 6).
  Implementing the mesh is its own multi-day track and not what this
  team is doing.
- **3D localisation.** Altitude is read from `position.alt_m` but the
  pipeline is 2D. Skip.
- **Real surveillance imagery.** Session 4 uses placeholders.

## Cross-session conventions checklist

For Sonnet to keep consistent across all sessions:

- Use the existing CSS custom-properties palette (`--accent`,
  `--warn`, `--hostile`, etc.). Don't introduce new top-level
  colours.
- New JSON fields go in by adding, not renaming. Old consumers must
  keep working.
- New phase additions to `PHASE_ORDER` go in the natural place in the
  kill chain (transit → listen → localize → respond → hold).
- All new modules in `triangulation/` follow the
  `from __future__ import annotations` + type-hints style already in
  `core/`.
- Update `AGENTS.md` whenever the schema or module map changes.
- All new pipeline knobs are surfaced as CLI flags AND module-level
  constants. Two places to find them is one too few.