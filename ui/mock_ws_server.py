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


def _compute_global_bounds(entries: list[dict]) -> dict:
    lats, lons = [], []
    for entry in entries:
        for d in entry["drones_used"]:
            lats.append(d["lat"])
            lons.append(d["lon"])
        lats.append(entry["source"]["lat"])
        lons.append(entry["source"]["lon"])
        for p in entry.get("cloud_latlon") or []:
            lats.append(p["lat"])
            lons.append(p["lon"])
    pad = 0.00018
    return {
        "min_lat": min(lats) - pad,
        "max_lat": max(lats) + pad,
        "min_lon": min(lons) - pad,
        "max_lon": max(lons) - pad,
    }


def entry_to_payload(entry: dict, bounds: dict | None = None) -> dict:
    b = bounds if bounds is not None else _compute_bounds(entry)
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


# Phase durations (seconds) — match ui/index.html PHASE_MS
_WS_PHASE_S = {"transit": 4.2, "listen": 2.2, "localize": 2.8, "hold": 5.2}
_WS_PHASE_ORDER = ["transit", "listen", "localize", "hold"]


async def stream_localizations(websocket, entries: list[dict]) -> None:
    n = len(entries)
    bounds = _compute_global_bounds(entries)
    idx = 0
    prev_idx = n - 1
    phase_i = 0
    phase_t = 0.0
    tick = 1 / 30

    while True:
        entry = entries[idx]
        prev_entry = entries[prev_idx]
        phase = _WS_PHASE_ORDER[phase_i]
        dur = _WS_PHASE_S[phase]
        t = min(1.0, phase_t / dur)
        t = t * t * (3 - 2 * t)

        cur = entry_to_payload(entry, bounds)
        prev = entry_to_payload(prev_entry, bounds)

        if phase == "transit":
            drones = []
            for i, d in enumerate(cur["drones"]):
                f = prev["drones"][i] if i < len(prev["drones"]) else d
                drones.append({
                    "id": d["id"],
                    "x": f["x"] + (d["x"] - f["x"]) * t,
                    "y": f["y"] + (d["y"] - f["y"]) * t,
                    "heading": 0,
                })
            payload = {"drones": drones, "targets": [], "cloud": []}
        elif phase == "listen":
            payload = {"drones": cur["drones"], "targets": [], "cloud": []}
        elif phase == "localize":
            payload = {
                "drones": cur["drones"],
                "targets": cur["targets"] if t > 0.4 else [],
                "cloud": cur["cloud"] if t > 0.2 else [],
            }
        else:
            payload = cur

        short = entry["scenario"].replace("scenario_", "").replace(".wav", "")
        if phase == "listen" and phase_t < tick * 1.5:
            payload["log"] = {"message": f"Segnale · {short}", "level": "warn"}
        elif phase == "localize" and 0.4 * dur < phase_t < 0.4 * dur + tick:
            payload["log"] = {
                "message": f"{entry.get('label_human', entry['label'])} · CEP50 {entry['cep50_m']}m",
                "level": "hostile",
            }

        await websocket.send(json.dumps(payload))
        phase_t += tick
        if phase_t >= dur:
            phase_t = 0.0
            phase_i += 1
            if phase_i >= len(_WS_PHASE_ORDER):
                phase_i = 0
                prev_idx = idx
                idx = (idx + 1) % n
        await asyncio.sleep(tick)


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
