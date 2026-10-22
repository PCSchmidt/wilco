"""The executor runs inside the simulator container, but its logic that does not need MAVLink is tested here by
importing it with a stub for pymavlink."""

import importlib.util
import json
import math
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ex():
    stub = types.ModuleType("pymavlink")
    stub.mavutil = types.SimpleNamespace(mavlink=types.SimpleNamespace(MAV_CMD_DO_SET_MODE=176, MAV_CMD_COMPONENT_ARM_DISARM=400, MAV_CMD_NAV_LAND=21, MAV_PARAM_TYPE_REAL32=9))
    sys.modules["pymavlink"] = stub
    spec = importlib.util.spec_from_file_location("executor", ROOT / "sim" / "executor.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["executor"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("executor", None)
    sys.modules.pop("pymavlink", None)


def mission(*steps):
    from wilco.schema import parse_json

    return parse_json(json.dumps({"id": "m", "lost_link": "rtl", "steps": [{"type": "takeoff", "alt": 40}, *steps, {"type": "return"}]}))


def test_waypoint_expansion(ex):
    m = mission(
        {"type": "goto", "x": 100, "y": 50, "alt": 60},
        {"type": "search_lawnmower", "x0": 0, "y0": 0, "width": 200, "height": 100, "spacing": 50, "alt": 70},
        {"type": "orbit", "x": 300, "y": 0, "radius": 100, "turns": 1, "alt": 50},
        {"type": "report", "cls": "vehicle"},
    )
    wp = ex.waypoints(m, (0.0, 0.0), 0.0)
    assert wp[0] == (0, 0.0, 0.0, 40.0)  # takeoff straight up from home
    assert wp[1] == (1, 100, 50, 60)
    rows = [w for w in wp if w[0] == 2]
    assert len(rows) == 2 * 3 and rows[0][1:3] == (0, 0) and rows[1][1:3] == (200, 0) and rows[2][1:3] == (200, 50)  # serpentine
    orbit = [w for w in wp if w[0] == 3]
    assert len(orbit) == 61 and all(abs(math.hypot(w[1] - 300, w[2]) - 100) < 1e-9 for w in orbit)
    assert not any(w[0] == 4 for w in wp)  # a report step moves nothing
    assert wp[-1] == (5, 0.0, 0.0, 50)  # return at the last altitude, then land


class FakeLink:
    """Just enough of Link for fly_to: a vehicle that sits exactly on the last setpoint it was given."""

    def __init__(self, ex):
        self.t = 0.0
        self.pos = (0.0, 0.0, -40.0)  # north, east, down
        self.sent = []
        self.ex = ex

    def setpoint(self, east, north, alt):
        self.sent.append((east, north, alt))
        self.pos = (north, east, -alt)

    def drain(self):
        self.t += 0.05

    def here(self):
        n, e, d = self.pos
        return e, n, -d


def test_carrot_moves_at_the_set_speed_and_never_past_the_target(ex, monkeypatch):
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    link = FakeLink(ex)
    assert ex.fly_to(link, 100.0, 0.0, 40.0, budget_s=200.0, carrot_speed=10.0)
    xs = [p[0] for p in link.sent]
    assert max(xs) <= 100.0 + 1e-9
    steps = [b - a for a, b in zip(xs, xs[1:])]
    assert max(steps) <= 10.0 * 0.05 + 1e-9  # never faster than the carrot speed per tick
    assert link.t >= 9.0  # 100 m at 10 m/s takes about 10 s of simulated time


def test_without_a_carrot_the_setpoint_is_the_target(ex, monkeypatch):
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    link = FakeLink(ex)
    assert ex.fly_to(link, 100.0, 0.0, 40.0, budget_s=5.0)
    assert link.sent[0] == (100.0, 0.0, 40.0)


def test_a_budget_that_runs_out_reports_failure(ex, monkeypatch):
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)

    class Stuck(FakeLink):
        def setpoint(self, east, north, alt):
            self.sent.append((east, north, alt))

    assert not ex.fly_to(Stuck(ex), 500.0, 0.0, 40.0, budget_s=2.0)


def test_a_vetoed_plan_is_refused_before_any_connection(ex, tmp_path):
    from wilco.scenarios import generate_library, Scenario
    import dataclasses

    sc = next(s for s in generate_library() if isinstance(s, Scenario))
    scen, plan, out = tmp_path / "s.json", tmp_path / "p.json", tmp_path / "o.json"
    scen.write_text(json.dumps(dataclasses.asdict(sc)))
    plan.write_text(json.dumps({"id": "x", "lost_link": "rtl", "steps": [{"type": "takeoff", "alt": 40}, {"type": "goto", "x": 99999, "y": 0, "alt": 40}, {"type": "return"}]}))
    ex.Link = lambda: pytest.fail("the executor connected to the vehicle for a vetoed plan")
    ex.main(str(plan), str(scen), str(out))
    res = json.loads(out.read_text())
    assert res["approved"] is False and res["flown"] is False and "geofence" in res["vetoes"]
