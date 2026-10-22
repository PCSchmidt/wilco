"""Property tests: soundness against an independent oracle, no gratuitous vetoes, monotonicity, and fuzzing."""

import dataclasses
import json
import random

from hypothesis import HealthCheck, given, settings, strategies as st

from wilco.oracle import oracle_safe
from wilco import Envelope, vet
from wilco.schema import parse_mission

FENCE = ((-1000.0, -1000.0), (1000.0, -1000.0), (1000.0, 1000.0), (-1000.0, 1000.0))
CFG = Envelope(fence=FENCE)
SLOW = settings(max_examples=400, deadline=None, suppress_health_check=list(HealthCheck))


def coord(rng, wild):
    return rng.uniform(-1300, 1300) if rng.random() < wild else rng.uniform(-900, 900)


def random_step(rng, wild):
    kind = rng.choice(["goto", "goto", "search_lawnmower", "orbit", "report"])
    alt = rng.uniform(0, 160) if rng.random() < wild else rng.uniform(15, 110)
    if kind == "goto":
        return {"type": "goto", "x": coord(rng, wild), "y": coord(rng, wild), "alt": alt}
    if kind == "search_lawnmower":
        return {
            "type": kind,
            "x0": coord(rng, wild),
            "y0": coord(rng, wild),
            "width": rng.uniform(1, 800),
            "height": rng.uniform(0, 500),
            "spacing": rng.uniform(2, 200),
            "alt": alt,
        }
    if kind == "orbit":
        return {"type": kind, "x": coord(rng, wild), "y": coord(rng, wild), "radius": rng.uniform(5, 400), "turns": rng.uniform(0.05, 3), "alt": alt}
    return {"type": "report", "cls": rng.choice(["vehicle", "person"])}


def random_mission(rng, wild=None):
    wild = rng.choice([0.0, 0.1, 0.5]) if wild is None else wild
    n = rng.randint(0, 6)
    steps = [{"type": "takeoff", "alt": rng.uniform(10, 120) if rng.random() > wild else rng.uniform(0, 200)}]
    steps += [random_step(rng, wild) for _ in range(n)]
    steps.append({"type": "return"})
    return {"id": "p", "lost_link": rng.choice(["rtl", "land"]), "steps": steps}


def random_cfg(rng):
    return Envelope(
        fence=FENCE,
        fence_margin=rng.choice([1.0, 5.0, 20.0]),
        alt_min=rng.choice([10.0, 30.0]),
        alt_max=rng.choice([80.0, 120.0]),
        speed=rng.choice([8.0, 12.0, 18.0]),
        max_duration_s=rng.choice([300.0, 600.0, 1200.0]),
        battery_wh=rng.choice([30.0, 80.0]),
        reserve_frac=rng.choice([0.1, 0.25, 0.4]),
        safety_factor=rng.choice([1.0, 1.2, 1.5]),
    )


def test_the_generator_produces_enough_approved_plans_to_make_the_soundness_test_meaningful():
    rng = random.Random(0)
    approved = sum(vet(random_mission(rng), random_cfg(rng)).approved for _ in range(3000))
    assert approved >= 150, approved


@given(st.randoms(use_true_random=False))
@SLOW
def test_an_approved_plan_is_safe_by_an_independent_oracle(rng):
    cfg = random_cfg(rng)
    plan = random_mission(rng)
    if vet(plan, cfg).approved:
        ok, why = oracle_safe(parse_mission(plan), cfg)
        assert ok, f"monitor approved but the oracle found a {why} violation: {json.dumps(plan)}"


@given(st.randoms(use_true_random=False))
@SLOW
def test_plans_that_are_safe_by_construction_are_not_vetoed(rng):
    # inside +-400 m, altitudes in the band, at most four waypoints: well within the energy budget
    pts = [{"type": "goto", "x": rng.uniform(-400, 400), "y": rng.uniform(-400, 400), "alt": rng.uniform(20, 100)} for _ in range(rng.randint(0, 4))]
    plan = {"id": "s", "lost_link": rng.choice(["rtl", "land"]), "steps": [{"type": "takeoff", "alt": rng.uniform(20, 100)}, *pts, {"type": "report", "cls": "vehicle"}, {"type": "return"}]}
    d = vet(plan, CFG)
    assert d.approved, (json.dumps(plan), d.vetoes)


@given(st.randoms(use_true_random=False))
@SLOW
def test_tightening_the_envelope_never_turns_a_veto_into_an_approval(rng):
    cfg = random_cfg(rng)
    plan = random_mission(rng)
    tighter = rng.choice(
        [
            dataclasses.replace(cfg, reserve_frac=min(0.9, cfg.reserve_frac + 0.1)),
            dataclasses.replace(cfg, safety_factor=cfg.safety_factor + 0.2),
            dataclasses.replace(cfg, max_duration_s=cfg.max_duration_s * 0.8),
            dataclasses.replace(cfg, alt_max=cfg.alt_max - 5, alt_min=cfg.alt_min + 5),
            dataclasses.replace(cfg, fence_margin=cfg.fence_margin + 10),
            dataclasses.replace(cfg, battery_wh=cfg.battery_wh * 0.9),
            dataclasses.replace(cfg, fence=tuple((x * 0.9, y * 0.9) for x, y in cfg.fence)),
        ]
    )
    if not vet(plan, cfg).approved:
        assert not vet(plan, tighter).approved, json.dumps(plan)


@given(st.randoms(use_true_random=False))
@SLOW
def test_the_decision_is_deterministic(rng):
    cfg, plan = random_cfg(rng), random_mission(rng)
    assert vet(plan, cfg) == vet(json.dumps(plan), cfg)


JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True, allow_infinity=True) | st.text(max_size=12),
    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=8), c, max_size=5),
    max_leaves=12,
)


@given(JSON)
@SLOW
def test_arbitrary_json_never_crashes_the_monitor_and_is_never_approved(obj):
    d = vet(obj, CFG)
    assert not d.approved  # a random JSON value is essentially never a valid, safe mission


@given(st.text(max_size=200))
@SLOW
def test_arbitrary_text_never_crashes_the_monitor(text):
    assert not vet(text, CFG).approved


@given(st.randoms(use_true_random=False), JSON)
@SLOW
def test_a_valid_plan_with_one_field_replaced_by_junk_never_crashes(rng, junk):
    plan = random_mission(rng, wild=0.0)
    step = rng.choice(plan["steps"])
    key = rng.choice(list(step))
    step[key] = junk
    d = vet(json.dumps(plan, allow_nan=True), CFG)
    if d.approved:  # junk happened to be a legal value: the oracle must still agree
        assert oracle_safe(parse_mission(json.loads(json.dumps(plan))), CFG)[0]
