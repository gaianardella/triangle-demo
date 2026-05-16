# Junction Defence Hackathon

Tactical map (WebSocket demo) + audio detection pipeline for military sound events.

## Layout

```
Junction_Defence_Hackathon/
├── ui/
│   ├── index.html          # Tactical map UI
│   └── mock_ws_server.py   # WebSocket demo server
├── detection/
│   ├── build_scenarios.py  # Generate UAV mix WAVs → data/scenarios/
│   └── detect_audio.py     # Classify audio → JSON
├── data/
│   ├── scenarios/          # Generated scenario WAVs
│   └── samples/            # Source clips (gunshot, tank, drone, …)
├── output/
│   └── events.json         # Detection output for integration
└── CREDITS.md
```

## Quick start

### Map UI

```bash
pip install websockets
python ui/mock_ws_server.py
# Open ui/index.html in the browser
```

### Audio pipeline

```bash
conda activate audio_env
pip install librosa numpy scipy

# 1) Build scenarios
python detection/build_scenarios.py --all

# 2) Detect + JSON
python detection/detect_audio.py --folder data/scenarios -o output/events.json
```

Paths resolve from the **repo root** (`data/`, `output/`).

## Detection classes

| `label` | Meaning |
|---------|---------|
| `gunshot` | Gunfire |
| `missile_launch` | Missile / UCAS launch |
| `drone` | UAV |
| `tank` | Tank engine |
| `null` / `relevant: false` | Not relevant (animals, background) |

See `output/events.json` for the full payload (`drone_id`, `timestamp_ns`, `bearing`, …).
