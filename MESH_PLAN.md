# Mesh Track — Implementation Plan & Reference

Architectural plan for the Kova "Tactical Mesh" track. Pairs with the
existing acoustic triangulation pipeline so the mesh demo and the
triangulation demo tell one coherent story. Built so that 95% of the
system runs without physical radios — hardware is reserved for the
moments that need it.

---

## How the mesh system works (implemented)

### Mesh data system (`mesh/` package)

The mesh package models a bandwidth-constrained tactical radio network.
Its job is to demonstrate that the full detection + localization story
can be transmitted using a fraction of the bytes that naive JSON would
require.

**Two packet types are defined in `mesh/payload.py`:**

| Packet | Magic | Wire size | When sent | Content |
|--------|-------|-----------|-----------|---------|
| `TACTICAL_EVENT` | `0xE001` | **32 B** payload + 16 B HMAC = **46 B** total | When a drone detects an acoustic event | label code, drone ID, timestamp (ns), lat/lon (E7 fixed-point), confidence |
| `LOC_SUMMARY` | `0xE002` | **24 B** payload + 16 B HMAC = **40 B** total | When the operator node receives a triangulation result | label code, scenario event ID (hash), lat/lon (E7), CEP50 (dm) |

Both formats use fixed-width binary structs (`struct.pack`) with
E7 fixed-point coordinates (lat × 10⁷ as int32). A naive JSON row
for the same data is ~500 B (tactical) or ~8 500 B (loc summary) —
roughly **91–94% compression**.

**Frame layer (`mesh/frame.py`):** each payload is wrapped in a frame
that adds source node ID, sequence number, TTL, and a 16-byte HMAC-SHA256
truncated tag. Frames are broadcast to all neighbours; each node
deduplicates by (src_id, seq) and decrements TTL before re-broadcasting
(flood routing).

**Transport (`mesh/transport/`):** two backends exist —
- `sim` — in-process shared bus (SimTransport), used in tests and the
  CLI demo. All nodes share a Python dict; no sockets needed.
- `udp` — localhost UDP on port 19987 for multi-terminal demos where
  each node runs in a separate terminal.

**`mesh/node.py` — MeshNode:** the core class. Handles send, receive,
dedup, flood-forward, and optional operator console printing.

**`mesh/metrics.py` — MeshMetrics:** simple counters (bytes sent/received,
frames sent/received, tactical/loc_summary counts). Shared singleton
via `get_metrics()`.

**`mesh/publish.py`:** helpers to bulk-publish events.json and
localizations.json over the mesh (used by `python -m mesh demo`).

---

### Mesh bandwidth API (`triangulation/server.py → /api/mesh/bandwidth`)

The server pre-computes bandwidth figures from the static data files
once on first request and caches them. The endpoint is stateless —
the UI maintains its own running session totals.

**What the server computes:**

```
events.json (all rows)
  → filter: relevant == true
  → for each row: event_row_to_tactical(row) → 32 B packet
  → measure: mesh_bytes = len(packet) + 16 (HMAC)
             json_bytes = len(json.dumps(row))
  → group by scenario filename

localizations.json (all entries)
  → skip bearing-only fixes (no CEP50 → no loc_summary emitted)
  → for each entry: pack_loc_summary(entry) → 24 B packet
  → measure: mesh_bytes = len(packet) + 16
             json_bytes = len(json.dumps(entry))
  → group by scenario filename
```

**Response shape:**

```json
{
  "total": {
    "mesh_bytes": 1472, "json_bytes": 19800,
    "saved_bytes": 18328, "saved_pct": 92.6,
    "tactical_packets": 24, "loc_packets": 8
  },
  "per_scenario": {
    "scenario_gunshot_mix.wav": {
      "tactical_count": 3, "loc_count": 1,
      "mesh_bytes": 178, "json_bytes": 2350
    },
    ...
  },
  "samples": {
    "tactical":    { "kind": "tactical", "mesh_bytes": 46, "json_bytes": 498,
                     "hex_mesh": "01 e0 01 01 03 02 00 …", "json_text": "{…}", "scenario": "…" },
    "loc_summary": { "kind": "loc_summary", "mesh_bytes": 40, "json_bytes": 8543,
                     "hex_mesh": "02 e0 01 01 00 00 …", "json_text": "{…}", "scenario": "…" }
  },
  "extrapolation": {
    "events_per_hour": 1000, "daily_mesh_kb": 3744.0, "daily_json_kb": 31104.0,
    "per_event_mesh_b": 46, "per_event_json_b": 498
  }
}
```

When `?scenario=<name>` is provided, the `samples` field is overridden
with that scenario's last tactical and loc_summary packets so the
popover hex dump always reflects the current scenario.

---

### Mesh bandwidth display (UI strip + popover)

The strip sits in the top bar and is always visible during the demo.

**Strip — two rows:**

```
MESH 46 B  /  JSON 498 B  /  SAVED 91%   ⓘ
TOTAL 178 B sent  ·  2.2 KB saved
```

- **Row 1** — last packet figures: mesh wire size vs JSON equivalent
  and compression ratio. Updates when the scenario changes (reflects
  that scenario's tactical or loc_summary packet).
- **Row 2** — running session totals: mesh bytes sent and JSON bytes
  saved so far this pitch. Accumulates as you advance through scenarios.
  Does NOT reset unless you click the reset button in the popover.

**Update cadence (by design):**

| Event | What updates |
|-------|-------------|
| Page load | Row 2 shows grand totals from all scenarios |
| Scenario advances | Row 1 → current scenario's packet; Row 2 += that scenario's bytes |
| Within a scenario (phase changes) | **Nothing changes** — correct, see below |
| Reset button clicked | Both rows reset to zero |

**Why nothing changes within a scenario:** the two packets for a scenario
(tactical at DETECT, loc_summary at LOCALIZE) are both accounted for
when the scenario loads. There is no new data to transmit mid-scenario —
the drone sent its event at DETECT, the operator received the loc_summary
at LOCALIZE. These are instantaneous radio events; the strip captures them
at scenario load time rather than animating them phase-by-phase.

> **Potential enhancement (Session 17 / future):** call `fetchBandwidth`
> at each phase transition and accumulate tactical bytes at DETECT and
> loc_summary bytes at LOCALIZE, so the running total visibly ticks up
> twice per scenario in sync with the animation. This is purely cosmetic.

**Popover (click strip to open):**

- Left panel: mesh hex dump of the last packet (8 bytes per line)
- Right panel: JSON equivalent (truncated at 400 chars)
- Bar chart: total mesh bytes vs total JSON bytes for the session
- Extrapolation footer: projected daily bandwidth at 1 000 events/hour
- Reset button: zeroes session totals (useful before a live pitch)

---

## Top-down: what is shown in the demo

Total time budget ≈ 2 minutes of pitch.

```
T+0:00   Three "drones" on the bench. Each runs a node process.
          A small topology panel on the operator UI shows neighbours
          and RSSI per link.
T+0:10   Gunshot audio plays. Each drone publishes a detection over
          the mesh — a 32-byte timestamp+ID frame.
T+0:20   Drones converge on a single localisation via existing TDOA.
          Operator UI shows target pin + CEP cloud (existing).
T+0:30   Decision engine emits RECON (existing ROE). Recon drone
          captures a placeholder image.
T+0:40   Image streams back via mesh. UI shows progressive arrival:
          coarse thumb at 80 ms, refined full at ~2 s. A telemetry
          line per chunk in the event log.
T+1:00   **BLOCK ANTENNA**: cover the recon drone's USB adapter with
          foil. UI shows "link recon→operator lost · rerouting via
          drone_3". The remaining chunks arrive via relay. Image
          completes.
T+1:15   Pull out a phone playing GPS jamming audio. Operator UI
          shows GPS-INDEPENDENT badge: mesh-NTP keeps clocks synced,
          TDOA continues working. (If time: trigger a mesh "channel
          hop" via a button to evade WiFi-channel jamming.)
T+1:30   Bonus scene: jammer triangulation. Three drones report
          RSSI of an unknown emitter, mesh forwards to operator, a
          second CEP cloud appears around the jammer's location.
T+1:50   Closing slide: dependency table — what data crosses the
          mesh, at what rate, why each criterion is hit.
```

Three moments must land:

1. **Progressive image arrival** (bandwidth efficiency) — first usable
   payload in ms, full image in seconds, while operator already
   has the coordinate to act on.
2. **Foil-the-antenna reroute** (resilience) — physical drama, undeniable.
3. **TDOA continues when GPS is jammed** (innovative application) —
   the mesh is the perception system, not just a delivery system.

## Architectural design

The whole mesh stack is structured around a **swappable transport
interface** so 95% of the code runs without physical radios. Hardware
fits in at the bottom; everything above can be developed, tested, and
even demoed using a software-loopback transport.

```
┌──────────────────────────────────────────────────────────────────┐
│                       APPLICATION LAYER                          │
│  · TDOA timestamp broadcast (32 B/msg)                           │
│  · Progressive image streamer (chunks + FEC)                     │
│  · Jammer triangulation reporter                                 │
│  · Operator console bridge → tactical UI                         │
└──────────────────────────────────────────────────────────────────┘
                                  │
┌──────────────────────────────────────────────────────────────────┐
│                          MESH LAYER                              │
│  · Routing: flood + (src, seq) dedup + TTL, optional AODV-lite   │
│  · Neighbour table + per-link RSSI tracking                      │
│  · Priority queue (HIGH = coords/status; BULK = imagery)         │
│  · Mesh-NTP service (4-timestamp protocol → swarm consensus)     │
└──────────────────────────────────────────────────────────────────┘
                                  │
┌──────────────────────────────────────────────────────────────────┐
│                        SECURITY LAYER                            │
│  · HMAC-SHA256 with PSK                                          │
│  · Sliding replay window per source                              │
│  · Optional: ChaCha20 payload encryption (deferred)              │
└──────────────────────────────────────────────────────────────────┘
                                  │
┌──────────────────────────────────────────────────────────────────┐
│            TRANSPORT INTERFACE  (swap-in / swap-out)             │
│  ┌──────────────────────────┬───────────────────────────────┐   │
│  │  SimTransport            │  RealTransport                │   │
│  │  in-process loopback     │  kova-wfb-rs raw 802.11       │   │
│  │  configurable per-link   │  three USB WiFi adapters      │   │
│  │  loss/delay/RSSI         │  measured RSSI, real losses   │   │
│  └──────────────────────────┴───────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

The transport interface (~10 methods: `send`, `register_receiver`,
`local_id`, `set_channel`, `rssi_to`, …) is the contract. Both
implementations satisfy it identically; everything above is portable.

This means: even if the hardware doesn't work on demo day, the entire
pitch runs cleanly via `SimTransport` with the topology programmed to
mimic a foil-blocked link.

## Group A — Tasks requiring physical hardware (3× USB WiFi adapters)

These need real radios because the value comes from physical phenomena
(distance, blocking, channel interference). Ranked by demo impact.

### H1. Live reroute under blockage  ★★★ TOP PRIORITY

The signature demo moment. Cover an adapter mid-stream; watch the mesh
notice, reroute, and finish the transfer through the relay.

**Subtasks:**

- H1.1 Bring up 3 USB adapters with kova-wfb-rs. Verify TX+RX works
  pairwise (drone_1↔2, 2↔3, 1↔3). Note any channel/regulatory issues
  with the specific dongle model.
- H1.2 Configure `RealTransport.send/recv` against kova-wfb-rs. Wire
  the same `Transport` interface that `SimTransport` exposes.
- H1.3 Calibrate the blocking technique. Tin foil wrap = ~30 dB loss;
  faraday-pouch / metal cup = >60 dB; metal mesh strainer is fast to
  remove for an "unblock" reveal. Pick what's repeatable and visible.
- H1.4 Wire UI: when a frame is received via a non-preferred neighbour,
  emit a log line `link X→Y lost · rerouting via Z`. Topology panel
  redraws the edge in amber.
- H1.5 Measure: time-to-detect-loss (target < 1 s), reroute success
  rate (target > 95% in foil-block conditions), max stall duration.
- H1.6 Rehearse the motion: block → wait → unblock. The drama is in
  the user's hand; the code has already done its job by then.

**Considerations:**

- 💡 If route discovery is reactive (AODV-style), tune the request
  timeout to ~300 ms so the reroute happens fast enough to feel
  live. If purely flood-based, it should "just work" because the
  relay path is always reachable in parallel.
- ⚠ Make sure the blocked drone is the *recon* drone, not the *operator*
  side, so the visual makes operational sense.

### H2. Range / RSSI characterisation  ★★

Provides the "this works at N metres at M dBm" line for the pitch. Not
a demo moment but a credibility-builder.

**Subtasks:**

- H2.1 Walk-out test: place TX at one end of the venue, RX at the
  other. Sample RSSI every 5 m to wall, every 2 m near edge of range.
- H2.2 Determine max usable link at chosen modulation. Note PER vs
  RSSI curve.
- H2.3 Document: one paragraph in the closing slide. "Demonstrated
  to 40 m indoor / 200 m outdoor at –92 dBm sensitivity with
  isotropic antennas. With Yagi at 8 dBi each end, reach scales
  to ~3 km."
- H2.4 Optional: if time, the same pair of drones at 50 m through
  a wall — show the relay-via-third-drone use case in space.

### H3. Channel-hopping under interference  ★★

If you can pull this off live, it's a strong moment. If not, fake-hop
on a button click — judges won't know the difference, and the protocol
behaviour is real either way.

**Subtasks:**

- H3.1 Implement channel switch command in the mesh protocol
  (frame type: `CHAN_HOP { new_channel, effective_at_ms }`). All
  nodes confirm before the switch.
- H3.2 PER-monitor: each link tracks rolling 1-second PER. If > 30%
  for 2 s, the link triggers a hop vote.
- H3.3 Bring an interferer (a second phone or laptop AP on channel 6,
  ping flooding works). Demonstrate the hop to channel 11.
- H3.4 Fallback: a "FORCE CHAN HOP" UI button that triggers the protocol
  manually. Useful if interference isn't reliable enough in the venue.

**Considerations:**

- ⚠ Channel switching takes ~50 ms on most adapters; expect a brief
  message gap. Buffer the priority queue across the hop.
- ⚠ Regulatory band matters. Stay inside 2.4 GHz channels 1–11 to
  avoid surprise issues with the Finnish regulator.

### H4. Throughput benchmarking  ★

Pitch-slide content only. No live demo.

**Subtasks:**

- H4.1 Measure raw TX rate at chosen modulation: payload bytes/sec
  with no FEC, with FEC, with HMAC overhead.
- H4.2 Compute: time for the full progressive image at measured rate
  vs naive raw-RGB transmission. Tabulate.
- H4.3 One closing slide line: "200 KB raw → 32 s. Our pipeline →
  1.8 s perceived, 4.5 s full quality."

## Group B — Tasks requiring only code & simulation

The bulk of the build. Every item here can be developed, tested, and
demoed without any radio hardware. Ranked by foundational-ness ×
demo-impact.

### C1. Transport interface + `SimTransport`  ★★★ ABSOLUTE PREREQUISITE

Nothing else gets built without this. The interface is what makes the
hardware swap clean; the simulator is what lets the demo run on stage
even if a USB adapter dies.

**Subtasks:**

- C1.1 Define `class Transport` (Protocol or ABC): `send(frame_bytes,
  to=None)`, `set_receiver(callable)`, `local_id() -> str`, `neighbours()
  -> dict[str, NeighbourState]`, `rssi_to(other) -> float`, `set_channel(int)`.
- C1.2 `SimTransport`: in-process, all nodes share a Python registry.
  Each `send` calls registered receivers on the other nodes after a
  configurable delay; loss probability per link.
- C1.3 Topology file (`mesh/topology.yaml` or similar): which node
  reaches which, with synthetic RSSI and base loss. Allows "stage
  one link as foil-blocked" before running.
- C1.4 Loss-injection wrapper: `LossyTransport(inner, drop=0.1,
  reorder_window=2)` for stress-testing.
- C1.5 RSSI synthesis: in sim mode, return a function of "distance"
  (configurable in topology file) plus jitter.
- C1.6 Unit tests: frame delivery, drop, delay, ordering, RSSI.

**Architecture notes:**

- 💡 `SimTransport` must run multi-process AND in-process. Some tests
  want in-process for speed; the live demo wants multi-process so
  each node has independent state. Use the same code path with a
  shared-memory or domain-socket backend.
- 💡 Don't bake the topology into the transport. Inject it.

### C2. Frame format + HMAC + replay window  ★★★

The protocol's spine. Everything else assumes authenticated, dedup'd
frames.

**Subtasks:**

- C2.1 Define wire format (suggested):
  ```
  magic    u32   0x4D455348 ("MESH")
  version  u8    1
  type     u8    DATA | NTP_REQ | NTP_REP | CHAN_HOP | RSSI_REPORT | HELLO
  src      u32   node id
  dst      u32   0 = broadcast
  seq      u32   per-source monotonic
  ttl      u8
  flags    u8
  payload  bytes (≤ MAX_PAYLOAD, e.g. 1024)
  hmac     u8[16] HMAC-SHA256-truncated over the preceding fields
  ```
- C2.2 Pack/unpack: `struct` or `msgpack` for `payload`; raw `struct`
  for headers.
- C2.3 HMAC: 32-byte PSK loaded from env var or `.mesh-key` file.
  Verify before any further processing.
- C2.4 Replay window: per-source ring buffer of last N=64 sequences;
  drop seq ≤ window-max-N or already-seen.
- C2.5 Negative tests: forged HMAC rejected, replayed frame rejected,
  truncated frame ignored.
- C2.6 Metrics: count rejected frames per reason (auth, replay,
  malformed). Exposed in the operator UI.

**Architecture notes:**

- 💡 Truncated HMAC (16 of 32 bytes) is enough for hackathon; full 32
  bytes if pre-shared bandwidth is no issue.
- 💡 Encryption is deferred. Authentication is the must-have; secrecy
  is a nice-to-have only if recon imagery is somehow sensitive in the
  pitch, which it isn't.

### C3. Routing: flood + dedup + TTL  ★★★

Simplest mesh that works. Robust to single-link failures by virtue of
sending the same message via every neighbour at once.

**Subtasks:**

- C3.1 Receive path: on inbound frame, after HMAC + replay checks,
  check `(src, seq)` against dedup cache. If new and TTL > 0,
  decrement TTL and rebroadcast to all neighbours except the source.
- C3.2 Dedup cache: time-bounded set (entries expire after ~5 s) per
  source.
- C3.3 Reverse-path learning (cheap): record which neighbour delivered
  `(src, seq)` first; that neighbour is the "preferred next hop"
  toward src.
- C3.4 Path-loss handling: when a neighbour goes silent for > timeout
  (e.g. 2 s of expected HELLO frames missing), prune them and emit
  a `link X lost` log line.
- C3.5 Test scenarios: 3-node line, 3-node triangle, 4-node grid;
  block one link; confirm delivery still works.

**Architecture notes:**

- 💡 Flood is wasteful but bulletproof for 3 nodes. AODV-lite is the
  upgrade target if there's time. Don't bother for the demo.
- ⚠ Dedup cache size: too small → false re-floods; too big → memory
  growth. 5 s TTL with seq ring of 64/source is plenty for the demo.

### C4. Progressive image transport  ★★★

The bandwidth story headline. JPEG q40 thumb first, refined full
second; chunked with FEC so a couple of lost packets don't break the
stream.

**Subtasks:**

- C4.1 Two-stage encoder: `make_progressive(image) -> (thumb_bytes,
  full_bytes)`. Thumb: 80×60 grayscale q20 (~1.5 KB). Full: 160×120
  colour q40 (~10 KB).
- C4.2 Chunker: split each layer into 512-byte fragments with
  `(stream_id, layer, idx, total)` header.
- C4.3 FEC: Reed-Solomon over groups of K=8 data chunks → produce
  M=4 parity chunks. Library: `reedsolo` (one pip install away).
- C4.4 Sender: queue thumb chunks at HIGH priority, full chunks at
  BULK priority.
- C4.5 Receiver: accumulate, apply FEC if any chunk missing, decode
  thumb the instant the thumb layer completes, decode full on full
  arrival.
- C4.6 Progress events: emit `{stream_id, percent_thumb, percent_full,
  time_since_start_ms}` for the UI.
- C4.7 Test: simulate 20% packet loss → recover with FEC. Time-to-thumb
  should be well under 200 ms.

**Architecture notes:**

- 💡 Don't get clever with custom codecs. JPEG + RS is good enough.
- 💡 The "thumb first" UX is the win. Even at 80×60, a target's
  silhouette is recognisable.
- ⚠ Reed-Solomon adds overhead; tune K/M for the link characteristics
  measured in H4. Default K=8, M=4 (50% redundancy) is a safe start.

### C5. Priority queue  ★★

Without this, an imagery transmission starves coord updates. With it,
the operator gets ground truth fast even while the picture is loading.

**Subtasks:**

- C5.1 Two queues: HIGH (coords, status, NTP, CHAN_HOP), BULK
  (imagery, telemetry).
- C5.2 Scheduler: drain HIGH first; serve BULK only when HIGH is
  empty. Optional: bandwidth fraction reservation (always preempt
  for HIGH).
- C5.3 Caller API: `transport.send(frame, priority="HIGH"|"BULK")`.
- C5.4 Test: while a large image stream is in flight, coord updates
  must arrive within one frame slot.

### C6. Mesh-NTP clock sync  ★★ (innovation)

The "GPS-jammed but TDOA still works" story. Drones sync their clocks
peer-to-peer over the mesh.

**Subtasks:**

- C6.1 Protocol: A sends `NTP_REQ {t1}`, B replies `NTP_REP {t1, t2, t3}`,
  A records `t4` on receive. Offset estimate is
  `((t2 - t1) - (t4 - t3)) / 2`, round-trip `(t4 - t1) - (t3 - t2)`.
- C6.2 Run every 500 ms between each pair of neighbours.
- C6.3 Per-pair EWMA offset; swarm consensus = median.
- C6.4 Surface as a UI panel: per-drone clock offset and σ over time.
  Pulse the indicator amber when σ > 1 ms.
- C6.5 Integrate with the existing acoustic pipeline: provide a
  "corrected timestamp" service that the detection module uses
  *instead of* `time.time_ns()`.
- C6.6 Test: introduce synthetic clock drift on one node (e.g. 1 ms
  per second of wall clock). Verify mesh-NTP keeps the corrected
  timestamps within ~100 µs.

**Architecture notes:**

- 💡 This is the single most important code task for the *innovation*
  criterion. It directly answers "why does this team need a mesh?".

### C7. Operator UI integration  ★★

Wire the mesh into the existing `ui/index.html` tactical map.

**Subtasks:**

- C7.1 Backend: each node writes a JSON status line every 500 ms to a
  small WebSocket the existing UI already supports. Fields: own ID,
  neighbours w/ RSSI, recent rejected/accepted frame counts,
  current clock offset, current channel.
- C7.2 Frontend: add a "MESH" sidebar panel next to the existing
  legend. Topology mini-graph (3 nodes, edges coloured by RSSI),
  per-link RSSI labels, message counters.
- C7.3 Frontend: extend the existing event log with mesh-coloured
  lines (`link X lost`, `rerouted via Z`, `chan hop 6 → 11`,
  `NTP σ exceeded 1 ms`).
- C7.4 Frontend: BLOCK button per node for sim mode (sends a "fake
  block" command to `SimTransport`); allows running the foil
  scenario without hardware.

### C8. Jammer triangulation  ★ (bonus innovation)

Same triangulation pipeline you already have, fed by RSSI instead of
acoustic timestamps. The mesh forwards the RSSI reports; the result is
a CEP cloud around the jammer.

**Subtasks:**

- C8.1 Frame type `RSSI_REPORT { node_id, channel, rssi_dbm,
  centre_freq_hz, time_ns }`.
- C8.2 Per-node broadband scanner: in sim, synthesised from a jammer
  position; in real mode, polled from the adapter's RSSI register.
- C8.3 Path-loss model: `rssi(d) = rssi_at_1m - 10*α*log10(d)`,
  α tunable (default 2.5 indoor, 3.5 outdoor).
- C8.4 Localise: feed three (or more) RSSI readings into a copy of
  `triangulation.core.localize()` with the residual rewritten in
  log-distance space. (One-evening adaptation.)
- C8.5 UI: a yellow "JAMMER" cloud appears on the map at the
  estimated jammer location.

**Architecture notes:**

- ⚠ Don't over-claim accuracy. RSSI-based localisation has tens of
  metres of error at best. Pitch it as "direction-finding plus
  range estimate", not "precise targeting".

### C9. Mesh topology visualization  ★

UI polish. The "look at the live mesh" panel that judges' eyes follow.

**Subtasks:**

- C9.1 SVG mini-graph in the existing UI's sidebar. Three drone
  vertices, edges weighted by RSSI.
- C9.2 Animated frame indicators: a small dot travelling along an edge
  whenever a frame is sent on that link.
- C9.3 Edge colour: green > –70 dBm, amber –70 to –85, red worse.
- C9.4 On-blocked: the edge turns dashed amber; appears removed when
  PER > 80% for 2 s.

### C10. Reed-Solomon FEC test fixture  ★

(Already partially covered in C4; this is the standalone test/bench.)

- C10.1 Standalone script `mesh/test_fec.py`: encode bytes, lose
  random chunks, decode. Confirm recovery for up to M=4 lost
  chunks per group.
- C10.2 Pitch-slide table: chunk loss rate vs end-to-end success
  rate at various K/M.

## Recommended build order

Assuming two people on the mesh track for ~30 hours total:

Day 1 morning: C1 (transport interface + SimTransport) + C2 (frame format
+ HMAC + replay). Without these, nothing works. ~6 h.

Day 1 afternoon: C3 (routing) + C5 (priority queue) + C4 (progressive
image, base layer). Now the simulated end-to-end runs. ~6 h.

Day 1 evening: C7 (UI integration). The simulated mesh now drives the
existing tactical map. Demo-able without hardware. ~3 h.

Day 2 morning: H1 (real hardware bring-up + foil reroute). One person.
~5 h. Meanwhile: C6 (mesh-NTP). The other person. ~4 h.

Day 2 afternoon: C4 polish (progressive image, FEC) + H2 (range
characterisation) + H4 (throughput numbers). ~5 h.

Day 2 evening: C8 (jammer triangulation) if bandwidth. Otherwise
rehearse demo. ~3 h.

## Integration with the existing repo

The mesh code lives under `mesh/` at the repo root, mirroring the
`triangulation/` layout:

```
Junction_Defence_Hackathon/
├── triangulation/                  (existing)
├── mesh/                           (new)
│   ├── __init__.py
│   ├── transport/
│   │   ├── __init__.py
│   │   ├── base.py                 # Transport ABC / Protocol
│   │   ├── sim.py                  # SimTransport
│   │   └── real.py                 # RealTransport (kova-wfb-rs binding)
│   ├── frame.py                    # wire format, HMAC, replay
│   ├── routing.py                  # flood + dedup + TTL
│   ├── priority.py                 # HIGH / BULK queues
│   ├── ntp.py                      # mesh-NTP service
│   ├── imagery.py                  # progressive encode + chunk + FEC
│   ├── rssi_reporter.py            # jammer-triangulation feed
│   ├── topology.yaml               # default sim topology
│   ├── node.py                     # main runtime: wire everything together
│   ├── operator.py                 # WebSocket bridge to ui/index.html
│   └── README.md
├── ui/index.html                   (existing, gets MESH panel added)
└── MESH_PLAN.md                    (this file)
```

The acoustic pipeline reads no mesh code directly; the mesh code calls
into the acoustic pipeline via the existing `triangulation.core.*`
functions when computing the jammer fix. Loose coupling on purpose.

## Out of scope

- **Full encryption (ChaCha20).** HMAC authentication is enough for
  the demo. List as future work.
- **AODV / OLSR proper routing.** Flooding is fine for 3 nodes. Mention
  the upgrade path in the pitch.
- **Real-time video.** Single-shot imagery only.
- **Mesh-aware autonomy** (drones replanning their paths based on link
  quality). Out of scope; would need a flight controller.
- **DSSS / OFDM physical-layer modifications.** Stay on top of
  standard 802.11; the work is at the framing and protocol layer.
- **Multi-channel simultaneous operation.** Single channel at a time;
  hopping is sequential.

## Pitch slide — closing dependency table

To paste into the pitch, fills in nicely after the demo:

| Data crossing the mesh | Rate | Latency requirement | Why mesh matters |
|---|---|---|---|
| Detection timestamps | 100 B / event | < 100 ms | Without sync, no TDOA — single-bearing localisation only |
| Target coords + CEP | 200 B / event | < 200 ms | Operator's actionable payload |
| Recon imagery | 10 KB / event | < 5 s | Confirmation before kinetic action |
| Mesh-NTP frames | 32 B × pairs × 2 Hz | < 50 ms | Replaces GPS time when GPS is denied |
| Jammer RSSI reports | 48 B × nodes × 5 Hz | best-effort | Turn the attack into a target |

If the mesh fails: coords still cross via single hop (acoustic still
works as long as one drone reaches the operator); imagery drops to
single-link; mesh-NTP falls back to GPS time if available.

If the mesh succeeds under jamming: every entry in the table keeps
flowing. That's the whole pitch.
