"""
solver_2drone.py — TDOA hyperbola locus for exactly two drones.

With two receivers the TDOA gives a hyperbola, not a point.
This module traces the relevant branch and builds an MC wedge polygon.

Public API
----------
hyperbola(p1, p2, dd, n_pts=64) -> ndarray shape (N, 2)
    Points along the near branch of the TDOA hyperbola in the
    *local-plane frame* (x=east, y=north, metres).

mc_wedge(events, drone_positions, clock_sigma_s, pos_sigma_m, n=200)
    -> (arcs, hull_xy)
    arcs     : list of (N,2) arrays, one per MC draw
    hull_xy  : (M,2) convex hull enclosing all arc points
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

# Speed of sound (must match solver.py)
C: float = 343.0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _rotate(pts: NDArray, theta: float) -> NDArray:
    """Rotate 2-D points *pts* (shape N×2) by angle *theta* radians."""
    c, s = np.cos(theta), np.sin(theta)
    R = np.array([[c, -s], [s, c]])
    return pts @ R.T


# ---------------------------------------------------------------------------
# Core: single hyperbola arc
# ---------------------------------------------------------------------------

def hyperbola(
    p1: NDArray,
    p2: NDArray,
    dd: float,
    *,
    n_pts: int = 64,
    extent_factor: float = 2.0,
) -> NDArray | None:
    """
    Return *n_pts* points on the TDOA hyperbola branch (local plane, metres).

    Parameters
    ----------
    p1, p2 : array-like shape (2,)
        Drone positions [x, y] in local-plane metres.
    dd : float
        Signed range difference  |x-p1| - |x-p2|  (= Δd = C · Δt  metres).
        Positive ⟹ drone 1 heard the event first (source closer to p1).
    n_pts : int
        Number of points along the arc (before clipping to extent_factor).
    extent_factor : float
        Arc half-width in multiples of the inter-drone separation.
        Default 2 keeps the arc within a reasonable viewport.

    Returns
    -------
    ndarray shape (N, 2) in local-plane metres, or None if the geometry is
    degenerate (|dd| >= inter-drone distance, i.e. no real hyperbola).
    """
    p1 = np.asarray(p1, dtype=float)
    p2 = np.asarray(p2, dtype=float)

    sep = np.linalg.norm(p2 - p1)          # 2c  (focal distance)
    if sep < 1e-6:
        return None

    a = abs(dd) / 2.0                       # semi-transverse axis
    c_foc = sep / 2.0                       # focal half-distance

    if a >= c_foc:
        # |dd| >= sep → unphysical (source would need to travel faster than C)
        return None

    b = np.sqrt(c_foc**2 - a**2)           # semi-conjugate axis

    # Branch sign: Δd > 0 → source on the branch closer to p1 (left branch
    # in the canonical frame, sign = -1); Δd < 0 → right branch, sign = +1.
    # dd = |source-p1| - |source-p2|: dd>0 → closer to p2 → right branch
    branch = 1.0 if dd > 0 else -1.0

    # Parameter range: clip at extent_factor × sep in the conjugate direction
    T_max = np.arcsinh(extent_factor * sep / b)
    t_vals = np.linspace(-T_max, T_max, n_pts)

    # Canonical hyperbola (centre at origin, foci on ±x axis)
    X = branch * a * np.cosh(t_vals)
    Y = b * np.sinh(t_vals)
    pts_canon = np.column_stack([X, Y])

    # Rotation: align the canonical x-axis with the p1→p2 direction,
    # then translate so the centre lies at the midpoint of p1,p2.
    theta = np.arctan2(p2[1] - p1[1], p2[0] - p1[0])
    mid = (p1 + p2) / 2.0

    pts_local = _rotate(pts_canon, theta) + mid
    return pts_local


# ---------------------------------------------------------------------------
# Monte-Carlo wedge
# ---------------------------------------------------------------------------

def mc_wedge(
    events: list[dict],
    drone_positions: NDArray,
    clock_sigma_s: float | NDArray,
    pos_sigma_m: float | NDArray,
    *,
    n: int = 200,
    n_pts: int = 64,
    extent_factor: float = 2.0,
    rng: np.random.Generator | None = None,
) -> tuple[list[NDArray], NDArray]:
    """
    Monte-Carlo uncertainty wedge for a 2-drone TDOA fix.

    Parameters
    ----------
    events : list of 2 dicts, each with ``event_time_ns`` (int).
    drone_positions : ndarray shape (2, 2)
        [[x1, y1], [x2, y2]] in local-plane metres.
    clock_sigma_s : float or array shape (2,)
        Per-drone clock σ in **seconds**.
    pos_sigma_m : float or array shape (2,)
        Per-drone position σ in **metres** (isotropic).
    n : int
        Number of MC draws.
    n_pts : int
        Points per arc passed to :func:`hyperbola`.
    extent_factor : float
        Forwarded to :func:`hyperbola`.
    rng : np.random.Generator or None
        RNG for reproducibility; defaults to ``np.random.default_rng(7)``.

    Returns
    -------
    arcs : list of (N,2) ndarrays
        One arc per successful draw (degenerate draws are dropped).
    hull_xy : (M,2) ndarray
        Convex hull of all arc points, or empty (0,2) if no arcs.
    """
    if rng is None:
        rng = np.random.default_rng(7)

    n_drones = 2
    clock_sigma_s = np.broadcast_to(
        np.asarray(clock_sigma_s, dtype=float), (n_drones,)
    )
    pos_sigma_m = np.broadcast_to(
        np.asarray(pos_sigma_m, dtype=float), (n_drones,)
    )
    drone_positions = np.asarray(drone_positions, dtype=float)

    t_ns = np.array([ev["event_time_ns"] for ev in events], dtype=np.int64)

    arcs: list[NDArray] = []
    all_pts: list[NDArray] = []

    for _ in range(n):
        # Perturb timestamps
        noise_t_ns = (rng.standard_normal(n_drones) * clock_sigma_s * 1e9).astype(int)
        t_ns_pert = t_ns + noise_t_ns

        # Perturb positions
        pos_pert = drone_positions.copy()
        for i in range(n_drones):
            if pos_sigma_m[i] > 0:
                pos_pert[i] += rng.standard_normal(2) * pos_sigma_m[i]

        dt_s = (t_ns_pert[0] - t_ns_pert[1]) * 1e-9
        dd = C * dt_s

        arc = hyperbola(
            pos_pert[0], pos_pert[1], dd,
            n_pts=n_pts, extent_factor=extent_factor,
        )
        if arc is not None:
            arcs.append(arc)
            all_pts.append(arc)

    if not all_pts:
        return [], np.zeros((0, 2))

    combined = np.vstack(all_pts)

    # Convex hull
    try:
        from scipy.spatial import ConvexHull
        hull = ConvexHull(combined)
        hull_xy = combined[hull.vertices]
        # Close the polygon
        hull_xy = np.vstack([hull_xy, hull_xy[0]])
    except Exception:
        hull_xy = combined

    return arcs, hull_xy


# ---------------------------------------------------------------------------
# Deterministic dd from events
# ---------------------------------------------------------------------------

def dd_from_events(events: list[dict]) -> float:
    """
    Compute range difference Δd = C · (t1 - t2) from two event dicts.

    Positive ⟹ drone at index 0 heard the sound first.
    """
    t0 = events[0]["event_time_ns"]
    t1 = events[1]["event_time_ns"]
    dt_s = (t0 - t1) * 1e-9
    return C * dt_s
