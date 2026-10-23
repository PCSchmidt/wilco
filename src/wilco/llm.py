"""A language-model mission planner served by a local llama.cpp server, with schema-constrained output.

The model sees the request (and, in some conditions, the vehicle's limits), and must answer with a mission that
parses against a fixed JSON schema (llama.cpp turns the schema into a grammar, so the output is syntactically
valid by construction; whether it is *safe* is the monitor's job). Optionally a vetoed plan is sent back with the
monitor's reasons for a limited number of corrections.

This is a text-only planner: the scenario library has no camera frames, so nothing here uses an image.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .monitor import Envelope, State, vet
from .scenarios import Scenario

PROMPT_VERSION = 3  # 2: compact, numeric feedback (v1 pasted one line per leg and overflowed the context); 3: spacing, arithmetic and standoff guidance

STEP_SCHEMAS = [
    {"type": "object", "properties": {"type": {"const": "takeoff"}, "alt": {"type": "number"}}, "required": ["type", "alt"], "additionalProperties": False},
    {"type": "object", "properties": {"type": {"const": "goto"}, "x": {"type": "number"}, "y": {"type": "number"}, "alt": {"type": "number"}}, "required": ["type", "x", "y", "alt"], "additionalProperties": False},
    {
        "type": "object",
        "properties": {"type": {"const": "search_lawnmower"}, "x0": {"type": "number"}, "y0": {"type": "number"}, "width": {"type": "number"}, "height": {"type": "number"}, "spacing": {"type": "number"}, "alt": {"type": "number"}},
        "required": ["type", "x0", "y0", "width", "height", "spacing", "alt"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {"type": {"const": "orbit"}, "x": {"type": "number"}, "y": {"type": "number"}, "radius": {"type": "number"}, "turns": {"type": "number"}, "alt": {"type": "number"}},
        "required": ["type", "x", "y", "radius", "turns", "alt"],
        "additionalProperties": False,
    },
    {"type": "object", "properties": {"type": {"const": "report"}, "cls": {"type": "string"}}, "required": ["type", "cls"], "additionalProperties": False},
    {"type": "object", "properties": {"type": {"const": "return"}}, "required": ["type"], "additionalProperties": False},
]
MISSION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "lost_link": {"enum": ["rtl", "land"]},
        "steps": {"type": "array", "items": {"oneOf": STEP_SCHEMAS}, "minItems": 2, "maxItems": 40},
    },
    "required": ["id", "lost_link", "steps"],
    "additionalProperties": False,
}

SYSTEM = """You plan drone missions. Reply with one JSON object only.

Coordinates are metres in a local frame: x is east, y is north, the origin is the take-off point (home). Altitude is metres above home.

Step types:
- takeoff {alt}: climb straight up from home.
- goto {x, y, alt}: fly to a point.
- search_lawnmower {x0, y0, width, height, spacing, alt}: fly back-and-forth rows over the rectangle whose lower-left corner is (x0, y0), extending width metres east and height metres north, rows spaced `spacing` metres apart.
- orbit {x, y, radius, turns, alt}: circle the point (x, y).
- report {cls}: a standing instruction to report any detection of class cls (for example "vehicle"); it moves nothing.
- return {}: fly home and land.

A mission has exactly one takeoff as its first step, a return as its last step, and nothing after the return. Set lost_link to "rtl".
The camera sees the ground within a circle of radius 0.5 x altitude around the drone. Space search rows about 1.6 x that radius apart (for example 40 m apart at 50 m altitude, 24 m apart at 30 m altitude); closer rows only waste battery. A search of a W x H rectangle flies about (H / spacing + 1) x W metres.
If an area is given as x from A to B and y from C to D, then x0 = A, y0 = C, width = B - A and height = D - C; work these out carefully.
When circling a structure, keep the orbit at least 10 m clear of its edge: radius = the structure's radius + at least 10 m, and never less than 15 m. A structure said to be D metres across has radius D / 2.

Example (request: "fly to (200, 100) and report any vehicles"):
{"id": "m1", "lost_link": "rtl", "steps": [{"type": "takeoff", "alt": 50}, {"type": "goto", "x": 200, "y": 100, "alt": 50}, {"type": "report", "cls": "vehicle"}, {"type": "return"}]}"""


def limits_text(sc: Scenario, cfg: Envelope) -> str:
    h = sc.cfg["half"]
    usable = sc.start_energy_wh - cfg.reserve_wh
    return (
        "\n\nLimits for this flight (a safety monitor rejects any plan that breaks them):\n"
        f"- Stay inside the square |x| <= {h:.0f} and |y| <= {h:.0f}, and at least {cfg.fence_margin + 10:.0f} m away from its edge.\n"
        f"- Altitude must be between {cfg.alt_min:.0f} and {cfg.alt_max:.0f} metres at every point except during take-off and landing.\n"
        f"- The battery holds {sc.start_energy_wh:.0f} Wh and {cfg.reserve_wh:.0f} Wh must remain in reserve, so {usable:.0f} Wh can be used. "
        f"Flying costs about 0.005 Wh per metre travelled (more when climbing), and from every point there must be enough left to fly straight home. "
        "Keep the whole route, including the way home, short enough.\n"
        f"- Orbit radius at least {cfg.orbit_min_radius:.0f} m. Search row spacing at least {cfg.min_spacing:.0f} m and at most {cfg.max_rows - 2} rows.\n"
        "- If the requested area does not fit inside these limits, search only the part that does."
    )


def feedback_text(vetoes, max_lines: int = 6) -> str:
    """Compact reasons for a rejection: one line per distinct (rule, code), with how many times it fired, the first
    step it fired at and, where the monitor has them, the observed value and the limit."""
    groups: dict = {}
    for v in vetoes:
        g = groups.setdefault((v.rule, v.code), {"v": v, "n": 0, "worst": v.observed})
        g["n"] += 1
        if v.observed is not None and (g["worst"] is None or v.observed < g["worst"]):
            g["worst"] = v.observed
    lines = []
    for (rule, code), g in list(groups.items())[:max_lines]:
        v = g["v"]
        text = f"- {rule}: {v.message}"
        if g["n"] > 1:
            text += f" (fired {g['n']} times, first at step {v.step})"
        elif v.step is not None:
            text += f" (step {v.step})"
        if rule == "battery" and g["worst"] is not None and v.limit is not None:
            text += f"; energy left after flying home would have been {g['worst']:.1f} Wh but the reserve is {v.limit:.0f} Wh"
        elif g["worst"] is not None and v.limit is not None:
            text += f"; observed {g['worst']:.1f}, limit {v.limit:.1f}"
        lines.append(text)
    return "The safety monitor rejected that plan:\n" + "\n".join(lines) + "\nReply with a corrected plan that satisfies the limits, as one JSON object."


@dataclass
class Attempt:
    text: str
    seconds: float
    tokens: int
    approved: bool
    rules: tuple = ()
    error: str = ""


@dataclass
class PlanRun:
    scenario: str
    attempts: list = field(default_factory=list)


class LlamaPlanner:
    def __init__(self, url: str, with_limits: bool, max_retries: int = 0, temperature: float = 0.0, seed: int = 0, max_tokens: int = 1500, timeout: float = 120.0) -> None:
        self.url, self.with_limits, self.max_retries = url.rstrip("/"), with_limits, max_retries
        self.temperature, self.seed, self.max_tokens, self.timeout = temperature, seed, max_tokens, timeout

    def _chat(self, messages: list) -> tuple[str, float, int]:
        body = {
            "messages": messages,
            "temperature": self.temperature,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object", "schema": MISSION_SCHEMA},
            "cache_prompt": True,
        }
        req = urllib.request.Request(f"{self.url}/v1/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.load(r)
        dt = time.monotonic() - t0
        return data["choices"][0]["message"]["content"], dt, int(data.get("usage", {}).get("completion_tokens", 0))

    def _chat_with_retry(self, messages: list) -> tuple[str, float, int]:
        try:
            return self._chat(messages)
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise  # a client error (for example the prompt is too long) will not change on retry
            return self._chat(messages)
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            return self._chat(messages)  # one retry for a transient network or server hiccup

    def plan(self, sc: Scenario) -> PlanRun:
        cfg = sc.envelope()
        user = sc.text + (limits_text(sc, cfg) if self.with_limits else "")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        run = PlanRun(sc.id)
        state = State(0.0, 0.0, 0.0, sc.start_energy_wh)
        for attempt in range(self.max_retries + 1):
            try:
                text, dt, toks = self._chat_with_retry(messages)
            except Exception as e:  # a failed request is recorded, not hidden, and scored as no plan
                run.attempts.append(Attempt("", 0.0, 0, False, ("schema",), f"{type(e).__name__}: {str(e)[:200]}"))
                break
            dec = vet(text, cfg, state)
            run.attempts.append(Attempt(text, dt, toks, dec.approved, tuple(sorted(dec.rules))))
            if dec.approved or attempt == self.max_retries:
                break
            messages = messages + [{"role": "assistant", "content": text}, {"role": "user", "content": feedback_text(dec.vetoes)}]
        return run
