"""Executor: flies a vetted mission in PX4 SITL over MAVLink (offboard position control) and logs the trajectory.

Runs inside the simulator container:  python3 executor.py <plan.json> <scenario.json> <out.json>

Mission frame: x = east, y = north, altitude above home (metres); PX4 local NED is (north, east, down).

The executor asks the monitor before it does anything and refuses a plan the monitor vetoes. It does not decide
anything itself: it turns steps into setpoints and records where the vehicle actually went.
"""

import json
import math
import os
import sys
import time

sys.path.insert(0, "/wilco/src")
from pymavlink import mavutil  # noqa: E402

from wilco.monitor import State, vet  # noqa: E402
from wilco.scenarios import Scenario  # noqa: E402
from wilco.schema import Goto, Lawnmower, Orbit, Report, Return, Takeoff, parse_json  # noqa: E402

TOL = 3.0  # metres to count a waypoint as reached
ORBIT_STEP_DEG = 6.0
POSITION_MASK = 0b110111111000  # position only


class Link:
    def __init__(self) -> None:
        self.m = mavutil.mavlink_connection("udpin:0.0.0.0:14540")
        self.m.wait_heartbeat(timeout=120)
        self.pos = None  # (north, east, down)
        self.t = 0.0  # simulated seconds (time_boot_ms of the latest position message)
        self.log: list[tuple[float, float, float, float]] = []  # (t, x_east, y_north, alt)

    def drain(self) -> None:
        while True:
            msg = self.m.recv_match(type="LOCAL_POSITION_NED", blocking=False)
            if msg is None:
                return
            self.pos = (msg.x, msg.y, msg.z)
            self.t = msg.time_boot_ms / 1000.0
            self.log.append((self.t, msg.y, msg.x, -msg.z))

    def setpoint(self, east: float, north: float, alt: float) -> None:
        self.m.mav.set_position_target_local_ned_send(0, self.m.target_system, self.m.target_component, 1, POSITION_MASK, north, east, -alt, 0, 0, 0, 0, 0, 0, 0, 0)

    def cmd(self, command: int, *p: float) -> None:
        self.m.mav.command_long_send(self.m.target_system, self.m.target_component, command, 0, *(list(p) + [0] * (7 - len(p))))

    def param(self, name: str, value: float) -> None:
        self.m.mav.param_set_send(self.m.target_system, self.m.target_component, name.encode(), value, mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

    def here(self) -> tuple[float, float, float]:
        n, e, d = self.pos
        return e, n, -d


def waypoints(mission, home, here_alt: float):
    """Expand steps into (step_index, east, north, alt) setpoints. Independent of the monitor's own expansion."""
    wp = []
    alt = here_alt
    for i, s in enumerate(mission.steps):
        if isinstance(s, Takeoff):
            alt = s.alt
            wp.append((i, home[0], home[1], alt))
        elif isinstance(s, Goto):
            alt = s.alt
            wp.append((i, s.x, s.y, alt))
        elif isinstance(s, Lawnmower):
            alt = s.alt
            rows = int(math.ceil(s.height / s.spacing)) + 1
            for r in range(rows):
                y = min(s.y0 + r * s.spacing, s.y0 + s.height)
                xs = (s.x0, s.x0 + s.width) if r % 2 == 0 else (s.x0 + s.width, s.x0)
                wp += [(i, xs[0], y, alt), (i, xs[1], y, alt)]
        elif isinstance(s, Orbit):
            alt = s.alt
            n = max(1, int(round(360.0 * s.turns / ORBIT_STEP_DEG)))
            for k in range(n + 1):
                a = math.radians(360.0 * s.turns * k / n)
                wp.append((i, s.x + s.radius * math.cos(a), s.y + s.radius * math.sin(a), alt))
        elif isinstance(s, Report):
            pass
        elif isinstance(s, Return):
            wp.append((i, home[0], home[1], alt))
    return wp


def fly_to(link: Link, east: float, north: float, alt: float, budget_s: float, tol: float = TOL, carrot_speed: float = 0.0) -> bool:
    """Fly to a point. With carrot_speed > 0 the setpoint is not the target but a point that moves toward it at
    that speed, so the vehicle follows a rate-limited path instead of chasing a step."""
    t_start = link.t
    carrot = link.here() if link.pos else (east, north, alt)
    last_t = link.t
    while True:
        if carrot_speed > 0:
            step = carrot_speed * max(link.t - last_t, 0.0)
            vec = (east - carrot[0], north - carrot[1], alt - carrot[2])
            dist = math.hypot(*vec)
            carrot = (east, north, alt) if dist <= step else tuple(c + v * step / dist for c, v in zip(carrot, vec))
            last_t = link.t
            link.setpoint(*carrot)
        else:
            link.setpoint(east, north, alt)
        link.drain()
        if link.pos is not None:
            e, n, a = link.here()
            if math.dist((e, n, a), (east, north, alt)) <= tol:
                return True
        if link.t - t_start > budget_s:
            return False
        time.sleep(0.02)


def main(plan_path: str, scenario_path: str, out_path: str) -> None:
    sc_raw = json.load(open(scenario_path))
    sc_raw = {k: (tuple(v) if isinstance(v, list) else v) for k, v in sc_raw.items()}
    sc_raw["targets"] = tuple(tuple(t) for t in sc_raw["targets"])
    sc_raw["waypoints"] = tuple(tuple(w) for w in sc_raw["waypoints"])
    sc = Scenario(**sc_raw)
    cfg = sc.envelope()
    text = open(plan_path).read()
    decision = vet(text, cfg, State(0.0, 0.0, 0.0, sc.start_energy_wh))
    result = {"scenario": sc.id, "approved": decision.approved, "vetoes": [v.rule for v in decision.vetoes], "flown": False}
    if not decision.approved:
        json.dump(result, open(out_path, "w"))
        return
    mission = parse_json(text)
    link = Link()
    link.drain()
    for _ in range(100):  # wait for a position estimate
        link.drain()
        if link.pos is not None:
            break
        time.sleep(0.1)
    home = (0.0, 0.0)
    link.param("COM_RCL_EXCEPT", 4)  # no RC link is attached in simulation
    link.param("NAV_DLL_ACT", 0)
    link.param("MPC_XY_VEL_MAX", float(os.environ.get("EXEC_SPEED", cfg.speed)))  # EXEC_SPEED overrides, for speed sweeps
    time.sleep(1.0)
    for _ in range(40):  # offboard needs a setpoint stream before the mode switch
        link.setpoint(0.0, 0.0, 0.0)
        link.drain()
        time.sleep(0.02)
    link.cmd(mavutil.mavlink.MAV_CMD_DO_SET_MODE, 1, 6)  # custom mode OFFBOARD
    for _ in range(25):
        link.setpoint(0.0, 0.0, 0.0)
        time.sleep(0.02)
    link.cmd(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 1)
    t0 = None
    ok = True
    reached = []
    for (i, e, n, a) in waypoints(mission, home, 0.0):
        if t0 is None:
            t0 = link.t
        # generous per-waypoint budget: twice the straight-line time at the monitor's assumed speed, plus slack
        cur = link.here() if link.pos else (0.0, 0.0, 0.0)
        v = float(os.environ.get("EXEC_SPEED", cfg.speed))
        carrot = v if os.environ.get("EXEC_CARROT") else 0.0
        budget = 2.5 * math.dist(cur, (e, n, a)) / v + 30.0
        r = fly_to(link, e, n, a, budget, carrot_speed=carrot)
        reached.append(r)
        if not r:
            ok = False
            break
    # land at home
    if ok:
        cur = link.here()
        fly_to(link, home[0], home[1], 3.0, 2.0 * cur[2] / 1.5 + 30.0, tol=1.5)
    link.cmd(mavutil.mavlink.MAV_CMD_NAV_LAND)
    t_land = link.t
    while link.t - t_land < 40.0:
        link.drain()
        if link.pos is not None and -link.pos[2] < 0.3:
            break
        time.sleep(0.05)
    link.drain()
    result.update(
        flown=True,
        completed=bool(ok),
        waypoints_reached=sum(reached),
        waypoints_total=len(reached),
        t_start=t0,
        t_end=link.t,
        log=link.log,
    )
    json.dump(result, open(out_path, "w"))


if __name__ == "__main__":
    main(*sys.argv[1:4])
