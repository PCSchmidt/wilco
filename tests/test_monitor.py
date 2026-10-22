"""Hand-written cases: one per rule, plus the boundaries that matter."""

import json

import pytest

from wilco import Context, Envelope, Evidence, State, check_action, vet
from wilco.schema import ReportAction, SchemaError, TrackAction, parse_json

FENCE = ((-1000.0, -1000.0), (1000.0, -1000.0), (1000.0, 1000.0), (-1000.0, 1000.0))
CFG = Envelope(fence=FENCE)


def mission(*steps, lost_link="rtl"):
    return {"id": "m1", "lost_link": lost_link, "steps": list(steps)}


TAKEOFF = {"type": "takeoff", "alt": 40}
RETURN = {"type": "return"}


def goto(x, y, alt=40):
    return {"type": "goto", "x": x, "y": y, "alt": alt}


GOOD = mission(
    TAKEOFF,
    {"type": "search_lawnmower", "x0": -300, "y0": -300, "width": 600, "height": 100, "spacing": 50, "alt": 60},
    {"type": "orbit", "x": 200, "y": 200, "radius": 50, "turns": 2, "alt": 60},
    {"type": "report", "cls": "vehicle"},
    RETURN,
)


def rules(plan, cfg=CFG, state=None):
    return vet(plan, cfg, state).rules


def test_a_reasonable_search_mission_is_approved():
    d = vet(GOOD, CFG)
    assert d.approved, d.vetoes


def test_goto_outside_the_fence_is_vetoed_with_the_step():
    d = vet(mission(TAKEOFF, goto(1500, 0), RETURN), CFG)
    assert "geofence" in d.rules
    assert any(v.step == 1 for v in d.vetoes if v.rule == "geofence")


def test_fence_margin_is_enforced():
    assert vet(mission(TAKEOFF, goto(990, 0), RETURN), CFG).approved
    assert "geofence" in rules(mission(TAKEOFF, goto(997, 0), RETURN))
    assert "geofence" in rules(mission(TAKEOFF, goto(1000, 0), RETURN))


def test_a_leg_may_not_clip_the_fence_even_if_it_returns_inside():
    # a non-convex fence is rejected at construction, so a clipped leg means an endpoint is outside; check both ends
    assert "geofence" in rules(mission(TAKEOFF, goto(0, 0), goto(1100, 0), goto(0, 0), RETURN))


def test_altitude_band_both_sides():
    assert "altitude" in rules(mission(TAKEOFF, goto(0, 100, 200), RETURN))
    assert "altitude" in rules(mission(TAKEOFF, goto(0, 100, 5), RETURN))
    assert "altitude" in rules(mission({"type": "takeoff", "alt": 300}, RETURN))
    assert vet(mission(TAKEOFF, goto(0, 100, 10), goto(0, 200, 120), RETURN), CFG).approved


def test_battery_reserve_vetoes_a_plan_that_cannot_get_home():
    plan = mission(TAKEOFF, *[goto(900 * (-1) ** i, 900) for i in range(40)], RETURN)
    d = vet(plan, CFG)
    assert "battery" in d.rules


def test_battery_start_below_reserve():
    low = State(0.0, 0.0, 0.0, 10.0)
    assert any(v.code == "start" for v in vet(GOOD, CFG, low).vetoes)


def test_duration_limit():
    slow = Envelope(fence=FENCE, max_duration_s=30.0)
    assert "duration" in rules(GOOD, slow)


@pytest.mark.parametrize(
    "plan",
    [
        mission(goto(0, 100), RETURN),  # no takeoff first
        mission(TAKEOFF, goto(0, 100)),  # no return last
        mission(TAKEOFF, RETURN, goto(0, 100), RETURN),  # step after return
        mission(TAKEOFF, TAKEOFF, RETURN),  # two takeoffs
    ],
)
def test_sequence_rules(plan):
    assert "sequence" in rules(plan)


def test_orbit_rules():
    assert "bad_param" in rules(mission(TAKEOFF, {"type": "orbit", "x": 0, "y": 0, "radius": 5, "turns": 1, "alt": 40}, RETURN))
    assert "geofence" in rules(mission(TAKEOFF, {"type": "orbit", "x": 900, "y": 0, "radius": 150, "turns": 1, "alt": 40}, RETURN))
    assert "bad_param" in rules(mission(TAKEOFF, {"type": "orbit", "x": 0, "y": 0, "radius": 50, "turns": 0, "alt": 40}, RETURN))


def test_a_long_orbit_is_vetoed_on_energy():
    plan = mission(TAKEOFF, {"type": "orbit", "x": 0, "y": 500, "radius": 400, "turns": 20, "alt": 40}, RETURN)
    assert "battery" in rules(plan)


def test_lawnmower_param_rules():
    base = {"type": "search_lawnmower", "x0": -100, "y0": -100, "width": 200, "height": 100, "spacing": 50, "alt": 40}
    assert vet(mission(TAKEOFF, base, RETURN), CFG).approved
    assert "bad_param" in rules(mission(TAKEOFF, {**base, "spacing": 1}, RETURN))
    assert "bad_param" in rules(mission(TAKEOFF, {**base, "width": 0}, RETURN))
    assert "geofence" in rules(mission(TAKEOFF, {**base, "x0": 900, "width": 200}, RETURN))


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"id":"m","lost_link":"rtl","steps":[]}',
        '{"id":"m","lost_link":"continue","steps":[{"type":"return"}]}',
        '{"id":"m","lost_link":"rtl","steps":[{"type":"strike","x":1}]}',
        '{"id":"m","lost_link":"rtl","steps":[{"type":"goto","x":NaN,"y":0,"alt":40}]}',
        '{"id":"m","lost_link":"rtl","steps":[{"type":"goto","x":Infinity,"y":0,"alt":40}]}',
        '{"id":"m","lost_link":"rtl","steps":[{"type":"goto","x":true,"y":0,"alt":40}]}',
        '{"id":"m","lost_link":"rtl","steps":[{"type":"goto","x":1e30,"y":0,"alt":40}]}',
        '{"id":"m","lost_link":"rtl","steps":[{"type":"goto","x":0,"y":0,"alt":40,"speed":99}]}',
        '{"id":"m","lost_link":"rtl","extra":1,"steps":[{"type":"return"}]}',
    ],
)
def test_malformed_plans_are_schema_vetoes(text):
    d = vet(text, CFG)
    assert not d.approved and d.rules == {"schema"}


def test_a_report_step_cannot_smuggle_a_claim():
    d = vet(mission(TAKEOFF, {"type": "report", "cls": "vehicle", "x": 10, "y": 10, "evidence": ["e1"]}, RETURN), CFG)
    assert d.rules == {"schema"}


def test_no_step_type_can_engage_a_target():
    for t in ("strike", "engage", "release", "attack", "terminal_guidance"):
        assert vet(mission(TAKEOFF, {"type": t}, RETURN), CFG).rules == {"schema"}


def test_the_monitor_fails_closed(monkeypatch):
    import wilco.monitor as mon

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(mon, "check_mission", boom)
    d = vet(GOOD, CFG)
    assert not d.approved and d.vetoes[0].code == "internal"


def test_config_rejects_bad_envelopes():
    with pytest.raises(ValueError):
        Envelope(fence=tuple(reversed(FENCE)))  # clockwise
    with pytest.raises(ValueError):
        Envelope(fence=FENCE, home=(999.0, 0.0))  # home inside the margin
    with pytest.raises(ValueError):
        Envelope(fence=FENCE, fence_margin=0.0)
    with pytest.raises(ValueError):
        Envelope(fence=((0, 0), (10, 0), (10, 10), (0, 10), (5, 5)))  # not convex


# ---------------------------------------------------------------- run-time evidence gate
def ev(**kw):
    base = dict(id="e1", cls="vehicle", confidence=0.9, x=100.0, y=100.0, t=95.0, source="onboard_camera")
    base.update(kw)
    return Evidence(**base)


def ctx(*evs, state=None, now=100.0):
    return Context(state or State(0.0, 0.0, 40.0, 70.0), {e.id: e for e in evs}, now)


def report(**kw):
    base = dict(cls="vehicle", x=105.0, y=100.0, evidence_ids=("e1",))
    base.update(kw)
    return ReportAction(**base)


def test_a_supported_report_is_allowed():
    assert check_action(report(), ctx(ev()), CFG).approved


@pytest.mark.parametrize(
    "action,context,code",
    [
        (report(evidence_ids=()), ctx(ev()), "none"),
        (report(evidence_ids=("zzz",)), ctx(ev()), "unknown"),
        (report(cls="person"), ctx(ev()), "class_mismatch"),
        (report(), ctx(ev(confidence=0.3)), "low_confidence"),
        (report(), ctx(ev(t=10.0)), "stale"),
        (report(), ctx(ev(t=120.0)), "stale"),  # from the future
        (report(x=500.0), ctx(ev()), "position_mismatch"),
        (report(), ctx(ev(source="planner")), "untrusted_source"),
        (report(), ctx(ev(confidence=float("nan"))), "low_confidence"),
    ],
)
def test_each_evidence_failure_has_its_own_veto(action, context, code):
    d = check_action(action, context, CFG)
    assert not d.approved and code in {v.code for v in d.vetoes}


def test_one_bad_citation_spoils_the_report():
    good, bad = ev(), ev(id="e2", confidence=0.1)
    assert not check_action(report(evidence_ids=("e1", "e2")), ctx(good, bad), CFG).approved


def test_tracking_needs_evidence_and_still_obeys_geometry_and_energy():
    assert check_action(TrackAction("e1"), ctx(ev()), CFG).approved
    assert not check_action(TrackAction("nope"), ctx(ev()), CFG).approved
    outside = ev(x=1500.0, y=0.0)
    assert "geofence" in check_action(TrackAction("e1"), ctx(outside), CFG).rules
    low = State(0.0, 0.0, 40.0, 20.5)
    assert "battery" in check_action(TrackAction("e1"), ctx(ev(), state=low), CFG).rules


def test_non_finite_state_is_a_veto():
    bad = State(float("nan"), 0.0, 40.0, 70.0)
    assert not check_action(report(), ctx(ev(), state=bad), CFG).approved


def test_parse_json_roundtrip():
    assert parse_json(json.dumps(GOOD)).steps[0].alt == 40
    with pytest.raises(SchemaError):
        parse_json("{")
