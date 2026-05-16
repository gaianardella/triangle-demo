"""Tests for Session 13 — Kill-drone routing in localize_scenario.

Covers:
  - All 3 drones alive → point fix returned
  - 1 drone killed → still 2+ alive → falls back to bearing fix (or point fix)
  - 2 drones killed (1 alive) → INSUFFICIENT_SENSORS
  - All drones killed → INSUFFICIENT_SENSORS
  - 2 alive drones → bearing fix (via killed_drone_ids)
  - policy.insufficient_sensors_decide output contract
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from triangulation.locate import localize_scenario, _localize_no_fix
from triangulation.policy import insufficient_sensors_decide

# ── Shared synthetic event builder ───────────────────────────────────────────

_SPEED_OF_SOUND = 343.0  # m/s


def _make_events(
    source_lat: float = 60.180,
    source_lon: float = 24.960,
    sigma_t_ms: float = 0.5,
    sigma_pos_m: float = 2.0,
    rng: np.random.Generator | None = None,
) -> list[dict]:
    """Generate 3-drone synthetic events matching real event schema.

    Fields required by locate.py:
      position: {lat, lon, alt_m}
      event_time_ns: int (nanoseconds)
      relevant: bool
      drone_id: str
      time_prediction_error_ms: float  (sigma_t field)
      position_error_m: float          (sigma_pos field)
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
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(
            math.radians(lat2)
        ) * math.sin(dlon / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    # Ground truth travel times from source to each drone
    t0_s = 1_700_000_000.0  # arbitrary base timestamp (s)
    events = []
    for drone_id, dlat, dlon in drone_positions:
        dist_m = haversine_m(source_lat, source_lon, dlat, dlon)
        travel_s = dist_m / _SPEED_OF_SOUND
        noise_s  = rng.normal(0, sigma_t_ms * 1e-3)
        measured_ns = int((t0_s + travel_s + noise_s) * 1e9)
        # Add position noise
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
                "timestamp_ns":  measured_ns,
                "label": "gunshot",
                "relevant": True,
                "time_prediction_error_ms": sigma_t_ms,
                "position_error_m":         sigma_pos_m,
                "path": "test_scenario.wav",
            }
        )
    return events


# ── Tests ────────────────────────────────────────────────────────────────────


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


class TestOneKilledDrone:
    """Kill drone_1; drone_2 + drone_3 remain — should produce a bearing fix."""

    def test_still_produces_fix(self):
        events = _make_events()
        result = localize_scenario(
            events,
            mc_samples=30,
            killed_drone_ids={"drone_1"},
            rng=np.random.default_rng(3),
        )
        # With 2 alive drones that are geometrically separated, we get a bearing fix
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


class TestAllDronesKilled:
    """Kill all 3 drones — must return INSUFFICIENT_SENSORS."""

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


class TestKilledIdsEmptyVsNone:
    """Empty set == None == no filter applied."""

    def test_empty_set_same_as_none(self):
        rng_a = np.random.default_rng(10)
        rng_b = np.random.default_rng(10)
        events = _make_events()
        r_none  = localize_scenario(events, mc_samples=20, killed_drone_ids=None, rng=rng_a)
        r_empty = localize_scenario(events, mc_samples=20, killed_drone_ids=set(), rng=rng_b)
        # Both should be full point fixes
        assert r_none["fix_kind"]  == r_empty["fix_kind"]
        assert r_none["recommended_action"] == r_empty["recommended_action"]


class TestLocalize_no_fix:
    """Unit-test the _localize_no_fix helper directly."""

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


class TestInsufficientSensorsDecide:
    """Unit-test policy.insufficient_sensors_decide."""

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

    def test_reason_mentions_restore(self):
        d = insufficient_sensors_decide("gunshot")
        assert "restore" in d.reason.lower() or "drone" in d.reason.lower()
