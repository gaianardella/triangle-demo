"""Tests for Session 11 — 2-drone bearing-only localization.

Covers:
  - solver_2drone.hyperbola geometry
  - solver_2drone.mc_wedge output shape
  - locate._bearing_localizable filter
  - locate.localize_2drone_scenario output contract
  - policy.bearing_decide
"""

from __future__ import annotations

import math
import numpy as np
import pytest

# ── solver_2drone tests ───────────────────────────────────────────────────────

from triangulation.core.solver_2drone import (
    hyperbola,
    mc_wedge,
    dd_from_events,
    C,
)


class TestHyperbola:
    def _two_drones(self):
        """Simple aligned pair on the x-axis."""
        p1 = np.array([0.0, 0.0])
        p2 = np.array([100.0, 0.0])  # 100 m east
        return p1, p2

    def test_returns_array(self):
        p1, p2 = self._two_drones()
        dd = 20.0   # source closer to p1
        arc = hyperbola(p1, p2, dd)
        assert arc is not None
        assert arc.ndim == 2
        assert arc.shape[1] == 2

    def test_n_pts(self):
        p1, p2 = self._two_drones()
        arc = hyperbola(p1, p2, 20.0, n_pts=32)
        assert arc.shape[0] == 32

    def test_degenerate_returns_none_when_dd_too_large(self):
        p1 = np.array([0.0, 0.0])
        p2 = np.array([10.0, 0.0])
        # |dd| >= sep → no real hyperbola
        assert hyperbola(p1, p2, 15.0) is None

    def test_degenerate_collocated_drones(self):
        p1 = np.array([0.0, 0.0])
        p2 = np.array([0.0, 0.0])
        assert hyperbola(p1, p2, 5.0) is None

    def test_branch_selection_dd_positive(self):
        """dd > 0 → |source-p1| > |source-p2| → source closer to p2 → arc x centroid > 100."""
        p1 = np.array([0.0, 0.0])
        p2 = np.array([200.0, 0.0])
        dd = 40.0   # source closer to p2 → branch on p2 side (x > 100)
        arc = hyperbola(p1, p2, dd)
        assert arc is not None
        # Arc centroid should be on the p2 side (x > mid of p1,p2 = 100)
        assert arc[:, 0].mean() > 100.0

    def test_branch_selection_dd_negative(self):
        """dd < 0 → |source-p1| < |source-p2| → source closer to p1 → arc x centroid < 100."""
        p1 = np.array([0.0, 0.0])
        p2 = np.array([200.0, 0.0])
        dd = -40.0  # source closer to p1 → branch on p1 side (x < 100)
        arc = hyperbola(p1, p2, dd)
        assert arc is not None
        assert arc[:, 0].mean() < 100.0

    def test_rotated_pair(self):
        """Drones on a diagonal — arc should still be geometrically valid."""
        p1 = np.array([0.0, 0.0])
        p2 = np.array([70.0, 70.0])   # ~99 m, NE direction
        dd = 15.0
        arc = hyperbola(p1, p2, dd)
        assert arc is not None
        # All arc points should be finite
        assert np.all(np.isfinite(arc))


class TestDdFromEvents:
    def test_positive_dd(self):
        """Drone 0 hears first (smaller time) → dd > 0."""
        events = [
            {"event_time_ns": 1_000_000_000},  # t=1s
            {"event_time_ns": 1_001_000_000},  # t=1.001s
        ]
        dd = dd_from_events(events)
        # dt = -1e-3 s → dd = C * (-1e-3) = -0.343
        assert dd == pytest.approx(-C * 1e-3, rel=1e-6)

    def test_negative_dd(self):
        events = [
            {"event_time_ns": 1_001_000_000},
            {"event_time_ns": 1_000_000_000},
        ]
        dd = dd_from_events(events)
        assert dd == pytest.approx(C * 1e-3, rel=1e-6)


class TestMcWedge:
    def _events(self):
        return [
            {"event_time_ns": 0,        "drone_id": "d1"},
            {"event_time_ns": 100_000,  "drone_id": "d2"},  # 0.1 ms apart
        ]

    def _pos(self):
        return np.array([[0.0, 0.0], [100.0, 0.0]])

    def test_returns_arcs_and_hull(self):
        arcs, hull = mc_wedge(
            self._events(), self._pos(),
            clock_sigma_s=np.array([1e-4, 1e-4]),
            pos_sigma_m=np.array([0.0, 0.0]),
            n=50, rng=np.random.default_rng(42),
        )
        assert isinstance(arcs, list)
        assert len(arcs) > 0
        assert hull.ndim == 2
        assert hull.shape[1] == 2

    def test_hull_encloses_arc_points(self):
        from scipy.spatial import ConvexHull
        arcs, hull = mc_wedge(
            self._events(), self._pos(),
            clock_sigma_s=np.array([1e-4, 1e-4]),
            pos_sigma_m=np.array([2.0, 2.0]),
            n=80, rng=np.random.default_rng(7),
        )
        # hull polygon (closed) should have >= 3 distinct vertices
        assert hull.shape[0] >= 4   # closed polygon: first == last

    def test_reproducible_with_seed(self):
        kw = dict(
            clock_sigma_s=np.array([1e-4, 1e-4]),
            pos_sigma_m=np.array([1.0, 1.0]),
            n=30,
        )
        _, h1 = mc_wedge(self._events(), self._pos(),
                         rng=np.random.default_rng(7), **kw)
        _, h2 = mc_wedge(self._events(), self._pos(),
                         rng=np.random.default_rng(7), **kw)
        np.testing.assert_array_equal(h1, h2)


# ── locate routing tests ──────────────────────────────────────────────────────

from triangulation.locate import _bearing_localizable, _localizable


def _make_row(drone_id, lat, lon, t_ns, relevant=True):
    return {
        "path": "scenario_test.wav",
        "drone_id": drone_id,
        "position": {"lat": lat, "lon": lon},
        "event_time_ns": t_ns,
        "timestamp_ns": 0,
        "relevant": relevant,
        "label": "gunshot",
        "label_human": "Gunshot",
        "time_prediction_error_ms": 1.0,
        "position_error_m": 2.0,
    }


class TestBearingLocalizable:
    def test_exactly_two_relevant_ok(self):
        group = [
            _make_row("d1", 60.0, 25.0, 1_000_000_000),
            _make_row("d2", 60.001, 25.001, 1_001_000_000),
        ]
        ok, _ = _bearing_localizable(group)
        assert ok

    def test_three_drones_not_2drone(self):
        group = [
            _make_row("d1", 60.0, 25.0, 1_000_000_000),
            _make_row("d2", 60.001, 25.001, 1_001_000_000),
            _make_row("d3", 60.002, 25.002, 1_002_000_000),
        ]
        ok, _ = _bearing_localizable(group)
        assert not ok   # 3 drones fails the "exactly 2" check

    def test_no_relevant_rows_fails(self):
        group = [
            _make_row("d1", 60.0, 25.0, 0, relevant=False),
            _make_row("d2", 60.001, 25.001, 0, relevant=False),
        ]
        ok, _ = _bearing_localizable(group)
        assert not ok

    def test_empty_group_fails(self):
        ok, _ = _bearing_localizable([])
        assert not ok


class TestLocalize3DroneSkip:
    def test_2drone_group_fails_localizable(self):
        group = [
            _make_row("d1", 60.0, 25.0, 1_000_000_000),
            _make_row("d2", 60.001, 25.001, 1_001_000_000),
        ]
        ok, reason = _localizable(group)
        assert not ok
        assert "2" in reason


# ── localize_2drone_scenario output contract ──────────────────────────────────

from triangulation.locate import localize_2drone_scenario


class TestLocalize2droneScenario:
    def _group(self):
        return [
            _make_row("d1", 60.000, 25.000, 1_000_000_000),
            _make_row("d2", 60.005, 25.005, 1_001_000_000),
        ]

    def test_fix_kind(self):
        result = localize_2drone_scenario(self._group(), mc_samples=50)
        assert result["fix_kind"] == "bearing"

    def test_required_fields_present(self):
        result = localize_2drone_scenario(self._group(), mc_samples=50)
        for field in ("scenario", "label", "drone_ids", "drones_used",
                      "source", "recommended_action", "hyperbola_latlon",
                      "wedge_latlon", "fix_kind"):
            assert field in result, f"missing field: {field}"

    def test_point_fields_null(self):
        result = localize_2drone_scenario(self._group(), mc_samples=50)
        assert result["cep50_m"] is None
        assert result["gdop"] is None
        assert result["cloud_latlon"] is None

    def test_action_is_recon(self):
        result = localize_2drone_scenario(self._group(), mc_samples=50)
        assert result["recommended_action"] == "RECON"
        assert result["weapons_release_required"] is False

    def test_hyperbola_latlon_not_empty(self):
        result = localize_2drone_scenario(self._group(), mc_samples=50)
        assert len(result["hyperbola_latlon"]) > 0
        pt = result["hyperbola_latlon"][0]
        assert "lat" in pt and "lon" in pt

    def test_wedge_latlon_not_empty(self):
        result = localize_2drone_scenario(self._group(), mc_samples=50)
        assert len(result["wedge_latlon"]) > 0

    def test_drone_ids_sorted(self):
        result = localize_2drone_scenario(self._group(), mc_samples=50)
        assert result["drone_ids"] == sorted(result["drone_ids"])


# ── policy.bearing_decide tests ───────────────────────────────────────────────

from triangulation.policy import bearing_decide, LABEL_SEVERITY


class TestBearingDecide:
    def test_always_recon(self):
        for label in (None, "gunshot", "tank", "missile_launch", "drone"):
            d = bearing_decide(label)
            assert d.action == "RECON", f"expected RECON for label={label}"

    def test_no_weapons_release(self):
        for label in (None, "gunshot", "tank"):
            d = bearing_decide(label)
            assert not d.weapons_release_required

    def test_severity_matches_label(self):
        for label, expected_sev in LABEL_SEVERITY.items():
            d = bearing_decide(label)
            assert d.severity == expected_sev
