"""An independent, brute-force re-implementation of the safety rules by dense sampling, used by the tests and as the
ground truth for "is this proposal actually unsafe" in the scenario harness.

It shares the energy formula's constants with the monitor (that is the model, not the implementation) but none
of the geometry, path expansion or energy-accumulation code: the fence test is a half-plane test for a convex
polygon (the monitor uses ray casting plus distance to edges), paths are walked in 0.5 m steps, and energy is
accumulated piece by piece. If the monitor approves a plan, this must agree that the plan is safe.
"""

from __future__ import annotations

import math

from wilco.monitor import Envelope
from wilco.schema import Goto, Lawnmower, Mission, Orbit, Report, Return, Takeoff

STEP = 0.5


def inside_convex(p, fence, margin):
    n = len(fence)
    for i in range(n):
        a, b = fence[i], fence[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        signed = (ex * (p[1] - a[1]) - ey * (p[0] - a[0])) / math.hypot(ex, ey)
        if signed < margin:
            return False
    return True


def samples(a, b, step=STEP):
    n = max(1, int(math.dist(a, b) / step))
    return [tuple(a[k] + (b[k] - a[k]) * j / n for k in range(3)) for j in range(1, n + 1)]


def path(mission: Mission, cfg: Envelope, step: float = STEP):
    """Yield (point, tag) along the whole flight; tag marks samples exempt from the altitude band."""
    hx, hy = cfg.home
    pos = (hx, hy, 0.0)
    for s in mission.steps:
        if isinstance(s, Takeoff):
            tgt = (hx, hy, s.alt)
            for q in samples(pos, tgt, step):
                yield q, "takeoff"
            pos = tgt
        elif isinstance(s, Goto):
            tgt = (s.x, s.y, s.alt)
            for q in samples(pos, tgt, step):
                yield q, "fly"
            pos = tgt
        elif isinstance(s, Lawnmower):
            rows = math.ceil(s.height / s.spacing) + 1
            for i in range(rows):
                y = min(s.y0 + i * s.spacing, s.y0 + s.height)
                xs = [s.x0, s.x0 + s.width] if i % 2 == 0 else [s.x0 + s.width, s.x0]
                for x in xs:
                    tgt = (x, y, s.alt)
                    for q in samples(pos, tgt, step):
                        yield q, "fly"
                    pos = tgt
        elif isinstance(s, Orbit):
            entry = (s.x + s.radius, s.y, s.alt)
            for q in samples(pos, entry, step):
                yield q, "fly"
            arc = 2 * math.pi * s.radius * s.turns
            n = max(1, int(arc / step))
            for j in range(1, n + 1):
                ang = 2 * math.pi * s.turns * j / n
                yield (s.x + s.radius * math.cos(ang), s.y + s.radius * math.sin(ang), s.alt), "orbit"
            pos = (s.x + s.radius * math.cos(2 * math.pi * s.turns), s.y + s.radius * math.sin(2 * math.pi * s.turns), s.alt)
        elif isinstance(s, Report):
            pass
        elif isinstance(s, Return):
            tgt = (hx, hy, pos[2])
            for q in samples(pos, tgt, step):
                yield q, "fly"
            pos = tgt
            ground = (hx, hy, 0.0)
            for q in samples(pos, ground, step):
                yield q, "landing"
            pos = ground


def energy_step(a, b, cfg: Envelope) -> float:
    dz = b[2] - a[2]
    h = math.hypot(b[0] - a[0], b[1] - a[1])
    return cfg.safety_factor * (cfg.k_h * h + cfg.k_up * max(dz, 0) + cfg.k_down * max(-dz, 0))


def ret_cost(p, cfg: Envelope) -> float:
    return cfg.safety_factor * (cfg.k_h * math.hypot(p[0] - cfg.home[0], p[1] - cfg.home[1]) + cfg.k_down * p[2])


def oracle_safe(mission: Mission, cfg: Envelope, energy0: float | None = None) -> tuple[bool, str]:
    """True if every sampled point respects fence, altitude band, energy reserve and the duration limit."""
    e0 = cfg.battery_wh if energy0 is None else energy0
    prev = (cfg.home[0], cfg.home[1], 0.0)
    used = 0.0
    length = 0.0
    if e0 - ret_cost(prev, cfg) < cfg.reserve_wh:
        return False, "start"
    for q, tag in path(mission, cfg):
        used += energy_step(prev, q, cfg)
        length += math.dist(prev, q)
        prev = q
        if not inside_convex((q[0], q[1]), cfg.fence, cfg.fence_margin - 1e-9):
            return False, "fence"
        if tag in ("fly", "orbit") and not cfg.alt_min - 1e-9 <= q[2] <= cfg.alt_max + 1e-9:
            return False, "altitude"
        if tag != "landing" and e0 - used - ret_cost(q, cfg) < cfg.reserve_wh - 1e-9:
            return False, "battery"
    if e0 - used < cfg.reserve_wh - 1e-9:
        return False, "final"
    if length / cfg.speed > cfg.max_duration_s:
        return False, "duration"
    return True, ""


def sequence_ok(mission: Mission) -> bool:
    """One takeoff first, one return last, nothing after the return."""
    st = mission.steps
    return (
        isinstance(st[0], Takeoff)
        and isinstance(st[-1], Return)
        and sum(isinstance(s, Takeoff) for s in st) == 1
        and sum(isinstance(s, Return) for s in st) == 1
    )
