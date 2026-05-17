"""Tests for kill-drone routing in localize_scenario.

Covers:
  - All 3 drones alive → point fix returned
  - 1 drone killed → 2 alive → bearing fix (or point fix)
  - 2 drones killed (1 alive) → INSUFFICIENT_SENSORS
  - All drones killed → INSUFFICIENT_SENSORS
  - empty set vs None → same result
  - _localize_no_fix helper contract
  - policy.insufficient_sensors_decide output contract
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from triangulation.locate import localize_scenario, _localize_no_fix
from triangulation.policy import insufficient_sensors_decide

# ── Synthetic event builder ───────────────────────────────────────────────────

_SPEED_OF_SOUND = 343.0  # m/s


def _make_events(
    source_lat: float = 60.180,
    source_lon: float = 24.960,
    sigma_t_ms: float = 0.5,
    sigma_pos_m: float = 2.0,
    rng: np.random.Generator | None = None,
) -> list[dict]:
    """Generate 3-drone synthetic events matching the real event schema.

    Schema fields required by locate.py:
      position: {lat, lon, alt_m}
      event_time_ns: int (nanoseconds)
      relevant: bool
      drone_id: str
      time_prediction_error_ms: float
      position_error_m: float
    """
    if rng is None:
        rng = np.random.default_rng(42)

    drone_positions = [
        ("drone_1", 60.182, 24.955),
        ("drone_2", 60.178, 24.965),
        ("drone_3", 60.183, 24.968),
    ]

    def haversine_m(lat1, lon1, lat2, lon2):
        R = 6_371_000
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2
        )
        return 2 * R * math.asin(math.sqrt(a))

    t0_s = 1_700_000_000.0  # arbitrary base timestamp (seconds)
    events = []
    for drone_id, dlat, dlon in drone_positions:
        dist_m = haversine_m(source_lat, source_lon, dlat, dlon)
        travel_s = dist_m / _SPEED_OF_SOUND
        noise_s = rng.normal(0, sigma_t_ms * 1e-3)
        measured_ns = int((t0_s + travel_s + noise_s) * 1e9)

        pos_noise_lat = rng.normal(0, sigma_pos_m / 111_320)
        pos_noise_lon = rng.normal(
            0, sigma_pos_m / (111_320 * math.cos(math.radians(dlat)))
        )
        events.append(
            {
                "drone_id": drone_id,
                "position": {
                    "lat": dlat + pos_noise_lat,
                    "lon": dlon + pos_noise_lon,
                    "alt_m": 50.0,
                },
                "event_time_ns": measured_ns,
                "timestamp_ns": measured_ns,
                "label": "gunshot",
                "relevant": True,
                "time_prediction_error_ms": sigma_t_ms,
                "position_error_m": sigma_pos_m,
                "path": "test_scenario.wav",
            }
        )
    return events


# ── All 3 drones alive ────────────────────────────────────────────────────────


class TestAllDronesAlive:
    def test_returns_point_fix(self):
        events = _make_events()
        result = localize_scenario(events, mc_samples=30, rng=np.random.default_rng(1))
        assert result["fix_kind"] == "point", f"Expected point fix, got {result['fix_kind']}"
        assert result["source"] is not None
        assert result["recommended_action"] != "INSUFFICIENT_SENSORS"

    def test_cep50_is_finite(self):
        events = _make_events()
        result = localize_scenario(events, mc_samples=30, rng=np.random.default_rng(2))
        assert result["cep50_m"] is not None
        assert math.isfinite(result["cep50_m"])
        assert result["cep50_m"] > 0


# ── 1 drone killed ────────────────────────────────────────────────────────────


class TestOneKilledDrone:
    """Kill one drone; the remaining two should still produce a fix."""

    def test_still_produces_fix(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_1"},
            rng=np.random.default_rng(3),
        )
        assert result["recommended_action"] != "INSUFFICIENT_SENSORS"
        assert result["fix_kind"] in ("point", "bearing")

    def test_fix_kind_not_none(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_2"},
            rng=np.random.default_rng(4),
        )
        assert result["fix_kind"] != "none"

    def test_kill_drone_3_still_fixes(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_3"},
            rng=np.random.default_rng(11),
        )
        assert result["fix_kind"] in ("point", "bearing")


# ── 2 drones killed (1 alive) ────────────────────────────────────────────────


class TestTwoDronesKilled:
    """Kill 2 drones — only 1 alive — must return INSUFFICIENT_SENSORS."""

    def test_insufficient_sensors_action(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_1", "drone_2"},
            rng=np.random.default_rng(5),
        )
        assert result["recommended_action"] == "INSUFFICIENT_SENSORS"
        assert result["fix_kind"] == "none"

    def test_source_is_none(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_2", "drone_3"},
            rng=np.random.default_rng(6),
        )
        assert result["source"] is None

    def test_cep50_is_none(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_1", "drone_3"},
            rng=np.random.default_rng(7),
        )
        assert result["cep50_m"] is None

    def test_weapons_release_false(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_1", "drone_2"},
            rng=np.random.default_rng(8),
        )
        assert result["weapons_release_required"] is False

    def test_required_fields_present(self):
        """All output dict keys must exist even for the no-fix path."""
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_1", "drone_2"},
            rng=np.random.default_rng(12),
        )
        for key in (
            "fix_kind",
            "source",
            "cep50_m",
            "recommended_action",
            "weapons_release_required",
            "cloud_latlon",
            "hyperbola_latlon",
            "wedge_latlon",
            "drones_used",
        ):
            assert key in result, f"Missing key: {key}"


# ── All drones killed ─────────────────────────────────────────────────────────


class TestAllDronesKilled:
    def test_insufficient_sensors(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_1", "drone_2", "drone_3"},
            rng=np.random.default_rng(9),
        )
        assert result["recommended_action"] == "INSUFFICIENT_SENSORS"
        assert result["fix_kind"] == "none"
        assert result["source"] is None

    def test_cloud_is_empty(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_1", "drone_2", "drone_3"},
            rng=np.random.default_rng(13),
        )
        assert result["cloud_latlon"] == []

    def test_weapons_release_false(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_1", "drone_2", "drone_3"},
            rng=np.random.default_rng(14),
        )
        assert result["weapons_release_required"] is False


# ── Empty set vs None ─────────────────────────────────────────────────────────


class TestKilledIdsEmptyVsNone:
    """An empty set should behave identically to None (no drones filtered)."""

    def test_empty_set_same_as_none(self):
        events = _make_events()
        r_none  = localize_scenario(events, mc_samples=20, killed_drone_ids=None,  rng=np.random.default_rng(10))
        r_empty = localize_scenario(events, mc_samples=20, killed_drone_ids=set(), rng=np.random.default_rng(10))
        assert r_none["fix_kind"]           == r_empty["fix_kind"]
        assert r_none["recommended_action"] == r_empty["recommended_action"]


# ── _localize_no_fix helper ───────────────────────────────────────────────────


class TestLocalizeNoFix:
    def test_required_fields(self):
        events = _make_events()
        result = _localize_no_fix(events)
        assert result["fix_kind"] == "none"
        assert result["source"] is None
        assert result["cep50_m"] is None
        assert result["recommended_action"] == "INSUFFICIENT_SENSORS"
        assert result["weapons_release_required"] is False

    def test_empty_group(self):
        result = _localize_no_fix([])
        assert result["recommended_action"] == "INSUFFICIENT_SENSORS"
        assert result["fix_kind"] == "none"

    def test_localization_confidence_zero(self):
        result = _localize_no_fix(_make_events())
        assert result["localization_confidence"] == 0.0

    def test_hyperbola_and_wedge_empty(self):
        result = _localize_no_fix(_make_events())
        assert result["hyperbola_latlon"] == []
        assert result["wedge_latlon"] == []


# ── policy.insufficient_sensors_decide ───────────────────────────────────────


class TestInsufficientSensorsDecide:
    def test_action_literal(self):
        d = insufficient_sensors_decide("gunshot")
        assert d.action == "INSUFFICIENT_SENSORS"

    def test_weapons_release_false(self):
        d = insufficient_sensors_decide("missile_launch")
        assert d.weapons_release_required is False

    def test_severity_high_for_missile(self):
        d = insufficient_sensors_decide("missile_launch")
        assert d.severity == "high"

    def test_severity_low_for_unknown(self):
        d = insufficient_sensors_decide("unknown_sound")
        assert d.severity == "low"

    def test_severity_low_for_none_label(self):
        d = insufficient_sensors_decide(None)
        assert d.severity == "low"

    def test_reason_mentions_restore_or_drone(self):
        d = insufficient_sensors_decide("gunshot")
        assert "restore" in d.reason.lower() or "drone" in d.reason.lower()

    def test_gunshot_severity_not_low(self):
        """gunshot is a known label in LABEL_SEVERITY so severity > low."""
        d = insufficient_sensors_decide("gunshot")
        assert d.severity != "low"
