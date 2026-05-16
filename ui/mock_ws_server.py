#!/usr/bin/env python3
"""Mock WebSocket server for tactical map demo. pip install websockets

With detection/output/localizations.json present, streams real drone +
source positions (normalized 0–1) per scenario. Otherwise falls back to
the animated demo feed.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from pathlib import Path

try:
    import websockets
except ImportError:
    raise SystemExit("Install: pip install websockets")

HOST = "0.0.0.0"
PORT = 8765

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCALIZATIONS_PATH = REPO_ROOT / "detection" / "output" / "localizations.json"

LABEL_TO_TYPE = {
    "gunshot": "gunshot",
    "missile_launch": "missile_launch",
    "tank": "tank",
    "drone": "drone_hostile",
}


def _compute_bounds(entry: dict) -> dict:
    lats, lons = [], []
    for d in entry["drones_used"]:
        lats.append(d["lat"])
        lons.append(d["lon"])
    lats.append(entry["source"]["lat"])
    lons.append(entry["source"]["lon"])
    for p in entry.get("cloud_latlon") or []:
        lats.append(p["lat"])
        lons.append(p["lon"])
    pad = 0.00012
    return {
        "min_lat": min(lats) - pad,
        "max_lat": max(lats) + pad,
        "min_lon": min(lons) - pad,
        "max_lon": max(lons) - pad,
    }


def _to_norm(lat: float, lon: float, b: dict) -> tuple[float, float]:
    x = (lon - b["min_lon"]) / (b["max_lon"] - b["min_lon"])
    y = 1.0 - (lat - b["min_lat"]) / (b["max_lat"] - b["min_lat"])
    return x, y


def entry_to_payload(entry: dict) -> dict:
    b = _compute_bounds(entry)
    drones = []
    for d in entry["drones_used"]:
        x, y = _to_norm(d["lat"], d["lon"], b)
        drones.append({"id": d["drone_id"], "x": x, "y": y, "heading": 0})
    sx, sy = _to_norm(entry["source"]["lat"], entry["source"]["lon"], b)
    icon = LABEL_TO_TYPE.get(entry["label"], "unknown")
    cloud = [_to_norm(p["lat"], p["lon"], b) for p in entry.get("cloud_latlon") or []]
    return {
        "drones": drones,
        "targets": [{
            "id": entry["scenario"],
            "type": icon,
            "x": sx,
            "y": sy,
            "label": entry.get("label_human") or entry["label"],
        }],
        "cloud": [{"x": c[0], "y": c[1]} for c in cloud],
        "sonar": [{"x": sx, "y": sy, "color": "rgba(232, 92, 74, 0.45)"}],
    }


def _load_localizations() -> list[dict]:
    if not LOCALIZATIONS_PATH.is_file():
        return []
    with LOCALIZATIONS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


async def stream_demo(websocket) -> None:
    t = 0.0
    while True:
        t += 0.08
        cx = 0.48 + math.sin(t) * 0.12
        cy = 0.52 + math.cos(t * 0.85) * 0.1
        payload = {
            "drones": [{"id": "RAVEN-1", "x": cx, "y": cy, "heading": (t * 80) % 360}],
            "targets": [
                {"id": "H1", "type": "tank", "x": 0.62, "y": 0.38, "label": "ARMOR"},
                {"id": "H2", "type": "soldier", "x": 0.71, "y": 0.55, "label": "INF"},
                {"id": "H3", "type": "weapon", "x": 0.28, "y": 0.48, "label": "CACHE"},
            ],
            "sonar": [{"x": cx, "y": cy}],
        }
        if random.random() < 0.15:
            payload["log"] = {"message": "Perception fused · map updated", "level": "ok"}
        await websocket.send(json.dumps(payload))
        await asyncio.sleep(1 / 30)


async def stream_localizations(websocket, entries: list[dict]) -> None:
    idx = 0
    while True:
        entry = entries[idx % len(entries)]
        payload = entry_to_payload(entry)
        short = entry["scenario"].replace("scenario_", "").replace(".wav", "")
        payload["log"] = {
            "message": f"{entry.get('label_human', entry['label'])} · {short} · CEP50 {entry['cep50_m']}m",
            "level": "hostile",
        }
        await websocket.send(json.dumps(payload))
        idx += 1
        await asyncio.sleep(4.0)


async def stream(websocket) -> None:
    entries = _load_localizations()
    if entries:
        await stream_localizations(websocket, entries)
    else:
        await stream_demo(websocket)


async def main() -> None:
    entries = _load_localizations()
    async with websockets.serve(stream, HOST, PORT):
        if entries:
            print(f"Mock feed (localizations) ws://localhost:{PORT} — {len(entries)} scenarios")
        else:
            print(f"Mock tactical feed ws://localhost:{PORT} (demo)")
        print("Open ui/index.html via http.server (repo root) → LOCALIZE or CONNECT")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
