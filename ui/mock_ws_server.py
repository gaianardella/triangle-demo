#!/usr/bin/env python3
"""Mock WebSocket server for tactical map demo. pip install websockets"""

import asyncio
import json
import math
import random

try:
    import websockets
except ImportError:
    raise SystemExit("Install: pip install websockets")

HOST = "0.0.0.0"
PORT = 8765


async def stream(websocket):
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


async def main():
    async with websockets.serve(stream, HOST, PORT):
        print(f"Mock tactical feed ws://{HOST}:{PORT}")
        print("Open ui/index.html → CONNECT (or ?ws=ws://localhost:8765)")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
