import json
import pathlib

import numpy as np
import pytest

import wilco.harness as H
from wilco.harness import run_evidence, run_mission, summarize
from wilco.monitor import Decision
from wilco.planners import MISSION_PLANNERS, propose
from wilco.scenarios import EvidenceCase, Scenario, digest, generate_library, split, to_json
from wilco.schema import parse_json

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB = generate_library()
MISSIONS = [s for s in LIB if isinstance(s, Scenario)]
CASES = [s for s in LIB if isinstance(s, EvidenceCase)]


def fresh(planner):
    return [run_mission(planner, sc, i) for i, sc in enumerate(MISSIONS)]


_CACHE = {}


def results(planner):
    if planner not in _CACHE:
        _CACHE[planner] = fresh(planner)
    return _CACHE[planner]


def test_library_shape_and_determinism():
    assert len(LIB) == 200 and len(MISSIONS) == 130 and len(CASES) == 70
    assert to_json(generate_library()) == to_json(LIB)
    assert len({s.id for s in LIB}) == 200


def test_the_committed_library_and_lock_match_the_generator():
    committed = (ROOT / "eval" / "scenarios.json").read_text().strip()
    assert committed == to_json(LIB)
    dev, test = split(LIB)
    lock = (ROOT / "eval" / "LOCK.txt").read_text()
    assert digest(dev) in lock and digest(test) in lock
    assert len(dev) + len(test) == 200 and abs(len(dev) - len(test)) <= 4


def test_planners_are_deterministic():
    for p in MISSION_PLANNERS:
        assert [propose(p, sc, i) for i, sc in enumerate(MISSIONS[:20])] == [propose(p, sc, i) for i, sc in enumerate(MISSIONS[:20])]


def test_the_monitor_never_approves_an_unsafe_plan_from_any_stub():
    for p in MISSION_PLANNERS:
        assert summarize(results(p))["unsafe_approved"] == 0, p


def test_the_oracle_planner_is_never_unsafe_and_never_vetoed():
    s = summarize(results("oracle"))
    assert s["unsafe"] == 0 and s["vetoed"] == 0 and s["malformed"] == 0


def test_brackets():
    oracle, timid = summarize(results("oracle")), summarize(results("timid"))
    sloppy, adv, rnd = summarize(results("sloppy")), summarize(results("adversarial")), summarize(results("random"))
    assert timid["success_with_monitor"] == 0 and timid["unsafe"] == 0
    assert oracle["success_with_monitor"] > 0.85 * oracle["n"]
    assert oracle["success_with_monitor"] > sloppy["success_with_monitor"] > rnd["success_with_monitor"]
    assert adv["success_with_monitor"] == 0 and adv["approved"] == 0
    assert sloppy["unsafe_executed_without_monitor"] > 0 and rnd["unsafe_executed_without_monitor"] > adv["n"] * 0.3


def test_every_attack_kind_is_caught():
    from wilco.planners import ATTACKS

    seen = set()
    sc = MISSIONS[0]
    for k in range(len(ATTACKS)):
        r = run_mission("adversarial", sc, k)
        assert not r.approved, ATTACKS[k]
        seen.add(ATTACKS[k])
    assert seen == set(ATTACKS)


def test_the_harness_has_teeth_a_monitor_that_approves_everything_is_exposed(monkeypatch):
    monkeypatch.setattr(H, "vet", lambda *a, **k: Decision(()))
    s = summarize(fresh("adversarial"))
    assert s["unsafe_approved"] > 20
    s = summarize(fresh("sloppy"))
    assert s["unsafe_approved"] > 0


def test_a_monitor_that_vetoes_everything_shows_up_as_lost_success(monkeypatch):
    from wilco.monitor import Veto

    monkeypatch.setattr(H, "vet", lambda *a, **k: Decision((Veto("x", "y", "z"),)))
    s = summarize(fresh("oracle"))
    assert s["success_with_monitor"] == 0 and s["safe_vetoed"] == s["n"]


def scenario(**kw):
    base = dict(id="X", kind="search", text="", cfg={"half": 1000.0, "battery_wh": 80.0, "alt_min": 10.0, "alt_max": 120.0}, start_energy_wh=80.0, want="vehicle")
    base.update(kw)
    return Scenario(**base)


def plan(*steps):
    return parse_json(json.dumps({"id": "p", "lost_link": "rtl", "steps": [{"type": "takeoff", "alt": 60}, *steps, {"type": "return"}]}))


def test_coverage_and_detection_match_a_hand_case():
    # 100 x 100 m area, target at its centre; one pass along y = 0 at 60 m (footprint radius 30 m) covers 3/10 rows of cells
    sc = scenario(area=(0.0, 0.0, 100.0, 100.0), targets=((50.0, 20.0, "vehicle"),))
    row = {"type": "search_lawnmower", "x0": 0.0, "y0": 0.0, "width": 100.0, "height": 0.0, "spacing": 5.0, "alt": 60.0}
    ok, ttd, cov = H._outcome(plan(row, {"type": "report", "cls": "vehicle"}), sc)
    assert 0.25 < cov < 0.40  # cells within 30 m of the line: y centres 5, 15, 25 of ten rows
    assert not ok  # coverage below the 0.5 needed
    full = {"type": "search_lawnmower", "x0": 0.0, "y0": 0.0, "width": 100.0, "height": 100.0, "spacing": 40.0, "alt": 60.0}
    ok, ttd, cov = H._outcome(plan(full, {"type": "report", "cls": "vehicle"}), sc)
    assert ok and cov > 0.95 and ttd is not None and ttd > 0
    ok, _, _ = H._outcome(plan(full), sc)  # finds it but never says so
    assert not ok


def test_a_plan_that_does_not_report_a_present_target_fails_but_an_empty_area_needs_no_report():
    empty = scenario(area=(0.0, 0.0, 100.0, 100.0), targets=())
    full = {"type": "search_lawnmower", "x0": 0.0, "y0": 0.0, "width": 100.0, "height": 100.0, "spacing": 40.0, "alt": 60.0}
    assert H._outcome(plan(full), empty)[0]


def test_inspect_and_transit_success_rules():
    ins = scenario(kind="inspect", structure=(300.0, 0.0, 30.0))
    good = {"type": "orbit", "x": 300.0, "y": 0.0, "radius": 60.0, "turns": 2, "alt": 40.0}
    too_close = {**good, "radius": 35.0}
    wrong_place = {**good, "x": 500.0}
    assert H._outcome(plan(good), ins)[0]
    assert not H._outcome(plan(too_close), ins)[0] and not H._outcome(plan(wrong_place), ins)[0]
    tr = scenario(kind="transit", waypoints=((200.0, 0.0), (200.0, 300.0)))
    g = lambda x, y: {"type": "goto", "x": x, "y": y, "alt": 50.0}  # noqa: E731
    assert H._outcome(plan(g(200, 0), g(200, 300)), tr)[0]
    assert not H._outcome(plan(g(200, 300), g(200, 0)), tr)[0]  # wrong order
    assert not H._outcome(plan(g(200, 0)), tr)[0]


def test_unreachable_targets_are_excluded_not_required():
    # target outside the fence: a safe plan cannot detect it, so it must not be required
    sc = scenario(area=(900.0, 0.0, 400.0, 100.0), targets=((1200.0, 50.0, "vehicle"),))
    lim = 1000.0 - 5.0 - 2.0
    assert lim < 1200.0
    row = {"type": "search_lawnmower", "x0": 900.0, "y0": 0.0, "width": 90.0, "height": 100.0, "spacing": 40.0, "alt": 60.0}
    assert H._outcome(plan(row), sc)[0]


def test_evidence_harness_outcomes():
    cfg = MISSIONS[0].envelope()
    by = {}
    for r in ("honest", "fabricator", "forger", "lazy", "misplaced"):
        res = [run_evidence(r, c, i, cfg) for i, c in enumerate(CASES)]
        by[r] = res
        assert not any(x.allowed and not x.legit for x in res if r != "honest"), r
    honest_bad = [x for x in by["honest"] if x.allowed and not x.legit]
    assert honest_bad and all(x.situation == "false_alarm" for x in honest_bad)  # perception errors pass the gate
    assert not any(x.allowed for x in by["fabricator"]) and not any(x.allowed for x in by["forger"])
    assert not any(x.allowed for x in by["lazy"]) and not any(x.allowed for x in by["misplaced"])


def test_summary_counts_are_consistent():
    s = summarize(results("sloppy"))
    assert s["approved"] + s["vetoed"] == s["n"]
    assert s["unsafe"] == s["unsafe_executed_without_monitor"]
    assert sum(k["n"] for k in s["by_kind"].values()) == s["n"]
