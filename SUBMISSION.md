# Triangle — GPS-Denied Acoustic Localization on a Bandwidth-Tight UAV Mesh

## The problem

In GPS-denied or contested environments, forces still need to **know where a threat is** and **what to do next**, without flooding a fragile mesh with heavy JSON telemetry. Gunfire, launches, and vehicle noise arrive at different UAVs at slightly different times; turning those milliseconds into metres and rules-of-engagement (ROE) decisions is hard—and doing it **fast and small on the wire** is harder.

### Why listen instead of only look?

A drone camera sees a narrow cone, blocked by trees, smoke, and night, only at short range. **Sound needs no line of sight**—it carries around cover; several UAVs hear the same event from different bearings, often before any imager has a clear target. Triangle uses acoustics as the long-range layer; recon imagery and the map follow once sound says *what* and *where*.

## The solution

**Triangle** is an integrated acoustic intelligence platform for UAV swarms. It ingests multi-drone acoustic detections, fuses them with **time-difference-of-arrival (TDOA) triangulation** and **Monte Carlo uncertainty**, recommends **ROE-aligned actions**, and distributes situational awareness over a **compact binary mesh protocol**, all through a single **operator live map** built for GPS-denied forest and border sectors.

## How Triangle works

### Acoustic sensing at the edge → distributed triangulation

Triangle follows a **sense → classify → fuse** chain. Each UAV listens in the forest sector; classified contacts become per-drone events and feed **distributed acoustic triangulation**.

**Operational classifier** — runs at the edge on every UAV feed:

- UAV noise suppression (notch harmonics, spectral subtraction) so rotor hum does not mask cues.
- Sliding windows scored with energy, crest factor, FFT bands, and spike duration for gunfire, missile, tank, and drone.
- Temporal voting and wildlife/clutter rejection before any fix.

**ML path:** YAMNet 1024-D embeddings vs class prototypes (cosine similarity); **~67% accuracy** on six held-out multi-UAV scenario mixes (gunshot, missile, tank vs bird/crickets/dog negatives).

Only relevant, label-confident detections are forwarded with event time, drone position, and sensor uncertainty, then time-synchronized across the swarm before triangulation.

### End-to-end pipeline

Per-threat **multi-UAV WAV mixes** are classified into events, fused into geodetic fixes with uncertainty, then published as compact mesh frames and on the **Flask live map** for playback and sandbox replanning—raw audio stays on the node, not the link.

### Localization engine

Triangle groups detections by contact and computes a threat fix with **three or more drones**, or a **two-drone bearing solution** when geometry allows. Every fix includes geodetic coordinates, **CEP50**, **GDOP**, localization confidence, and a **95% uncertainty ellipse** from propagated timing and position error—not a single optimistic point. The system degrades gracefully when assets are lost (insufficient sensors, bearing-only locus).

### ROE decision support

A deterministic policy engine maps fix quality and threat class to **STRIKE**, **RECON**, **SEARCH**, or **HOLD**, with rationale and priority. Wildlife and weak fixes stay on HOLD; military contacts escalate only when ellipse size and GDOP pass engagement thresholds.

### Mesh-native telemetry

Triangle replaces bulky JSON with wire-efficient frames: **~32-byte tactical events** and **~24-byte localization summaries**. Operators see real-time compression ratios, packet inspection, and bandwidth saved per mission—critical when the link is contested or jammed.

## Operator console

The **Triangle Live Map** (Finnish sector basemap) gives commanders a continuous mission picture across five profiles: missile/UCAS launch (RECON), armoured vehicle (tight ellipse), gunfire (engagement threshold), wildlife false positive (no engagement), and degraded geometry (HOLD).

**Mission cycle:** PATROL → DETECT → LOCALIZE → DECIDE → RESPOND → COMPLETE—with live drone tracks, confidence clouds, ROE advisories, recon imagery, and strike handoff when authorized.

**Response chain:** after acoustic localization, a **recon UAV flies to the threat fix** (inside the red confidence cloud), captures a **still image**, and pushes it to the operator recon feed—visual confirmation before strike. Authorized STRIKE vectors a killer asset to the same fix; sound finds, photo confirms, policy engages—no raw video on the mesh.

**Capabilities:** σ sliders (CEP50, cloud, ROE); mesh bandwidth dashboard; planning sandbox; resilient swarm with asset loss and bearing-only fixes.

## Why Triangle

Built in Python (Flask, NumPy/SciPy, Leaflet), Triangle closes the loop from **classified sound on the wing** to **coordinates on the map** to **authorized action**—GPS-denied sensing, swarm coordination, and operator decisions under bandwidth stress.
