# Triangle

GPS-denied acoustic TDOA triangulation for UAV swarms, with ROE decision support, compact mesh telemetry, and an operator live map.

**Live demo:** https://triangle-demo.onrender.com  
**Repository:** https://github.com/gaianardella/triangle-demo

---

## Quick start

Full UI (σ sliders, mesh panel, sandbox) — **use this**:

```bash
pip install -r requirements.txt
python -m triangulation.server
```

Open **http://localhost:5050/** in your browser.

The server loads precomputed localizations and serves the UI + API. Hard refresh after code changes: **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows/Linux).

### Public demo (Render)

Stays online when your laptop is off:

1. Push `main` to GitHub: https://github.com/gaianardella/triangle-demo  
2. [Render Dashboard](https://dashboard.render.com/) → connect the repo (or use `render.yaml` Blueprint)  
3. Share the service URL, e.g. **https://triangle-demo.onrender.com/**

### Legacy static UI (no API)

σ sliders, sandbox, and live recompute **will not work** — map only:

```bash
python3 -m http.server 8080
# Open http://localhost:8080/ui/index.html
```

Prefer `python -m triangulation.server` on port **5050**.

### Regenerate localizations

Only needed after changing detection or triangulation logic:

```bash
python -m triangulation.locate \
  --in detection/output/events.json \
  --out detection/output/localizations.json \
  --pretty
```

See [Audio pipeline](#audio-pipeline) for the full detect → locate flow.

---

## UI operator guide

Triangle’s console is a single-page map (Finnish forest sector). On load it enters **LOCALIZE** mode and loads five precomputed scenarios.

### Interactive tutorial (judges)

A **step-by-step spotlight tour** opens **automatically** when the map loads (before the demo runs).

| Control | Action |
|---------|--------|
| **Next** / **Tab** | Next tour step |
| **Back** / **Shift+Tab** | Previous step |
| **Exit** (last step) | End tour and **explore the interactive demo** (scenario 1 starts with AUTO on) |
| **Skip tutorial** | Same as Exit — top-right and bottom-left on every step |
| **GUIDE** (header) | Reopen the tour anytime |
| **?** | Reopen the tour |

- Add `?notour=1` to the URL to skip the auto-tour and go straight to the demo.
- Add `?guide=1` to force the tour on reload.

The tour walks through: scenario cards → phase stepper → playback controls → map → mesh strip → σ sliders → sandbox → **GUIDE** button.

### Layout overview

| Area | What it shows |
|------|----------------|
| **Header** | Title, mesh bandwidth strip (clickable) |
| **Left panel** | Scenario cards, phase stepper, sandbox, phase label |
| **Map** | Drones, threat, confidence cloud, ROE banner, recon camera feed |
| **Bottom bar** | Phase controls (PREV / NEXT / AUTO / RESET) |
| **Right panel** | σ sliders, live CEP50/GDOP/ROE readout, drone status, event log |
| **Footer** | Drone/hostile counts, FPS |

---

### Scenario cards (left panel)

Five cards — click any card to **switch scenario** and restart from PATROL with **AUTO** enabled.

| # | Scenario | ROE chip | What it demonstrates |
|---|----------|----------|----------------------|
| 1 | Missile Launch Detected | RECON | UCAS/missile launch, good geometry |
| 2 | Armor Contact — Tank Engine | RECON | Tight ellipse, low GDOP |
| 3 | Gunfire Localized | RECON | Small-arms, engagement-grade CEP50 |
| 4 | FALSE ALARM — Wildlife Noise | F/A | Bird misclassified; no engagement |
| 5 | Poor Geometry — Hold Fire | HOLD | High GDOP / large CEP50 |

Each card also has a **⊞** button (top-right of the card) to open that scenario directly in **Sandbox** (see below).

---

### Mission phases

The mission runs through six phases (dots in the left stepper light up as you progress):

1. **PATROL** — standby (skipped instantly when a scenario starts with AUTO on)
2. **DETECT** — acoustic event, sonar rings, mesh tactical packet
3. **LOCALIZE** — TDOA confidence cloud / ellipse on the map
4. **DECIDE** — ROE banner (STRIKE / RECON / SEARCH / HOLD)
5. **RESPOND** — recon drone, camera feed, or strike/search animation
6. **COMPLETE** — scenario freezes; use **RESET** to replay

The subtitle bar under the map describes the current phase in plain language.

---

### Phase controls (bottom of map)

| Button | Action |
|--------|--------|
| **⏪ PREV** | Go back one phase (clears recon feed, ROE banner, respond animations as needed) |
| **▶ NEXT** | Advance one phase; if an animation is running, snaps it to the end first |
| **⏵ AUTO** | Toggle automatic phase advance (on by default; green when active). When on, phases progress on their own after each animation finishes |
| **⟲ RESET** | Return to **PATROL** for the current scenario; **AUTO stays on** |

At **COMPLETE**, AUTO turns off and the scenario stays frozen until you press **RESET** or pick another card.

---

### Keyboard shortcuts

**During the tutorial:** **Tab** / **Shift+Tab** step through the tour; other map shortcuts are disabled.

**During the demo:**

| Key | Action |
|-----|--------|
| **→** | Next phase (same as **NEXT**) |
| **←** | Previous phase (same as **PREV**) |
| **Space** | Toggle **AUTO** |
| **R** | **RESET** current scenario |
| **K** | Cycle “kill” state on listening drones (see Drone status) |
| **?** | Open the tutorial |
| **Esc** | Close tutorial, or exit **Sandbox** |

Shortcuts are ignored while focus is in a text input.

---

### Mesh bandwidth strip (header, centre)

Click the strip to open the **mesh vs JSON** popover:

- Side-by-side **hex dump** of the last mesh frame vs equivalent JSON
- Session totals and compression **% saved**
- **↺ RESET COUNTERS** — zero the session byte counters

Counters increment when tactical events fire at **DETECT** and localization summaries at **LOCALIZE**.

---

### Error parameters (right panel)

Two sliders recompute the current scenario’s uncertainty **live** (calls the backend sweep API):

| Slider | Range | Effect |
|--------|-------|--------|
| **σ_t — timing error** | 0.001 ms → 20 ms (log scale) | Timing jitter → CEP50, cloud size, GDOP |
| **σ_pos — position error** | 0 → 50 m | Drone position uncertainty |

Readout below the sliders shows **CEP50**, **Zone**, **GDOP**, and recommended **Action**. The mini chart plots σ_t vs CEP50 with the current operating point.

---

### Drone status (right panel, during LOCALIZE+)

Appears once a scenario is running. **Pills** list each listening UAV ID:

- **Click a pill** — mark that drone as killed (simulates asset loss)
- **Click again** — restore it
- **[K]** — cycle kills one drone at a time; press again when all are killed to restore all
- **RESTORE ALL** — clear all kills

Killing drones updates the fix via the API (bearing-only / insufficient sensors scenarios).

---

### Sandbox (left panel)

**ENTER SANDBOX** — planning mode for the current (or ⊞-selected) scenario:

- **Drag** amber source star and green drone icons on the map
- Live **CEP50 / error / ROE** in the sandbox stat line (backend `/api/sandbox/localize`)
- Banner: *“SANDBOX — drag drones & source · ESC to exit”*

**EXIT SANDBOX** (or **Esc**) returns to the normal mission view.

---

### Map overlays (automatic)

These are not buttons; they appear during the mission:

| Element | When |
|---------|------|
| **Scenario intro card** | Brief title/purpose at scenario start (hidden when DETECT begins) |
| **ROE banner** | DECIDE / RESPOND — colour by action (red hold, amber recon, green false alarm) |
| **Recon camera feed** | RECON respond — still image when the recon UAV enters the red confidence cloud |
| **Strike / search animations** | RESPOND for STRIKE or SEARCH ROE |
| **Confidence cloud** | LOCALIZE onward (red ellipse or bearing wedge) |

---

### Event log (right panel)

Scrollable timestamped messages: load status, phase changes, mesh events, telemetry lines, sandbox/API errors.

---

## Audio pipeline

```bash
# Generate multi-UAV scenario WAVs
python detection/build_scenarios.py --all

# Edge classifier → events.json
python detection/detect_audio.py --folder data/scenarios

# Triangulation → localizations.json (optional if using committed output)
python -m triangulation.locate --in detection/output/events.json \
  --out detection/output/localizations.json --pretty
```

See `detection/output/events.json` for detection payloads. ML embedding path: `detection/classify_embed.py --fit` then `detection/eval_scenarios.py`.

---

## Project layout

```
triangle-demo/
├── ui/index.html              # Operator live map
├── triangulation/server.py    # Flask app + static UI
├── detection/                 # Audio classify + scenarios
├── mesh/                      # Compact binary mesh frames
├── detection/output/localizations.json
├── pitch/                     # Promo video, slides, sample WAV (not used at runtime)
├── render.yaml                # Render.com deploy
└── SUBMISSION.md              # Hackathon description
```

---

## Architecture

- **[Mesh network](docs/MESH_ARCHITECTURE.md)** — frame formats, bandwidth model, degradation matrix

---

## Credits

See `CREDITS.md`. Map tiles © Maanmittauslaitos (MML WMTS).
