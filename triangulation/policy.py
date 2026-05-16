"""ROE policy engine — pure, deterministic, no I/O.

Maps localization quality metrics + threat label onto a recommended
action (STRIKE / RECON / HOLD) and a numeric priority score.

All thresholds are module-level constants so they can be tuned for a
live demo without touching the logic.

Usage
-----
    from triangulation.policy import decide, priority

    decision = decide(cep50_m=4.2, gdop=1.8, label="tank", confidence=0.88)
    # Decision(action='STRIKE', reason='...', severity='high',
    #           weapons_release_required=True)

    score = priority(label="tank", recommended_action="STRIKE",
                     cep50_m=4.2, severity="high")
    # e.g. 118.64
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

# ── Thresholds ───────────────────────────────────────────────────────────────

# A fix with CEP50 below this (metres) AND GDOP below STRIKE_GDOP_MAX
# is tight enough to authorise a strike.
STRIKE_CEP_MAX: float = 10.0

# Above this GDOP the geometry is too poor for a strike even if CEP50 is small.
STRIKE_GDOP_MAX: float = 3.0

# Below this localisation_confidence the fix is considered unusable — HOLD
# regardless of any other metric.
HOLD_CONFIDENCE_FLOOR: float = 0.10

# CEP50 above this (metres) triggers a SEARCH sweep rather than RECON.
# The fix is real but too imprecise for a point engagement.
SEARCH_CEP_FLOOR: float = 50.0

# Only these labels are eligible for a STRIKE recommendation.
STRIKE_ELIGIBLE_LABELS: tuple[str, ...] = ("gunshot", "missile_launch", "tank")

# ── Severity mapping ─────────────────────────────────────────────────────────

LABEL_SEVERITY: dict[str | None, str] = {
    "missile_launch": "high",
    "tank":           "high",
    "gunshot":        "medium",
    "drone":          "low",
    None:             "low",
}

# ── Priority scoring constants ────────────────────────────────────────────────

SEVERITY_BASE: dict[str, float] = {
    "high":   100.0,
    "medium":  50.0,
    "low":     20.0,
}

ACTION_BONUS: dict[str, float] = {
    "STRIKE": 20.0,
    "RECON":  10.0,
    "SEARCH":  5.0,
    "HOLD":    0.0,
    "INSUFFICIENT_SENSORS": 0.0,
}

# Each metre of CEP50 beyond 10 m subtracts this from the priority score.
PRIORITY_CEP_PENALTY_PER_M: float = 0.3
PRIORITY_CEP_PENALTY_FLOOR: float = 10.0

# Labels that always get STRIKE once confidence is above the HOLD floor,
# regardless of CEP50 / GDOP thresholds.
ALWAYS_STRIKE_LABELS: tuple[str, ...] = ("gunshot",)

# ── Types ────────────────────────────────────────────────────────────────────

Action = Literal["STRIKE", "RECON", "SEARCH", "HOLD", "INSUFFICIENT_SENSORS"]


@dataclass(frozen=True)
class Decision:
    """Output of :func:`decide`."""

    action: Action
    reason: str
    severity: str
    weapons_release_required: bool


def insufficient_sensors_decide(label: str | None) -> Decision:
    """Decision when fewer than 2 alive drones make a fix impossible."""
    severity = LABEL_SEVERITY.get(label, "low")
    return Decision(
        action="INSUFFICIENT_SENSORS",
        reason=(
            "fewer than 2 alive drones — cannot compute fix; "
            "restore drones to resume localization"
        ),
        severity=severity,
        weapons_release_required=False,
    )


def bearing_decide(label: str | None) -> Decision:
    """Decision for a 2-drone bearing-only (hyperbola locus) fix.

    Without a resolved point we can never authorise a strike.  The action is
    always RECON — we have a direction to investigate but not a coordinate.
    Severity is inherited from the label so urgency ordering is preserved.
    """
    severity = LABEL_SEVERITY.get(label, "low")
    return Decision(
        action="RECON",
        reason=(
            "2-drone bearing-only fix; source position is on a hyperbola locus "
            "— RECON required to resolve location before further action"
        ),
        severity=severity,
        weapons_release_required=False,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def search_points(
    center_xy: "np.ndarray",
    cov: "np.ndarray",
    n: int = 3,
) -> "np.ndarray":
    """Return *n* sweep positions spaced along the ellipse major axis.

    Parameters
    ----------
    center_xy:
        2-element array — TDOA point estimate in local metric coordinates (m).
    cov:
        2×2 covariance matrix from the MC cloud (same local frame).
    n:
        Number of sweep points.  Default 3 produces
        ``[center - semi_major·v̂, center, center + semi_major·v̂]``.

    Returns
    -------
    np.ndarray, shape (n, 2)
        Sweep waypoints in the same local metric frame as *center_xy*.
    """
    # Eigenvector of the largest eigenvalue = major axis direction.
    eigvals, eigvecs = np.linalg.eigh(cov)
    major_idx = int(np.argmax(eigvals))
    major_vec = eigvecs[:, major_idx]          # unit vector (eigh guarantees this)

    # Semi-major axis at 95 % confidence: chi2(df=2, 0.95) ≈ 5.991
    semi_major = float(np.sqrt(max(eigvals[major_idx], 0.0) * 5.991))

    offsets = np.linspace(-semi_major, semi_major, n)
    return np.array([np.asarray(center_xy, float) + o * major_vec for o in offsets])


def decide(
    cep50_m: float,
    gdop: float,
    label: str | None,
    confidence: float,
) -> Decision:
    """Return a :class:`Decision` for the given localization quality + label.

    Parameters
    ----------
    cep50_m:
        50th-percentile circular error in metres.
    gdop:
        Geometric dilution of precision (≥ 1.0).
    label:
        Threat class from the audio classifier, e.g. ``"tank"``.  ``None``
        means not relevant (should never reach this function in practice, but
        handled gracefully).
    confidence:
        ``localization_confidence`` score (0–1).

    Notes
    -----
    ``confidence`` is derived from ``cep50_m`` so gating on *both* would
    double-count the same signal.  ``confidence`` is only used for the
    absolute HOLD floor; all other decisions use ``cep50_m`` + ``gdop``
    + ``label`` directly.
    """
    severity = LABEL_SEVERITY.get(label, "low")

    # 1. Unusable fix — HOLD regardless.
    if confidence < HOLD_CONFIDENCE_FLOOR:
        return Decision(
            action="HOLD",
            reason=f"localisation_confidence {confidence:.3f} below floor "
                   f"{HOLD_CONFIDENCE_FLOOR}",
            severity=severity,
            weapons_release_required=False,
        )

    # 1b. Fix is real but too coarse for a point engagement — SEARCH.
    # Checked before the strike/recon envelope so that even unconditionally
    # strike-eligible labels don't get weapons release on a 200 m cloud.
    if cep50_m > SEARCH_CEP_FLOOR:
        return Decision(
            action="SEARCH",
            reason=(
                f"CEP50 {cep50_m:.1f}m exceeds search floor {SEARCH_CEP_FLOOR}m — "
                "fix too imprecise for point engagement; sweeping sector"
            ),
            severity=severity,
            weapons_release_required=False,
        )

    # 1c. Always-strike labels — bypass CEP50/GDOP envelope.
    if label in ALWAYS_STRIKE_LABELS:
        return Decision(
            action="STRIKE",
            reason=(
                f"label '{label}' is unconditionally strike-authorised "
                f"(CEP50 {cep50_m:.1f}m, GDOP {gdop:.2f})"
            ),
            severity=severity,
            weapons_release_required=True,
        )

    # 2. Strike envelope check.
    strike_eligible = label in STRIKE_ELIGIBLE_LABELS
    cep_ok = cep50_m < STRIKE_CEP_MAX
    gdop_ok = gdop < STRIKE_GDOP_MAX

    if strike_eligible and cep_ok and gdop_ok:
        return Decision(
            action="STRIKE",
            reason=(
                f"CEP50 {cep50_m:.1f}m within strike envelope "
                f"(<{STRIKE_CEP_MAX}m), GDOP {gdop:.2f} (<{STRIKE_GDOP_MAX}), "
                f"label '{label}' is strike-eligible"
            ),
            severity=severity,
            weapons_release_required=True,
        )

    # 3. Build a human-readable reason for RECON.
    # (CEP50 ≤ SEARCH_CEP_FLOOR is guaranteed at this point.)
    reasons: list[str] = []
    if not strike_eligible:
        reasons.append(f"label '{label}' not strike-eligible")
    if not cep_ok:
        reasons.append(f"CEP50 {cep50_m:.1f}m exceeds limit {STRIKE_CEP_MAX}m")
    if not gdop_ok:
        reasons.append(f"GDOP {gdop:.2f} exceeds limit {STRIKE_GDOP_MAX}")

    return Decision(
        action="RECON",
        reason="; ".join(reasons) or "RECON by default",
        severity=severity,
        weapons_release_required=False,
    )


def priority(
    label: str | None,
    recommended_action: Action,
    cep50_m: float | None,
    severity: str,
) -> float:
    """Numeric threat priority score (higher = more urgent).

    Formula::

        base    = SEVERITY_BASE[severity]
        bonus   = ACTION_BONUS[recommended_action]
        penalty = max(0, cep50_m - PRIORITY_CEP_PENALTY_FLOOR)
                  * PRIORITY_CEP_PENALTY_PER_M
        score   = base + bonus - penalty

    The absolute values don't matter, only relative order across scenarios.
    ``cep50_m`` may be ``None`` for bearing-only fixes; the penalty is zero
    in that case (bearing fixes are still ranked by severity / action).
    """
    base = SEVERITY_BASE.get(severity, SEVERITY_BASE["low"])
    bonus = ACTION_BONUS.get(recommended_action, 0.0)
    if cep50_m is None:
        penalty = 0.0
    else:
        penalty = max(0.0, cep50_m - PRIORITY_CEP_PENALTY_FLOOR) * PRIORITY_CEP_PENALTY_PER_M
    return base + bonus - penalty
