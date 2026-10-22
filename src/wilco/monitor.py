"""Deterministic safety monitor. It sees a plan (or a run-time action) and the world model; the planner does not
get to configure it. Every veto names the rule, the step, the observed value and the limit.

Rules (see docs/monitor.md for the exact definitions and what they do NOT cover):

  schema      the plan did not parse (raised by schema.py, reported here)
  sequence    one takeoff first, one return last, nothing after the return
  bad_param   a step parameter is outside what the vehicle config allows
  geofence    every leg (and orbit circle) stays inside the fence with a margin
  altitude    every waypoint altitude is inside [alt_min, alt_max]
  battery     at every waypoint, remaining energy minus the cost of flying straight home is at least the reserve
  duration    estimated flight time is within the limit
  lost_link   the lost-link action is allowed and its return path is inside the fence
  evidence    a report or a track action must cite trusted, fresh, nearby, confident detections

The monitor fails closed: any exception while checking is a veto, never an approval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

from . import geometry as geo
from .schema import (
    Goto,
    Lawnmower,
    LOST_LINK_ACTIONS,
    Mission,
    Orbit,
    Report,
    ReportAction,
    Return,
    SchemaError,
    Takeoff,
    TrackAction,
    parse_json,
    parse_mission,
)


@dataclass(frozen=True)
class EvidencePolicy:
    min_confidence: float = 0.6
    max_age_s: float = 30.0
    max_position_error_m: float = 25.0
    trusted_sources: tuple[str, ...] = ("onboard_camera",)


@dataclass(frozen=True)
class Envelope:
    """Vehicle and area limits. Energy numbers are modelling assumptions, not measurements of a real vehicle."""

    fence: tuple[geo.Point, ...]
    fence_margin: float = 5.0
    home: geo.Point = (0.0, 0.0)
    alt_min: float = 10.0
    alt_max: float = 120.0
    speed: float = 12.0
    max_duration_s: float = 1200.0
    battery_wh: float = 80.0
    reserve_frac: float = 0.25
    k_h: float = 0.004  # Wh per metre flown horizontally
    k_up: float = 0.002  # extra Wh per metre climbed
    k_down: float = 0.0002  # Wh per metre descended
    safety_factor: float = 1.2  # multiplies every energy estimate (wind, ageing battery)
    orbit_min_radius: float = 15.0
    min_spacing: float = 5.0
    max_rows: int = 200
    evidence: EvidencePolicy = field(default_factory=EvidencePolicy)

    def __post_init__(self) -> None:
        poly = list(self.fence)
        if not geo.is_convex_ccw(poly) or not geo.is_simple(poly):
            raise ValueError("fence must be a convex polygon with counter-clockwise vertices")
        if self.fence_margin <= 0:
            raise ValueError("fence_margin must be positive (it also keeps the geometry tests exact)")
        if not geo.point_ok(self.home, poly, self.fence_margin):
            raise ValueError("home must be inside the fence with its margin")
        if not 0 < self.alt_min < self.alt_max:
            raise ValueError("need 0 < alt_min < alt_max")
        for name in ("speed", "max_duration_s", "battery_wh", "k_h", "k_up", "k_down", "orbit_min_radius", "min_spacing"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")
        if not 0 <= self.reserve_frac < 1 or self.safety_factor < 1:
            raise ValueError("need 0 <= reserve_frac < 1 and safety_factor >= 1")

    @property
    def reserve_wh(self) -> float:
        return self.reserve_frac * self.battery_wh


@dataclass(frozen=True)
class Veto:
    rule: str
    code: str
    message: str
    step: int | None = None
    observed: float | None = None
    limit: float | None = None


@dataclass(frozen=True)
class Decision:
    vetoes: tuple[Veto, ...]

    @property
    def approved(self) -> bool:
        return not self.vetoes

    @property
    def rules(self) -> frozenset[str]:
        return frozenset(v.rule for v in self.vetoes)


@dataclass(frozen=True)
class State:
    x: float
    y: float
    alt: float
    energy_wh: float


@dataclass(frozen=True)
class Evidence:
    id: str
    cls: str
    confidence: float
    x: float
    y: float
    t: float
    source: str


@dataclass(frozen=True)
class Context:
    state: State
    evidence: Mapping[str, Evidence]
    now: float


Pos3 = tuple[float, float, float]


class _Trace:
    """Walks a path leg by leg, accumulating energy and distance and recording vetoes."""

    def __init__(self, cfg: Envelope, start: Pos3, energy0: float) -> None:
        self.cfg = cfg
        self.pos = start
        self.energy0 = energy0
        self.used = 0.0
        self.length = 0.0
        self.vetoes: list[Veto] = []
        self.fence = list(cfg.fence)
        self.last_step = -1

    def veto(self, rule: str, code: str, msg: str, step: int | None, obs: float | None = None, lim: float | None = None) -> None:
        self.vetoes.append(Veto(rule, code, msg, step, obs, lim))

    # energy ------------------------------------------------------------------------------------------------
    def leg_energy(self, a: Pos3, b: Pos3) -> float:
        c = self.cfg
        dz = b[2] - a[2]
        h = math.hypot(b[0] - a[0], b[1] - a[1])
        return c.safety_factor * (c.k_h * h + c.k_up * max(dz, 0.0) + c.k_down * max(-dz, 0.0))

    def return_energy(self, p: Pos3) -> float:
        c = self.cfg
        return c.safety_factor * (c.k_h * math.hypot(p[0] - c.home[0], p[1] - c.home[1]) + c.k_down * p[2])

    def check_battery(self, p: Pos3, step: int, extra_return: float = 0.0) -> None:
        remaining = self.energy0 - self.used
        margin = remaining - self.return_energy(p) - extra_return - self.cfg.reserve_wh
        if margin < 0:
            self.veto(
                "battery",
                "reserve",
                f"after step {step} the vehicle could not fly home and keep the reserve",
                step,
                remaining - self.return_energy(p) - extra_return,
                self.cfg.reserve_wh,
            )

    def check_alt(self, z: float, step: int) -> None:
        c = self.cfg
        if not c.alt_min <= z <= c.alt_max:
            self.veto("altitude", "band", f"altitude {z:.1f} m outside [{c.alt_min}, {c.alt_max}]", step, z, c.alt_max if z > c.alt_max else c.alt_min)

    # motion ------------------------------------------------------------------------------------------------
    def fly_to(self, b: Pos3, step: int, check_alt: bool = True, check_fence: bool = True, check_bat: bool = True) -> None:
        a = self.pos
        if check_fence and not geo.segment_ok((a[0], a[1]), (b[0], b[1]), self.fence, self.cfg.fence_margin):
            self.veto("geofence", "leg", f"leg to ({b[0]:.0f}, {b[1]:.0f}) leaves the fence or comes within {self.cfg.fence_margin} m of it", step)
        if check_alt:
            self.check_alt(b[2], step)
        self.used += self.leg_energy(a, b)
        self.length += math.dist(a, b)
        self.pos = b
        if check_bat:
            self.check_battery(b, step)
        self.last_step = step


def _finite(*vals: float) -> bool:
    return all(math.isfinite(v) for v in vals)


def _lawnmower_points(s: Lawnmower, rows: int) -> list[geo.Point]:
    pts: list[geo.Point] = []
    for i in range(rows):
        y = min(s.y0 + i * s.spacing, s.y0 + s.height)
        xs = (s.x0, s.x0 + s.width) if i % 2 == 0 else (s.x0 + s.width, s.x0)
        pts += [(xs[0], y), (xs[1], y)]
    return pts


def check_mission(mission: Mission, cfg: Envelope, state: State | None = None) -> Decision:
    """Static check of a whole plan against the envelope, from `state` (default: on the ground at home, full)."""
    home3: Pos3 = (cfg.home[0], cfg.home[1], 0.0)
    st = state or State(cfg.home[0], cfg.home[1], 0.0, cfg.battery_wh)
    tr = _Trace(cfg, (st.x, st.y, st.alt), st.energy_wh)
    steps = mission.steps

    # sequence
    if not isinstance(steps[0], Takeoff):
        tr.veto("sequence", "first", "the first step must be a takeoff", 0)
    if sum(isinstance(s, Takeoff) for s in steps) != 1:
        tr.veto("sequence", "takeoff_count", "exactly one takeoff is required", None)
    if not isinstance(steps[-1], Return):
        tr.veto("sequence", "last", "the last step must be a return", len(steps) - 1)
    for i, s in enumerate(steps[:-1]):
        if isinstance(s, Return):
            tr.veto("sequence", "after_return", "no step may follow a return", i)
    if mission.lost_link not in LOST_LINK_ACTIONS:
        tr.veto("lost_link", "action", f"lost-link action {mission.lost_link!r} is not allowed", None)
    if tr.energy0 - tr.return_energy(tr.pos) < cfg.reserve_wh:
        tr.veto("battery", "start", "the vehicle starts below its reserve", None)

    for i, s in enumerate(steps):
        if isinstance(s, Takeoff):
            tr.fly_to((cfg.home[0], cfg.home[1], s.alt), i)
        elif isinstance(s, Goto):
            tr.fly_to((s.x, s.y, s.alt), i)
        elif isinstance(s, Lawnmower):
            rows = int(math.ceil(s.height / s.spacing)) + 1 if s.spacing > 0 else 0
            if s.spacing < cfg.min_spacing or s.width <= 0 or rows > cfg.max_rows or rows < 1:
                tr.veto("bad_param", "lawnmower", f"search spacing {s.spacing} m / width {s.width} m / {rows} rows is not allowed", i, s.spacing, cfg.min_spacing)
                continue
            for x, y in _lawnmower_points(s, rows):
                tr.fly_to((x, y, s.alt), i)
        elif isinstance(s, Orbit):
            if s.radius < cfg.orbit_min_radius or s.turns <= 0:
                tr.veto("bad_param", "orbit", f"orbit radius {s.radius} m / turns {s.turns} is not allowed", i, s.radius, cfg.orbit_min_radius)
                continue
            if not geo.circle_ok((s.x, s.y), s.radius, list(cfg.fence), cfg.fence_margin):
                tr.veto("geofence", "orbit", "the orbit circle leaves the fence or comes too close to it", i)
            entry: Pos3 = (s.x + s.radius, s.y, s.alt)
            tr.fly_to(entry, i, check_fence=False)
            arc = 2.0 * math.pi * s.radius * s.turns
            tr.used += tr.cfg.safety_factor * cfg.k_h * arc
            tr.length += arc
            ang = 2.0 * math.pi * s.turns
            tr.pos = (s.x + s.radius * math.cos(ang), s.y + s.radius * math.sin(ang), s.alt)
            # Energy used plus the cost of flying straight home never decreases along a path (a step of length d
            # changes the return cost by at most k_h * d but uses at least k_h * d), so the tightest point of the
            # orbit is its end and checking there is exact.
            tr.check_battery(tr.pos, i)
            tr.last_step = i
        elif isinstance(s, Report):
            pass  # a standing instruction; the claim is gated at run time
        elif isinstance(s, Return):
            tr.fly_to((cfg.home[0], cfg.home[1], tr.pos[2]), i, check_alt=False)
            tr.fly_to(home3, i, check_alt=False, check_fence=False, check_bat=False)

    if tr.length / cfg.speed > cfg.max_duration_s:
        tr.veto("duration", "limit", f"estimated flight time {tr.length / cfg.speed:.0f} s exceeds {cfg.max_duration_s:.0f} s", None, tr.length / cfg.speed, cfg.max_duration_s)
    if tr.energy0 - tr.used < cfg.reserve_wh:
        tr.veto("battery", "final", "the plan ends below the reserve", None, tr.energy0 - tr.used, cfg.reserve_wh)
    return Decision(tuple(tr.vetoes))


def _evidence_vetoes(cls: str, claim: geo.Point | None, ids: tuple[str, ...], ctx: Context, cfg: Envelope) -> list[Veto]:
    p = cfg.evidence
    out: list[Veto] = []
    if not ids:
        return [Veto("evidence", "none", "no evidence cited")]
    for eid in ids:
        e = ctx.evidence.get(eid)
        if e is None:
            out.append(Veto("evidence", "unknown", f"evidence '{eid}' is not in the sensor log"))
            continue
        if e.source not in p.trusted_sources:
            out.append(Veto("evidence", "untrusted_source", f"evidence '{eid}' comes from '{e.source}'"))
        if e.cls != cls:
            out.append(Veto("evidence", "class_mismatch", f"evidence '{eid}' is '{e.cls}', not '{cls}'"))
        if not e.confidence >= p.min_confidence:
            out.append(Veto("evidence", "low_confidence", f"evidence '{eid}' confidence {e.confidence:.2f}", None, e.confidence, p.min_confidence))
        age = ctx.now - e.t
        if not 0 <= age <= p.max_age_s:
            out.append(Veto("evidence", "stale", f"evidence '{eid}' is {age:.0f} s old", None, age, p.max_age_s))
        if claim is not None:
            err = math.hypot(e.x - claim[0], e.y - claim[1])
            if not err <= p.max_position_error_m:
                out.append(Veto("evidence", "position_mismatch", f"evidence '{eid}' is {err:.0f} m from the claimed position", None, err, p.max_position_error_m))
    return out


def check_action(action: ReportAction | TrackAction, ctx: Context, cfg: Envelope) -> Decision:
    """Run-time gate for an action the executor wants to take now, given the vehicle state and sensor log."""
    s = ctx.state
    if not _finite(s.x, s.y, s.alt, s.energy_wh, ctx.now):
        return Decision((Veto("schema", "state", "vehicle state is not finite"),))
    if isinstance(action, ReportAction):
        if not _finite(action.x, action.y):
            return Decision((Veto("schema", "claim", "claimed position is not finite"),))
        return Decision(tuple(_evidence_vetoes(action.cls, (action.x, action.y), action.evidence_ids, ctx, cfg)))
    if isinstance(action, TrackAction):
        e = ctx.evidence.get(action.evidence_id)
        if e is None:
            return Decision((Veto("evidence", "unknown", f"evidence '{action.evidence_id}' is not in the sensor log"),))
        vetoes = _evidence_vetoes(e.cls, (e.x, e.y), (action.evidence_id,), ctx, cfg)
        tr = _Trace(cfg, (s.x, s.y, s.alt), s.energy_wh)
        tr.check_alt(s.alt, 0)
        tr.check_battery((s.x, s.y, s.alt), 0)
        tr.fly_to((e.x, e.y, s.alt), 0)
        return Decision(tuple(vetoes + tr.vetoes))
    raise TypeError("unknown action")


def vet(plan: object, cfg: Envelope, state: State | None = None) -> Decision:
    """Parse (if text or a dict) and check a plan. Never raises; any failure is a veto."""
    try:
        if isinstance(plan, Mission):
            mission = plan
        elif isinstance(plan, str):
            mission = parse_json(plan)
        else:
            mission = parse_mission(plan)
        return check_mission(mission, cfg, state)
    except SchemaError as e:
        return Decision((Veto("schema", "parse", str(e)),))
    except Exception as e:  # fail closed
        return Decision((Veto("schema", "internal", f"monitor error, plan rejected: {type(e).__name__}: {e}"),))
