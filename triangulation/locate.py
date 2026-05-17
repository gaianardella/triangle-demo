"""TDOA localisation pipeline — events.json -> localizations.json.

Reads the detection JSON, groups events by ``path`` (one scenario =
one event from three drones), filters out non-relevant rows, projects
each drone's lat/lon to a local metric plane, runs the TDOA solver,
and runs a Monte-Carlo cloud using the per-drone error fields from
the input JSON. Writes a sibling JSON with the source coordinates +
95% confidence cloud + CEP statistics for each scenario.

Usage
-----
    python -m triangulation.locate \\
        --in  detection/output/events.json \\
        --out detection/output/localizations.json

Optional flags
--------------
    --mc-samples N        MC sample count (default 400)
    --confidence 0.95     ellipse confidence level (default 0.95)
    --cloud-format ellipse|hull|samples
                          how to dump cloud_95 (default 'ellipse')
    --pretty              pretty-print the output JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np

from .core import (C, ellipse_axes, ellipse_xy, localize, mc_confidence,
                    solver_2drone)
from .jam import apply_jamming
from .policy import (decide as _policy_decide, priority as _policy_priority,
                      search_points as _search_points,
                      bearing_decide as _bearing_decide,
                      insufficient_sensors_decide as _insufficient_sensors_decide)
from .projection import (latlon_to_local, latlon_to_local_array,
                         local_to_latlon, local_to_latlon_array)


def _mgrs_or_none(lat: float, lon: float) -> str | None:
    """Convert WGS84 coordinates to an MGRS grid reference (10 m precision).

    Returns ``None`` gracefully when the ``mgrs`` package is not installed.
    """
    try:
        import mgrs  # optional dependency
        m = mgrs.MGRS()
        return str(m.toMGRS(lat, lon, MGRSPrecision=4))  # 4 = 10 m grid square
    except Exception:
        return None


# Field name in the input JSON for per-drone position uncertainty. If the
# field is absent on a given event we fall back to zero (timing-only MC).
POSITION_ERROR_FIELD = "position_error_m"

# Field for the per-drone timing uncertainty. The detector currently
# emits both `_us` and `_ms` columns; the user has confirmed `_ms` is
# the canonical one — single source of truth.
TIME_ERROR_FIELD_MS = "time_prediction_error_ms"


# ---------------------------------------------------------------- grouping
def _group_by_scenario(events: list[dict]) -> dict[str, list[dict]]:
    """Group raw detections by scenario path. Preserves drone order."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        groups[e.get("path", "<unknown>")].append(e)
    return dict(groups)


def _localizable(group: list[dict]) -> tuple[bool, str]:
    """Decide whether a group has enough info for a full TDOA point fix (3+ drones)."""
    if not group:
        return False, "empty group"
    if not all(bool(e.get("relevant")) for e in group):
        return False, "non-relevant rows present"
    drone_ids = {e["drone_id"] for e in group}
    if len(drone_ids) < 3:
        return False, f"only {len(drone_ids)} distinct drone(s); need 3+ for point fix"
    if any("event_time_ns" not in e or "position" not in e for e in group):
        return False, "missing event_time_ns or position field"
    return True, "ok"


def _bearing_localizable(group: list[dict]) -> tuple[bool, str]:
    """Decide whether a group qualifies for a 2-drone bearing/hyperbola fix."""
    if not group:
        return False, "empty group"
    relevant = [e for e in group if e.get("relevant")]
    if not relevant:
        return False, "no relevant rows"
    drone_ids = {e["drone_id"] for e in relevant}
    if len(drone_ids) != 2:
        return False, f"{len(drone_ids)} relevant drone(s); need exactly 2"
    if any("event_time_ns" not in e or "position" not in e for e in relevant):
        return False, "missing event_time_ns or position field"
    return True, "ok"


# ------------------------------------------------------------ per-scenario
def _per_drone_sigmas(group: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Pull σ_t (seconds) and σ_pos (metres) per detection from the JSON.

    σ_t comes from ``time_prediction_error_ms`` divided by 1000.
    σ_pos comes from ``position_error_m`` (or 0 if absent).
    """
    sigma_t_s = np.array([float(e.get(TIME_ERROR_FIELD_MS, 0.0)) / 1000.0
                          for e in group])
    sigma_p_m = np.array([float(e.get(POSITION_ERROR_FIELD, 0.0))
                          for e in group])
    return sigma_t_s, sigma_p_m


def _bearing_deg(estimate_xy: np.ndarray,
                 ref_xy: np.ndarray) -> tuple[float, float]:
    """Bearing (degrees from N) and slant distance (m) from `ref` to estimate."""
    dx, dy = float(estimate_xy[0] - ref_xy[0]), float(estimate_xy[1] - ref_xy[1])
    bearing = float(np.degrees(np.arctan2(dx, dy)) % 360.0)
    distance = float(np.hypot(dx, dy))
    return bearing, distance


def _confidence_score(cep50_m: float, scale_m: float = 25.0) -> float:
    """Map CEP50 -> [0, 1]. 25 m maps to ~0.5; sub-metre maps to ~1."""
    return float(1.0 / (1.0 + cep50_m / scale_m))


def _cloud_as(cloud_xy: np.ndarray, ellipse_polygon_xy: np.ndarray,
              fmt: str) -> np.ndarray:
    """Pick the cloud representation requested by --cloud-format."""
    if fmt == "ellipse":
        return ellipse_polygon_xy
    if fmt == "hull":
        try:
            from scipy.spatial import ConvexHull
            hull = ConvexHull(cloud_xy)
            return cloud_xy[hull.vertices]
        except Exception:
            return ellipse_polygon_xy
    if fmt == "samples":
        return cloud_xy
    raise ValueError(f"unknown cloud format: {fmt}")


def _localize_no_fix(group: list[dict], *, scenario_variant: str | None = None) -> dict:
    """Return an INSUFFICIENT_SENSORS result when <2 drones are alive."""
    label = group[0].get("label") if group else None
    decision = _insufficient_sensors_decide(label)
    return {
        "fix_kind": "none",
        "source": None,
        "cep50_m": None,
        "cep95_m_approx": None,
        "zone_area_m2": None,
        "gdop": None,
        "recommended_action": decision.action,
        "recommended_action_reason": decision.reason,
        "recommended_action_severity": decision.severity,
        "weapons_release_required": decision.weapons_release_required,
        "localization_confidence": 0.0,
        "cloud_latlon": [],
        "cloud_xy_local": [],
        "hyperbola_latlon": [],
        "hyperbola_xy_local": [],
        "wedge_latlon": [],
        "wedge_xy_local": [],
        "drones_used": [],
        "scenario": scenario_variant or "",
        "input_errors": {},
    }


def localize_scenario(group: list[dict], *,
                       mc_samples: int = 400,
                       confidence: float = 0.95,
                       cloud_format: str = "ellipse",
                       jammed_drone_ids: set[str] | None = None,
                       killed_drone_ids: set[str] | None = None,
                       scenario_variant: str | None = None,
                       sigma_t_override_ms: float | None = None,
                       sigma_pos_override_m: float | None = None,
                       rng: np.random.Generator | None = None) -> dict:
    """Run the full pipeline on one scenario group; return an output dict."""
    rng = rng if rng is not None else np.random.default_rng(7)

    # Session 13: filter out killed drones before any math
    if killed_drone_ids:
        group = [e for e in group if e.get("drone_id") not in killed_drone_ids]

    # Route by number of alive relevant drones
    alive_ids = {e["drone_id"] for e in group if e.get("relevant")}
    n_alive = len(alive_ids)
    if n_alive < 2:
        return _localize_no_fix(group, scenario_variant=scenario_variant)
    if n_alive == 2:
        ok, _ = _bearing_localizable(group)
        if ok:
            return localize_2drone_scenario(
                group,
                mc_samples=mc_samples,
                confidence=confidence,
                scenario_variant=scenario_variant,
                sigma_t_override_ms=sigma_t_override_ms,
                sigma_pos_override_m=sigma_pos_override_m,
                rng=rng,
            )


    # 1. choose a local projection origin: centroid of the drone positions
    lats = np.array([e["position"]["lat"] for e in group])
    lons = np.array([e["position"]["lon"] for e in group])
    lat0, lon0 = float(lats.mean()), float(lons.mean())

    # 2. project drones to local plane
    xy = latlon_to_local_array(lats, lons, lat0, lon0)
    drone_positions = {e["drone_id"]: tuple(xy[i])
                       for i, e in enumerate(group)}

    # 3. localize (grid + LM)
    estimate_xy, _diag = localize(group, drone_positions,
                                   ts_field="event_time_ns")

    # 4. Monte-Carlo cloud with per-drone σ
    sigma_t_s, sigma_p_m = _per_drone_sigmas(group)
    # Apply global overrides when supplied (replace, not add)
    if sigma_t_override_ms is not None:
        sigma_t_s = np.full_like(sigma_t_s, float(sigma_t_override_ms) / 1000.0)
    if sigma_pos_override_m is not None:
        sigma_p_m = np.full_like(sigma_p_m, float(sigma_pos_override_m))
    mc = mc_confidence(group, drone_positions,
                       clock_sigma_s=sigma_t_s,
                       pos_sigma_m=sigma_p_m,
                       n=mc_samples, x0=estimate_xy,
                       ts_field="event_time_ns",
                       rng=rng)

    # 5. ellipse + summary stats
    ellipse_pts_xy = ellipse_xy(mc["mean"], mc["cov"], conf=confidence)
    major, minor = ellipse_axes(mc["cov"], conf=confidence)
    gdop = float(major / max(minor, 1e-9))
    zone_area = float(np.pi * major * minor)
    cep50 = float(mc["cep50"])
    # cep95 ≈ cep50 * 2.08 for a 2-D Rayleigh (good enough as a hint)
    cep95 = cep50 * 2.08

    # 6. project estimate + cloud polygon back to lat/lon
    src_lat, src_lon = local_to_latlon(float(estimate_xy[0]),
                                        float(estimate_xy[1]),
                                        lat0, lon0)
    cloud_xy = _cloud_as(mc["cloud"], ellipse_pts_xy, cloud_format)
    cloud_ll = local_to_latlon_array(cloud_xy, lat0, lon0)

    # 7. handy bearing/distance relative to the first drone (lexical order)
    ids_sorted = sorted({e["drone_id"] for e in group})
    ref_id = ids_sorted[0]
    bearing, distance = _bearing_deg(estimate_xy,
                                      np.asarray(drone_positions[ref_id]))

    label = group[0].get("label")
    decision = _policy_decide(
        cep50_m=cep50,
        gdop=gdop,
        label=label,
        confidence=_confidence_score(cep50),
    )
    priority_score = _policy_priority(
        label=label,
        recommended_action=decision.action,
        cep50_m=cep50,
        severity=decision.severity,
    )

    # ── Session 9: SEARCH pattern ────────────────────────────────────────
    # When the policy emits SEARCH, compute 3 sweep waypoints along the
    # major axis of the uncertainty ellipse and include them in the output.
    search_pattern_xy: list[list[float]] | None = None
    search_pattern_ll: list[dict] | None = None
    if decision.action == "SEARCH":
        pts = _search_points(estimate_xy, mc["cov"], n=3)
        search_pattern_xy = [[float(p[0]), float(p[1])] for p in pts]
        pts_ll = local_to_latlon_array(pts, lat0, lon0)
        search_pattern_ll = [{"lat": float(p[0]), "lon": float(p[1])}
                              for p in pts_ll]

    return {
        "scenario": Path(group[0].get("path", "")).name,
        "label": label,
        "label_human": group[0].get("label_human"),
        "event_timestamp_ns": int(group[0].get("timestamp_ns", 0)),
        "drone_ids": ids_sorted,
        "drones_used": [
            {"drone_id": e["drone_id"],
             "lat": float(e["position"]["lat"]),
             "lon": float(e["position"]["lon"]),
             "event_time_ns": int(e["event_time_ns"]),
             "sigma_t_ms": float(e.get(TIME_ERROR_FIELD_MS, 0.0)),
             "sigma_pos_m": float(e.get(POSITION_ERROR_FIELD, 0.0))}
            for e in sorted(group, key=lambda r: r["drone_id"])
        ],
        "source": {
            "lat": src_lat, "lon": src_lon,
            "x_m_local": float(estimate_xy[0]),
            "y_m_local": float(estimate_xy[1]),
            "origin_lat": lat0, "origin_lon": lon0,
        },
        "cep50_m": round(cep50, 3),
        "cep95_m_approx": round(cep95, 3),
        "zone_area_m2": round(zone_area, 3),
        "gdop": round(gdop, 3),
        "localization_confidence": round(_confidence_score(cep50), 3),
        "bearing_from_first_drone_deg": round(bearing, 2),
        "distance_from_first_drone_m": round(distance, 2),
        "cloud_format": cloud_format,
        "cloud_confidence": confidence,
        "cloud_latlon": [{"lat": float(p[0]), "lon": float(p[1])}
                          for p in cloud_ll],
        "cloud_xy_local": [[float(p[0]), float(p[1])] for p in cloud_xy],
        "input_errors": {
            "time_ms_max": float(np.max(sigma_t_s) * 1000.0),
            "position_m_max": float(np.max(sigma_p_m)),
            "time_s_per_drone": [float(x) for x in sigma_t_s],
            "position_m_per_drone": [float(x) for x in sigma_p_m],
            "sigma_t_override_ms": sigma_t_override_ms,
            "sigma_pos_override_m": sigma_pos_override_m,
        },
        # ROE policy fields (Session 1)
        "recommended_action": decision.action,
        "recommended_action_reason": decision.reason,
        "recommended_action_severity": decision.severity,
        "weapons_release_required": decision.weapons_release_required,
        "source_mgrs": _mgrs_or_none(src_lat, src_lon),
        "threat_priority": round(priority_score, 3),
        # Jamming fields (Session 2) — null when not in a jammed variant
        "scenario_variant": scenario_variant,
        "jam_status_per_drone": (
            {e["drone_id"]: e.get("jam_status", "clean") for e in group}
            if jammed_drone_ids is not None else None
        ),
        # Session 9: fix kind + SEARCH sweep pattern
        "fix_kind": "point",       # "bearing" added in Session 11
        "search_pattern_xy_local": search_pattern_xy,
        "search_pattern_latlon":   search_pattern_ll,
    }


# --------------------------------------------------------- 2-drone bearing fix
def localize_2drone_scenario(group: list[dict], *,
                              mc_samples: int = 400,
                              confidence: float = 0.95,
                              jammed_drone_ids: set[str] | None = None,
                              scenario_variant: str | None = None,
                              sigma_t_override_ms: float | None = None,
                              sigma_pos_override_m: float | None = None,
                              rng: np.random.Generator | None = None) -> dict:
    """Run a bearing-only (hyperbola locus) fix for exactly 2 relevant drones.

    Returns a dict with the same top-level keys as ``localize_scenario`` where
    possible, plus:
      - ``fix_kind``          = "bearing"
      - ``hyperbola_latlon``  = list[{lat, lon}] deterministic arc
      - ``hyperbola_xy_local``= list[[x, y]] same arc in local-plane metres
      - ``wedge_latlon``      = list[{lat, lon}] MC uncertainty hull polygon
      - ``wedge_xy_local``    = list[[x, y]] hull in local-plane metres

    Fields that require a point fix (``source``, ``cep50_m``, ``gdop``,
    ``zone_area_m2``, ``cloud_*``) are set to ``null``.
    """
    rng = rng if rng is not None else np.random.default_rng(7)

    # Use only relevant rows
    relevant = [e for e in group if e.get("relevant")]
    # Sort by drone_id for determinism
    relevant = sorted(relevant, key=lambda e: e["drone_id"])

    lats = np.array([e["position"]["lat"] for e in relevant])
    lons = np.array([e["position"]["lon"] for e in relevant])
    lat0, lon0 = float(lats.mean()), float(lons.mean())

    xy = latlon_to_local_array(lats, lons, lat0, lon0)
    p1, p2 = xy[0], xy[1]

    # Deterministic range difference
    dd = solver_2drone.dd_from_events(relevant)

    # Deterministic hyperbola arc
    arc_xy = solver_2drone.hyperbola(p1, p2, dd, n_pts=64)

    if arc_xy is not None:
        arc_ll = local_to_latlon_array(arc_xy, lat0, lon0)
        hyperbola_latlon = [{"lat": float(p[0]), "lon": float(p[1])}
                            for p in arc_ll]
        hyperbola_xy_local = [[float(p[0]), float(p[1])] for p in arc_xy]
    else:
        hyperbola_latlon = []
        hyperbola_xy_local = []

    # Per-drone sigmas
    sigma_t_s, sigma_p_m = _per_drone_sigmas(relevant)
    if sigma_t_override_ms is not None:
        sigma_t_s = np.full_like(sigma_t_s, float(sigma_t_override_ms) / 1000.0)
    if sigma_pos_override_m is not None:
        sigma_p_m = np.full_like(sigma_p_m, float(sigma_pos_override_m))

    # MC wedge
    drone_pos_array = np.array([p1, p2])
    _, hull_xy = solver_2drone.mc_wedge(
        relevant, drone_pos_array,
        clock_sigma_s=sigma_t_s,
        pos_sigma_m=sigma_p_m,
        n=mc_samples, n_pts=64, rng=rng,
    )
    if hull_xy.shape[0] > 0:
        hull_ll = local_to_latlon_array(hull_xy, lat0, lon0)
        wedge_latlon = [{"lat": float(p[0]), "lon": float(p[1])} for p in hull_ll]
        wedge_xy_local = [[float(p[0]), float(p[1])] for p in hull_xy]
    else:
        wedge_latlon = []
        wedge_xy_local = []

    # Bearing from first drone to midpoint of the arc (if available)
    ids_sorted = sorted({e["drone_id"] for e in relevant})
    ref_id = ids_sorted[0]
    ref_xy = np.asarray(p1)
    arc_mid_xy = np.mean(arc_xy, axis=0) if arc_xy is not None else ref_xy
    bearing, distance = _bearing_deg(arc_mid_xy, ref_xy)

    label = relevant[0].get("label")

    # Policy: bearing-only fix always gets RECON regardless of label.
    # We cannot authorise a strike without a resolved point estimate.
    decision = _bearing_decide(label)

    priority_score = _policy_priority(
        label=label,
        recommended_action=decision.action,
        cep50_m=None,          # undefined for bearing fix
        severity=decision.severity,
    )

    # Representative lat/lon from arc midpoint (for map display / MGRS hint)
    if arc_xy is not None:
        mid_lat, mid_lon = local_to_latlon(
            float(arc_mid_xy[0]), float(arc_mid_xy[1]), lat0, lon0
        )
    else:
        mid_lat, mid_lon = lat0, lon0

    return {
        "scenario": Path(relevant[0].get("path", "")).name,
        "label": label,
        "label_human": relevant[0].get("label_human"),
        "event_timestamp_ns": int(relevant[0].get("timestamp_ns", 0)),
        "drone_ids": ids_sorted,
        "drones_used": [
            {"drone_id": e["drone_id"],
             "lat": float(e["position"]["lat"]),
             "lon": float(e["position"]["lon"]),
             "event_time_ns": int(e["event_time_ns"]),
             "sigma_t_ms": float(e.get(TIME_ERROR_FIELD_MS, 0.0)),
             "sigma_pos_m": float(e.get(POSITION_ERROR_FIELD, 0.0))}
            for e in relevant
        ],
        # No resolved point source — set to approximate arc midpoint for
        # downstream display; consumers should check fix_kind == "bearing".
        "source": {
            "lat": mid_lat, "lon": mid_lon,
            "x_m_local": float(arc_mid_xy[0]),
            "y_m_local": float(arc_mid_xy[1]),
            "origin_lat": lat0, "origin_lon": lon0,
        },
        # Point-fix quality metrics are undefined for a bearing fix
        "cep50_m": None,
        "cep95_m_approx": None,
        "zone_area_m2": None,
        "gdop": None,
        "localization_confidence": None,
        "bearing_from_first_drone_deg": round(bearing, 2),
        "distance_from_first_drone_m": round(distance, 2),
        # Cloud fields: not applicable for bearing fix
        "cloud_format": None,
        "cloud_confidence": None,
        "cloud_latlon": None,
        "cloud_xy_local": None,
        "input_errors": {
            "time_ms_max": float(np.max(sigma_t_s) * 1000.0),
            "position_m_max": float(np.max(sigma_p_m)),
            "time_s_per_drone": [float(x) for x in sigma_t_s],
            "position_m_per_drone": [float(x) for x in sigma_p_m],
            "sigma_t_override_ms": sigma_t_override_ms,
            "sigma_pos_override_m": sigma_pos_override_m,
        },
        "recommended_action": decision.action,
        "recommended_action_reason": decision.reason,
        "recommended_action_severity": decision.severity,
        "weapons_release_required": decision.weapons_release_required,
        "source_mgrs": _mgrs_or_none(mid_lat, mid_lon),
        "threat_priority": round(priority_score, 3),
        "scenario_variant": scenario_variant,
        "jam_status_per_drone": (
            {e["drone_id"]: e.get("jam_status", "clean") for e in relevant}
            if jammed_drone_ids is not None else None
        ),
        # Session 11: fix kind + bearing-specific locus fields
        "fix_kind": "bearing",
        "hyperbola_latlon": hyperbola_latlon,
        "hyperbola_xy_local": hyperbola_xy_local,
        "wedge_latlon": wedge_latlon,
        "wedge_xy_local": wedge_xy_local,
        # Not applicable for bearing fix
        "search_pattern_xy_local": None,
        "search_pattern_latlon": None,
    }


# ----------------------------------------------------------------- driver
def run(events_path: Path, out_path: Path, *,
        mc_samples: int = 400,
        confidence: float = 0.95,
        cloud_format: str = "ellipse",
        pretty: bool = False,
        verbose: bool = True,
        mesh_publish: bool = False,
        jam_drone: str | None = None,
        jam_position_mult: float = 5.0,
        jam_time_mult: float = 1.0,
        jam_label: str = "gps_jammed",
        variant_tag: str | None = None) -> list[dict]:
    """Top-level: read events, localise every relevant scenario, write JSON."""
    with open(events_path) as f:
        events: list[dict] = json.load(f)

    # Apply jamming to the event list before grouping (if requested).
    jammed_drone_ids: set[str] | None = None
    if jam_drone is not None:
        events = apply_jamming(
            events,
            target_drone_id=jam_drone,
            pos_mult=jam_position_mult,
            time_mult=jam_time_mult,
            jam_label=jam_label,
        )
        jammed_drone_ids = {jam_drone}

    groups = _group_by_scenario(events)
    out: list[dict] = []
    skipped: list[tuple[str, str]] = []
    rng = np.random.default_rng(7)

    for scenario, group in groups.items():
        ok, reason = _localizable(group)
        if ok:
            # ── 3+ drone point fix ─────────────────────────────────────────
            try:
                entry = localize_scenario(
                    group, mc_samples=mc_samples,
                    confidence=confidence,
                    cloud_format=cloud_format,
                    jammed_drone_ids=jammed_drone_ids,
                    scenario_variant=variant_tag,
                    rng=rng,
                )
            except Exception as exc:  # pragma: no cover (defensive)
                skipped.append((scenario, f"error: {exc}"))
                continue
            out.append(entry)
            if verbose:
                print(f"  ✓ {entry['scenario']:40s} "
                      f"({entry['label'] or '':14s}) "
                      f"CEP50={entry['cep50_m']:6.2f}m "
                      f"zone={entry['zone_area_m2']:7.1f}m² "
                      f"gdop={entry['gdop']:5.2f}")
        else:
            # ── check for 2-drone bearing fix ─────────────────────────────
            b_ok, b_reason = _bearing_localizable(group)
            if not b_ok:
                skipped.append((scenario, reason))  # original reason
                continue
            try:
                entry = localize_2drone_scenario(
                    group, mc_samples=mc_samples,
                    confidence=confidence,
                    jammed_drone_ids=jammed_drone_ids,
                    scenario_variant=variant_tag,
                    rng=rng,
                )
            except Exception as exc:  # pragma: no cover (defensive)
                skipped.append((scenario, f"error (bearing): {exc}"))
                continue
            out.append(entry)
            if verbose:
                n_hyp = len(entry.get("hyperbola_latlon") or [])
                print(f"  ~ {entry['scenario']:40s} "
                      f"({entry['label'] or '':14s}) "
                      f"bearing-fix  arc={n_hyp}pts  "
                      f"action={entry['recommended_action']}")

    # Stamp priority_rank (0 = highest priority) across the whole list.
    ranked = sorted(enumerate(out), key=lambda x: x[1]["threat_priority"], reverse=True)
    for rank, (orig_idx, _) in enumerate(ranked):
        out[orig_idx]["priority_rank"] = rank

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2 if pretty else None)

    if verbose:
        print(f"\n  Wrote {len(out)} localisations → {out_path}")
        if skipped:
            print(f"  Skipped {len(skipped)} scenarios:")
            for s, r in skipped:
                print(f"    · {s}: {r}")

    if mesh_publish and out:
        from mesh.publish import publish_localizations_file
        if verbose:
            print("\n  Mesh publish (24 B summaries, not full clouds):")
        publish_localizations_file(out_path, verbose=verbose)

    return out


def _cli(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m triangulation.locate",
        description="Run TDOA localisation over a detection events.json.",
    )
    p.add_argument("--in", dest="inp",
                   default="detection/output/events.json",
                   help="input events.json path")
    p.add_argument("--out", dest="out",
                   default="detection/output/localizations.json",
                   help="output localizations.json path")
    p.add_argument("--mc-samples", type=int, default=400)
    p.add_argument("--confidence", type=float, default=0.95)
    p.add_argument("--cloud-format",
                   choices=("ellipse", "hull", "samples"),
                   default="ellipse")
    p.add_argument("--pretty", action="store_true")
    p.add_argument("--quiet", action="store_true")
    # Jamming flags (Session 2)
    p.add_argument("--jam-drone", default=None,
                   help="drone_id to simulate GPS jamming on")
    p.add_argument("--jam-position-mult", type=float, default=5.0,
                   help="multiply position_error_m for the jammed drone (default 5.0)")
    p.add_argument("--jam-time-mult", type=float, default=1.0,
                   help="multiply time_prediction_error_ms for the jammed drone (default 1.0)")
    p.add_argument("--jam-label", default="gps_jammed",
                   help="jam_status string written for the affected drone (default 'gps_jammed')")
    p.add_argument("--variant-tag", default=None,
                   help="tag written to scenario_variant in every output entry (e.g. 'clean')")
    p.add_argument(
        "--mesh-publish",
        action="store_true",
        help="after writing JSON, publish 24 B loc summaries on tactical mesh (not full clouds)",
    )
    args = p.parse_args(argv)

    run(Path(args.inp), Path(args.out),
        mc_samples=args.mc_samples,
        confidence=args.confidence,
        cloud_format=args.cloud_format,
        pretty=args.pretty,
        verbose=not args.quiet,
        mesh_publish=args.mesh_publish,
        jam_drone=args.jam_drone,
        jam_position_mult=args.jam_position_mult,
        jam_time_mult=args.jam_time_mult,
        jam_label=args.jam_label,
        variant_tag=args.variant_tag)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
