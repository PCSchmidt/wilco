"""Where does the monitor flip from approve to veto? It should flip where the brute-force oracle does.

Random plans rarely land near a threshold, so a monitor that is a little too lax (or too strict) passes them.
These tests find each threshold by bisection on one parameter and compare it with the oracle's.
"""

import dataclasses

import pytest

from oracle import oracle_safe
from wilco import Context, Envelope, Evidence, State, check_action, vet
from wilco.schema import TrackAction, parse_mission

BASE = Envelope(fence=((-1000.0, -1000.0), (1000.0, -1000.0), (1000.0, 1000.0), (-1000.0, 1000.0)))


def sq(h):
    return ((-h, -h), (h, -h), (h, h), (-h, h))


def plan(*steps):
    return {"id": "t", "lost_link": "rtl", "steps": [{"type": "takeoff", "alt": 50}, *steps, {"type": "return"}]}


def goto(x, y, alt=60):
    return {"type": "goto", "x": x, "y": y, "alt": alt}


PLANS = {
    "goto": plan(goto(400, 300), goto(-200, 450, 80), goto(100, -300, 30)),
    "lawnmower": plan({"type": "search_lawnmower", "x0": -300, "y0": -200, "width": 500, "height": 200, "spacing": 40, "alt": 70}),
    # whole turns end where they began, at the point of the circle farthest from home, so the conservative bound is tight
    "orbit_far": plan(goto(300, 0), {"type": "orbit", "x": 300, "y": 0, "radius": 150, "turns": 8, "alt": 60}),
    "orbit": plan(goto(300, 0), {"type": "orbit", "x": 300, "y": 0, "radius": 150, "turns": 2.5, "alt": 60}),
}


def bisect(pred, lo, hi, n=40):
    """Smallest x in [lo, hi] with pred(x) true, for pred monotone (false then true)."""
    assert not pred(lo) and pred(hi), "bracket does not straddle the threshold"
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if pred(mid) else (mid, hi)
    return hi


def min_battery(p, monitor):
    parsed = parse_mission(p)
    if monitor:
        return bisect(lambda b: vet(p, dataclasses.replace(BASE, battery_wh=b)).approved, 1.0, 5000.0)
    return bisect(lambda b: oracle_safe(parsed, dataclasses.replace(BASE, battery_wh=b))[0], 1.0, 5000.0)


@pytest.mark.parametrize("name", ["goto", "lawnmower"])
def test_the_energy_threshold_matches_the_oracle(name):
    m, o = min_battery(PLANS[name], True), min_battery(PLANS[name], False)
    assert abs(m - o) / o < 0.01, (m, o)


def test_the_orbit_energy_threshold_matches_the_oracle_for_any_number_of_turns():
    m, o = min_battery(PLANS["orbit"], True), min_battery(PLANS["orbit_far"], True)
    assert abs(m - min_battery(PLANS["orbit"], False)) / m < 0.01
    assert abs(o - min_battery(PLANS["orbit_far"], False)) / o < 0.01


def min_half_fence(p, monitor):
    parsed = parse_mission(p)
    if monitor:
        return bisect(lambda h: vet(p, Envelope(fence=sq(h), fence_margin=5.0)).approved, 20.0, 5000.0)
    return bisect(lambda h: oracle_safe(parsed, Envelope(fence=sq(h), fence_margin=5.0))[0], 20.0, 5000.0)


@pytest.mark.parametrize("name", ["goto", "lawnmower", "orbit", "orbit_far"])
def test_the_fence_threshold_matches_the_oracle(name):
    m, o = min_half_fence(PLANS[name], True), min_half_fence(PLANS[name], False)
    assert abs(m - o) < 1.0, (m, o)


def test_the_orbit_circle_must_keep_its_margin():
    # centre 880 m from the origin on a 1000 m fence: a 118 m circle is 2 m from the boundary, inside the 5 m margin
    near = plan({"type": "orbit", "x": 0, "y": 880, "radius": 118, "turns": 1, "alt": 60})
    ok = plan({"type": "orbit", "x": 0, "y": 880, "radius": 110, "turns": 1, "alt": 60})
    assert "geofence" in vet(near, BASE).rules
    assert vet(ok, BASE).approved


def test_the_altitude_limits_are_inclusive_and_exact():
    for z, ok in [(10.0, True), (9.99, False), (120.0, True), (120.01, False)]:
        assert vet(plan(goto(0, 100, z)), BASE).approved == ok


def test_straight_home_energy_is_enforced_at_run_time():
    # far from home with little energy: tracking a target even 100 m further out would leave too little to get back
    far = State(800.0, 0.0, 40.0, 24.5)
    ev = {"e1": Evidence("e1", "vehicle", 0.9, 900.0, 0.0, 95.0, "onboard_camera")}
    d = check_action(TrackAction("e1"), Context(far, ev, 100.0), BASE)
    assert "battery" in d.rules
    rich = State(800.0, 0.0, 40.0, 40.0)
    assert check_action(TrackAction("e1"), Context(rich, ev, 100.0), BASE).approved


def test_safety_factor_scales_the_threshold():
    base = min_battery(PLANS["goto"], True)
    tighter = bisect(lambda b: vet(PLANS["goto"], dataclasses.replace(BASE, battery_wh=b, safety_factor=1.5)).approved, 1.0, 5000.0)
    assert tighter > base * 1.15
