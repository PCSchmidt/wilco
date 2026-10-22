"""Mission schema and strict parsing.

A plan is data, not code: a closed set of step types, every field typed and bounded, unknown keys rejected,
non-finite numbers rejected. The planner (a model) can only say things this module can parse; the monitor
then judges what was said.

Deliberately absent: any step that engages or attacks a target. Search, inspection, reporting only.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Union

MAX_STEPS = 64
MAX_TEXT = 64
LOST_LINK_ACTIONS = ("rtl", "land")
COORD_LIMIT = 1.0e6  # metres; anything larger is not a plausible local coordinate


class SchemaError(ValueError):
    """The plan does not parse; the message says where."""


@dataclass(frozen=True)
class Takeoff:
    alt: float


@dataclass(frozen=True)
class Goto:
    x: float
    y: float
    alt: float


@dataclass(frozen=True)
class Lawnmower:
    """Serpentine search of the rectangle [x0, x0+width] x [y0, y0+height], rows parallel to x."""

    x0: float
    y0: float
    width: float
    height: float
    spacing: float
    alt: float


@dataclass(frozen=True)
class Orbit:
    x: float
    y: float
    radius: float
    turns: float
    alt: float


@dataclass(frozen=True)
class Report:
    """A standing instruction: report any detection of `cls`. It carries no claim and no evidence; the
    executor produces the actual report at run time and the monitor gates it then."""

    cls: str


@dataclass(frozen=True)
class Return:
    """Fly home at the current altitude and land."""


Step = Union[Takeoff, Goto, Lawnmower, Orbit, Report, Return]


@dataclass(frozen=True)
class Mission:
    id: str
    lost_link: str
    steps: tuple[Step, ...]


# Run-time actions (produced by the executor, not by the planner).
@dataclass(frozen=True)
class ReportAction:
    cls: str
    x: float
    y: float
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class TrackAction:
    """Fly to the position of a detection, at the current altitude, to keep it in view."""

    evidence_id: str


def _num(d: dict, key: str, lo: float = -COORD_LIMIT, hi: float = COORD_LIMIT, where: str = "") -> float:
    if key not in d:
        raise SchemaError(f"{where}missing field '{key}'")
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SchemaError(f"{where}field '{key}' must be a number")
    v = float(v)
    if not math.isfinite(v):
        raise SchemaError(f"{where}field '{key}' must be finite")
    if not lo <= v <= hi:
        raise SchemaError(f"{where}field '{key}' = {v} outside [{lo}, {hi}]")
    return v


def _text(d: dict, key: str, where: str = "") -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v or len(v) > MAX_TEXT or not v.isprintable():
        raise SchemaError(f"{where}field '{key}' must be a short printable string")
    return v


def _only(d: dict, allowed: set[str], where: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise SchemaError(f"{where}unknown field(s) {sorted(extra)}")


def parse_step(d: object, i: int) -> Step:
    where = f"step {i}: "
    if not isinstance(d, dict):
        raise SchemaError(f"{where}must be an object")
    t = d.get("type")
    if t == "takeoff":
        _only(d, {"type", "alt"}, where)
        return Takeoff(_num(d, "alt", 0.0, 1.0e4, where))
    if t == "goto":
        _only(d, {"type", "x", "y", "alt"}, where)
        return Goto(_num(d, "x", where=where), _num(d, "y", where=where), _num(d, "alt", 0.0, 1.0e4, where))
    if t == "search_lawnmower":
        _only(d, {"type", "x0", "y0", "width", "height", "spacing", "alt"}, where)
        return Lawnmower(
            _num(d, "x0", where=where),
            _num(d, "y0", where=where),
            _num(d, "width", 0.0, COORD_LIMIT, where),
            _num(d, "height", 0.0, COORD_LIMIT, where),
            _num(d, "spacing", 0.0, COORD_LIMIT, where),
            _num(d, "alt", 0.0, 1.0e4, where),
        )
    if t == "orbit":
        _only(d, {"type", "x", "y", "radius", "turns", "alt"}, where)
        return Orbit(
            _num(d, "x", where=where),
            _num(d, "y", where=where),
            _num(d, "radius", 0.0, COORD_LIMIT, where),
            _num(d, "turns", 0.0, 100.0, where),
            _num(d, "alt", 0.0, 1.0e4, where),
        )
    if t == "report":
        _only(d, {"type", "cls"}, where)
        return Report(_text(d, "cls", where))
    if t == "return":
        _only(d, {"type"}, where)
        return Return()
    raise SchemaError(f"{where}unknown step type {t!r}")


def parse_mission(obj: object) -> Mission:
    if not isinstance(obj, dict):
        raise SchemaError("mission must be an object")
    _only(obj, {"id", "lost_link", "steps"}, "")
    mid = _text(obj, "id")
    ll = obj.get("lost_link")
    if ll not in LOST_LINK_ACTIONS:
        raise SchemaError(f"lost_link must be one of {LOST_LINK_ACTIONS}")
    steps = obj.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise SchemaError(f"steps must be a list of 1 to {MAX_STEPS} steps")
    return Mission(mid, ll, tuple(parse_step(s, i) for i, s in enumerate(steps)))


def parse_json(text: str) -> Mission:
    try:
        obj = json.loads(
            text,
            parse_constant=lambda c: (_ for _ in ()).throw(SchemaError(f"non-finite constant {c}")),
        )
    except SchemaError:
        raise
    except (ValueError, RecursionError) as e:
        raise SchemaError(f"not valid JSON: {e}") from e
    return parse_mission(obj)
