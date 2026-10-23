"""Failure taxonomy for saved language-model plans: why did each plan fail to do the job?

    python scripts/taxonomy_llm.py results/llm/q7b_dev_L1_R2.jsonl [...]

Each plan (first attempt) is put in exactly one bucket, checked in this order:
  success                      approved and did the task
  malformed                    did not parse (for example, cut off at the token limit)
  vetoed: <rules>              parsed but the monitor refused it, by rule
  approved, wrong extent       a search whose rectangle's width or height is more than 25% off the requested (reachable) area
  approved, covers too little  a search with the right extent but coverage below the 0.5 needed (spacing or altitude too coarse)
  approved, no report step     did the search but did not say it would report vehicles
  approved, target missed      covered enough but a reachable vehicle was not under the footprint
  approved, orbit wrong        inspection with the wrong centre (more than 25 m), too small a radius, or under one turn
  approved, route wrong        transit that missed or reordered a reachable waypoint
  approved, declined           takeoff and return only
"""

import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wilco.harness import score_text  # noqa: E402
from wilco.scenarios import Scenario, generate_library  # noqa: E402
from wilco.schema import Lawnmower, Orbit, Report, SchemaError, parse_json  # noqa: E402

BY_ID = {s.id: s for s in generate_library() if isinstance(s, Scenario)}


def bucket(sc: Scenario, text: str) -> str:
    res = score_text(sc, text)
    if not res.parsed:
        return "malformed"
    if not res.approved:
        return "vetoed: " + ", ".join(res.rules)
    if res.success_with_monitor:
        return "success"
    m = parse_json(text)
    kinds = [type(s).__name__ for s in m.steps]
    if kinds in (["Takeoff", "Return"],):
        return "approved, declined"
    lim = sc.cfg["half"] - 5.0 - 2.0
    if sc.kind in ("search", "degraded"):
        mow = next((s for s in m.steps if isinstance(s, Lawnmower)), None)
        if mow is None:
            return "approved, no search step"
        x0, y0, w, h = sc.area
        want_w = min(x0 + w, lim) - max(x0, -lim)
        want_h = min(y0 + h, lim) - max(y0, -lim)
        if want_w > 0 and (abs(mow.width - want_w) > 0.25 * want_w or (want_h > 20 and abs(mow.height - want_h) > 0.25 * want_h)):
            return "approved, wrong extent"
        if res.coverage is not None and res.coverage < 0.5:
            return "approved, covers too little"
        if not any(isinstance(s, Report) for s in m.steps):
            return "approved, no report step"
        return "approved, target missed"
    if sc.kind == "inspect":
        return "approved, orbit wrong"
    return "approved, route wrong"


for path in sys.argv[1:]:
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    counts: dict[str, int] = {}
    for r in rows:
        sc = BY_ID[r["scenario"]]
        b = bucket(sc, r["attempts"][0]["text"])
        counts[b] = counts.get(b, 0) + 1
    print(f"{pathlib.Path(path).stem} ({len(rows)} first attempts)")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>3}  {k}")
    print()
