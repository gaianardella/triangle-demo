"""Tactical Edge — Flask backend (Sessions 8, 10, 16).

Serves the UI and provides live TDOA recompute endpoints so the browser
sliders can update CEP50, the confidence cloud, and the ROE chip in
real time without re-running the offline pipeline.

Usage
-----
    python -m triangulation.server [--port 5050] [--host 0.0.0.0]

    Opens http://localhost:5050/ — serves ui/index.html plus all static
    assets from the ui/ directory.

Endpoints
---------
    GET /                               → ui/index.html
    GET /<file>                         → static asset from ui/
    GET /api/scenarios                  → all scenarios (default sigmas)
    GET /api/scenarios/<id>             → single scenario (default sigmas)
    GET /api/scenarios/<id>             → live recompute
        ?sigma_t_ms=X&sigma_pos_m=Y
    GET /api/scenarios/<id>/sweep       → 15-point (sigma_t, CEP50) sweep
        ?sigma_pos_m=Y                    for the money-curve mini-chart
    GET /api/events?scenario=<id>       → raw events for a scenario (debug)
    POST /api/sandbox                   → free-play sandbox localization
        {drones, source, sigma_t_ms,      from dragged geometry; returns the
         sigma_pos_m, label}               same localize_scenario output plus
                                           sandbox_truth: {lat, lon}

Notes
-----
- MC sample count for live recompute: 120 (target ≈15–30 ms latency).
- Results are LRU-cached by (scenario_id, σ_t, σ_pos); max 50 entries.
- The same ``localize_scenario`` function used by the offline pipeline
  runs here, so live results are guaranteed to be consistent.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

import numpy as np

try:
    from flask import Flask, abort, jsonify, request, send_from_directory
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Flask not installed. Run: pip install flask"
    ) from exc

from .locate import (
    _group_by_scenario,
    _localizable,
    localize_scenario,
)
from .sandbox import build_events as _build_sandbox_events

try:
    from mesh.payload import (
        event_row_to_tactical,
        json_row_wire_size,
        pack_loc_summary,
        TACTICAL_EVENT_SIZE,
        LOC_SUMMARY_SIZE,
    )
    _MESH_AVAILABLE = True
except ImportError:
    _MESH_AVAILABLE = False

# ── Paths ─────────────────────────────────────────────────────────────────────

_ROOT            = Path(__file__).resolve().parent.parent
_UI_DIR          = _ROOT / "ui"
_EVENTS_PATH     = _ROOT / "detection" / "output" / "events.json"
_LOCS_PATH       = _ROOT / "detection" / "output" / "localizations.json"
_DETECTION_DIR   = _ROOT / "detection" / "output"

# HMAC-SHA256 truncated to 16 B is appended to every frame on the wire.
_HMAC_SIZE = 16

# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)

# ── Event cache (loaded once at startup) ──────────────────────────────────────

_events:  list[dict] | None = None
_groups:  dict[str, list[dict]] | None = None


def _load_events() -> tuple[list[dict], dict[str, list[dict]]]:
    global _events, _groups
    if _events is None:
        with open(_EVENTS_PATH) as fh:
            _events = json.load(fh)
        _groups = _group_by_scenario(_events)
    return _events, _groups   # type: ignore[return-value]


# ── LRU recompute cache ───────────────────────────────────────────────────────

_MAX_CACHE = 50
_cache: OrderedDict[tuple, dict] = OrderedDict()


def _cache_key(scenario_id: str,
               sigma_t_ms: float | None,
               sigma_pos_m: float | None,
               killed_drone_ids: set[str] | None = None) -> tuple:
    return (
        scenario_id,
        round(sigma_t_ms,  4) if sigma_t_ms  is not None else None,
        round(sigma_pos_m, 3) if sigma_pos_m is not None else None,
        tuple(sorted(killed_drone_ids)) if killed_drone_ids else (),
    )


def _cache_get(key: tuple) -> dict | None:
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    return None


def _cache_put(key: tuple, val: dict) -> None:
    _cache[key] = val
    _cache.move_to_end(key)
    while len(_cache) > _MAX_CACHE:
        _cache.popitem(last=False)


# ── Group lookup helper ───────────────────────────────────────────────────────

def _find_group(scenario_id: str) -> list[dict] | None:
    _, groups = _load_events()
    # Try exact key first
    if scenario_id in groups:
        return groups[scenario_id]
    # Try matching by basename
    for key, group in groups.items():
        if Path(key).name == scenario_id:
            return group
    return None


# ── Recompute helper ──────────────────────────────────────────────────────────

def _recompute(group: list[dict],
               scenario_id: str,
               sigma_t_ms: float | None,
               sigma_pos_m: float | None,
               killed_drone_ids: set[str] | None = None) -> dict:
    key = _cache_key(scenario_id, sigma_t_ms, sigma_pos_m, killed_drone_ids)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    rng   = np.random.default_rng(7)
    entry = localize_scenario(
        group,
        mc_samples=120,
        sigma_t_override_ms=sigma_t_ms,
        sigma_pos_override_m=sigma_pos_m,
        killed_drone_ids=killed_drone_ids,
        rng=rng,
    )
    _cache_put(key, entry)
    return entry


# ── Static file serving ───────────────────────────────────────────────────────

@app.route("/")
def index():                                         # noqa: D103
    return send_from_directory(str(_UI_DIR), "index.html")


@app.route("/<path:filename>")
def static_ui(filename: str):                        # noqa: D103
    # Prevent path traversal: only serve files inside ui/
    safe = (_UI_DIR / filename).resolve()
    if not str(safe).startswith(str(_UI_DIR.resolve())):
        abort(403)
    return send_from_directory(str(_UI_DIR), filename)


@app.route("/detection/output/<path:filename>")
def detection_output(filename: str):
    """Serve static files from detection/output/ (e.g. localizations.json).

    The UI's loadLocalizations() requests
    ``/detection/output/localizations.json`` — this route fulfils it.
    """
    safe = (_DETECTION_DIR / filename).resolve()
    if not str(safe).startswith(str(_DETECTION_DIR.resolve())):
        abort(403)
    return send_from_directory(str(_DETECTION_DIR), filename)


# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/api/scenarios")
def api_all_scenarios():
    """Return all localizable scenarios with their default-sigma results."""
    _, groups = _load_events()
    results: list[dict] = []
    rng = np.random.default_rng(7)
    for sid, group in groups.items():
        ok, _ = _localizable(group)
        if not ok:
            continue
        try:
            entry = localize_scenario(group, mc_samples=120, rng=rng)
            results.append(entry)
        except Exception:
            pass
    return jsonify(results)


@app.route("/api/scenarios/<scenario_id>")
def api_scenario(scenario_id: str):
    """Return a single scenario, optionally recomputed with overridden sigmas.

    Query params:
        sigma_t_ms  — override timing error for all drones (float, ms)
        sigma_pos_m — override position error for all drones (float, m)
    """
    group = _find_group(scenario_id)
    if group is None:
        abort(404)

    sigma_t_ms  = request.args.get("sigma_t_ms",  type=float)
    sigma_pos_m = request.args.get("sigma_pos_m", type=float)
    killed_raw  = request.args.get("killed", "").strip()
    killed_drone_ids: set[str] | None = (
        {d for d in killed_raw.split(",") if d} if killed_raw else None
    )

    try:
        return jsonify(_recompute(group, scenario_id, sigma_t_ms, sigma_pos_m, killed_drone_ids))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scenarios/<scenario_id>/sweep")
def api_sweep(scenario_id: str):
    """16-point σ_t sweep for the money-curve mini-chart.

    Returns a list of {sigma_t_ms, cep50_m, action} for the range
    0.001 ms → 20 ms (log-spaced).  σ_pos is held fixed (default or
    the value passed via ?sigma_pos_m=Y).
    """
    group = _find_group(scenario_id)
    if group is None:
        abort(404)

    sigma_pos_m = request.args.get("sigma_pos_m", type=float)
    sigmas_ms   = np.logspace(np.log10(0.001), np.log10(20.0), 16)

    results = []
    rng = np.random.default_rng(7)
    for st in sigmas_ms:
        try:
            entry = localize_scenario(
                group,
                mc_samples=60,
                sigma_t_override_ms=float(st),
                sigma_pos_override_m=sigma_pos_m,
                rng=rng,
            )
            results.append({
                "sigma_t_ms": round(float(st), 5),
                "cep50_m":    entry["cep50_m"],
                "action":     entry["recommended_action"],
            })
        except Exception:
            pass
    return jsonify(results)


@app.route("/api/events")
def api_events():
    """Raw events for a scenario (debug / inspection)."""
    events, _ = _load_events()
    scenario = request.args.get("scenario")
    if scenario:
        filtered = [
            e for e in events
            if Path(e.get("path", "")).name == scenario
            or e.get("path", "") == scenario
        ]
        return jsonify(filtered)
    return jsonify(events)


@app.route("/api/sandbox", methods=["POST"])
def api_sandbox():
    """Free-play sandbox localization from dragged geometry.

    Request body (JSON):
        drones      — list of {drone_id, lat, lon}
        source      — {lat, lon}  (true source position)
        sigma_t_ms  — timing error σ in ms  (default 1.0)
        sigma_pos_m — position error σ in m (default 5.0)
        label       — acoustic class label  (default "gunshot")

    Returns the same payload as /api/scenarios/<id> plus
        sandbox_truth: {lat, lon}  — the user-placed source position.
    """
    body        = request.get_json(force=True, silent=True) or {}
    drones      = body.get("drones", [])
    source      = body.get("source", {})
    sigma_t_ms  = float(body.get("sigma_t_ms",  1.0))
    sigma_pos_m = float(body.get("sigma_pos_m", 5.0))
    label       = str(body.get("label",         "gunshot"))
    killed_list = body.get("killed_drone_ids", [])
    killed_drone_ids: set[str] | None = set(killed_list) if killed_list else None

    if len(drones) < 3:
        return jsonify({"error": "Need at least 3 drones for TDOA"}), 400
    if not source.get("lat") or not source.get("lon"):
        return jsonify({"error": "source lat/lon required"}), 400

    try:
        rng   = np.random.default_rng(42)
        group = _build_sandbox_events(
            drones, source, sigma_t_ms, sigma_pos_m, label, rng
        )
        rng2  = np.random.default_rng(42)          # fresh seeded rng for MC
        entry = localize_scenario(
            group,
            mc_samples=120,
            sigma_t_override_ms=sigma_t_ms,
            sigma_pos_override_m=sigma_pos_m,
            killed_drone_ids=killed_drone_ids,
            rng=rng2,
        )
        entry["sandbox_truth"] = {
            "lat": float(source["lat"]),
            "lon": float(source["lon"]),
        }
        return jsonify(entry)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Session 16: Mesh bandwidth data ──────────────────────────────────────────

_bw_data: dict | None = None   # computed once at first request


def _compute_bandwidth_data() -> dict:
    """Pre-compute per-scenario and aggregate mesh-vs-JSON bandwidth numbers.

    Returns a dict with:
      ``total``        — grand totals across all events + all localizations.
      ``per_scenario`` — keyed by scenario basename, per-scenario deltas.
      ``samples``      — one representative packet per kind (tactical / loc_summary).
      ``extrapolation``— projected daily savings at assumed 1 000 events/hour.
    """
    if not _MESH_AVAILABLE:
        return {"error": "mesh package not available"}

    events, _ = _load_events()

    locs: list[dict] = []
    if _LOCS_PATH.exists():
        with open(_LOCS_PATH) as fh:
            locs = json.load(fh)

    # ── Per-row tactical accounting ──
    tactical_rows: list[dict] = []
    for row in events:
        if not row.get("relevant"):
            continue
        pkt = event_row_to_tactical(row)
        if pkt is None:
            continue
        scenario = Path(row.get("path", "")).name
        json_sz  = json_row_wire_size(row)
        mesh_sz  = len(pkt) + _HMAC_SIZE   # payload + HMAC overhead
        tactical_rows.append({
            "scenario": scenario,
            "drone_id": row.get("drone_id", ""),
            "kind":     "tactical",
            "mesh_bytes": mesh_sz,
            "json_bytes": json_sz,
            "hex_mesh": pkt.hex(" "),
            "json_text": json.dumps(row, separators=(",", ":")),
        })

    # ── Per-entry loc-summary accounting ──
    loc_rows: list[dict] = []
    for loc in locs:
        if loc.get("cep50_m") is None:
            continue  # bearing fix — no loc summary emitted
        try:
            ls = pack_loc_summary(loc)
        except Exception:
            continue
        loc_json_sz = len(json.dumps(loc, separators=(",", ":")).encode())
        mesh_sz     = len(ls) + _HMAC_SIZE
        loc_rows.append({
            "scenario":   loc.get("scenario", ""),
            "kind":       "loc_summary",
            "mesh_bytes": mesh_sz,
            "json_bytes": loc_json_sz,
            "hex_mesh":   ls.hex(" "),
            "json_text":  json.dumps(loc, separators=(",", ":")),
        })

    # ── Per-scenario aggregation ──
    per_scenario: dict[str, dict] = {}
    for r in tactical_rows + loc_rows:
        sc = r["scenario"]
        if sc not in per_scenario:
            per_scenario[sc] = {
                "tactical_count":      0,
                "loc_count":           0,
                "mesh_bytes":          0,
                "json_bytes":          0,
                # Split by packet type so the UI can reveal them phase-by-phase
                "tactical_mesh_bytes": 0,
                "tactical_json_bytes": 0,
                "loc_mesh_bytes":      0,
                "loc_json_bytes":      0,
                "last_tactical":       None,
                "last_loc_summary":    None,
            }
        ps = per_scenario[sc]
        ps["mesh_bytes"] += r["mesh_bytes"]
        ps["json_bytes"] += r["json_bytes"]
        if r["kind"] == "tactical":
            ps["tactical_count"]      += 1
            ps["tactical_mesh_bytes"] += r["mesh_bytes"]
            ps["tactical_json_bytes"] += r["json_bytes"]
            ps["last_tactical"] = r
        else:
            ps["loc_count"]      += 1
            ps["loc_mesh_bytes"] += r["mesh_bytes"]
            ps["loc_json_bytes"] += r["json_bytes"]
            ps["last_loc_summary"] = r

    # ── Grand totals ──
    total_mesh = sum(r["mesh_bytes"] for r in tactical_rows + loc_rows)
    total_json = sum(r["json_bytes"] for r in tactical_rows + loc_rows)
    saved      = total_json - total_mesh
    saved_pct  = round(100.0 * saved / total_json, 1) if total_json > 0 else 0.0

    # ── Representative sample packets (for hex-dump popover) ──
    samples = {
        "tactical":    tactical_rows[0] if tactical_rows else None,
        "loc_summary": loc_rows[0]      if loc_rows     else None,
    }

    # ── Extrapolation (1 000 events / hour, 24 h) ──
    # "event" = 1 relevant detection row from one drone
    events_per_hour = 1000
    hours = 24
    per_event_mesh  = (TACTICAL_EVENT_SIZE + _HMAC_SIZE)   # 46 B
    per_event_json  = (
        sum(r["json_bytes"] for r in tactical_rows) / len(tactical_rows)
        if tactical_rows else 501
    )
    per_loc_mesh    = (LOC_SUMMARY_SIZE + _HMAC_SIZE)      # 40 B
    per_loc_json    = (
        sum(r["json_bytes"] for r in loc_rows) / len(loc_rows)
        if loc_rows else 8503
    )
    # Assume 1 loc per 3 detection events (3 drones → 1 scenario)
    daily_mesh_kb  = round((events_per_hour * per_event_mesh +
                            events_per_hour / 3 * per_loc_mesh) * hours / 1024, 1)
    daily_json_kb  = round((events_per_hour * per_event_json +
                            events_per_hour / 3 * per_loc_json) * hours / 1024, 1)

    return {
        "total": {
            "mesh_bytes":  total_mesh,
            "json_bytes":  total_json,
            "saved_bytes": saved,
            "saved_pct":   saved_pct,
            "tactical_packets": len(tactical_rows),
            "loc_packets":      len(loc_rows),
        },
        # Public view — totals + split by packet type; no last_* blobs
        "per_scenario": {
            sc: {k: v for k, v in ps.items()
                 if k not in ("last_tactical", "last_loc_summary")}
            for sc, ps in per_scenario.items()
        },
        # Full view — includes last_* for per-scenario sample lookup
        "_per_scenario_full": per_scenario,
        "samples": samples,
        "extrapolation": {
            "events_per_hour":   events_per_hour,
            "daily_mesh_kb":     daily_mesh_kb,
            "daily_json_kb":     daily_json_kb,
            "per_event_mesh_b":  per_event_mesh,
            "per_event_json_b":  round(per_event_json),
            "per_loc_mesh_b":    per_loc_mesh,
            "per_loc_json_b":    round(per_loc_json),
        },
    }


def _get_bandwidth_data() -> dict:
    global _bw_data
    if _bw_data is None:
        _bw_data = _compute_bandwidth_data()
    return _bw_data


@app.route("/api/mesh/bandwidth")
def api_mesh_bandwidth():
    """Session 16 — Mesh bandwidth accounting for the top-bar strip + popover.

    Optional query param:
        scenario=<basename>   — when provided, returns that scenario's
                                last_tactical and last_loc_summary packets
                                as the ``last_packet`` field for the popover.

    Always returns grand totals + per_scenario breakdown + extrapolation.
    The UI accumulates its own session totals locally; the server is stateless.
    """
    data = _get_bandwidth_data()
    if "error" in data:
        return jsonify(data), 503

    scenario = request.args.get("scenario")

    # Default samples: global first tactical + first loc_summary
    base_samples = data.get("samples") or {}

    # When a scenario key is supplied, override samples with that scenario's
    # last packets so the UI strip and hex-dump popover reflect the current scene.
    if scenario:
        full_ps = data.get("_per_scenario_full") or {}
        # Exact match first; fall back to substring match
        ps = full_ps.get(scenario)
        if ps is None:
            for key, val in full_ps.items():
                if scenario in key or key in scenario:
                    ps = val
                    break
        if ps:
            sc_samples = {}
            if ps.get("last_tactical"):
                sc_samples["tactical"] = ps["last_tactical"]
            if ps.get("last_loc_summary"):
                sc_samples["loc_summary"] = ps["last_loc_summary"]
            if sc_samples:
                base_samples = {**base_samples, **sc_samples}

    # Build a clean response — truncate json_text to keep payload small.
    response = {
        "total":         data["total"],
        "per_scenario":  data["per_scenario"],
        "extrapolation": data["extrapolation"],
        "samples": {
            kind: {
                "kind":       s["kind"],
                "mesh_bytes": s["mesh_bytes"],
                "json_bytes": s["json_bytes"],
                "hex_mesh":   s["hex_mesh"],
                "json_text":  s["json_text"][:400],
                "scenario":   s["scenario"],
            }
            for kind, s in base_samples.items()
            if s is not None
        },
    }
    return jsonify(response)


# ── CORS header (permissive — demo only) ─────────────────────────────────────

@app.after_request
def add_cors(response):                              # noqa: D103
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


# ── Entry point ───────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="python -m triangulation.server",
        description="Tactical Edge — Flask backend (Session 8)",
    )
    p.add_argument("--port", type=int, default=5050,
                   help="listen port (default 5050)")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind host (default 127.0.0.1; use 0.0.0.0 for LAN)")
    args = p.parse_args(argv)

    # Pre-load events so first request is fast
    events, groups = _load_events()
    n_ok = sum(1 for _, g in groups.items() if _localizable(g)[0])
    print(f"Tactical Edge server")
    print(f"  Events loaded: {len(events)}  Localizable scenarios: {n_ok}")
    print(f"  UI:   http://{args.host}:{args.port}/")
    print(f"  API:  http://{args.host}:{args.port}/api/scenarios")
    print()

    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main(sys.argv[1:])
