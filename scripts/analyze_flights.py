"""Compare simulated flights (sim/out/*.json from sim/run_batch.py) with what the monitor and harness predicted.

    python scripts/analyze_flights.py       # writes results/flights.txt and results/flights.json
"""

import dataclasses
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wilco.harness import SAMPLE, _outcome  # noqa: E402
from wilco.oracle import path  # noqa: E402
from wilco.scenarios import Scenario  # noqa: E402
from wilco.schema import parse_json  # noqa: E402

OUT, WORK = ROOT / "sim" / "out", ROOT / "sim" / "work"
lines: list[str] = []


def log(s=""):
    print(s)
    lines.append(s)


def load_scenario(name):
    raw = json.loads((WORK / f"{name}_scenario.json").read_text())
    raw = {k: (tuple(v) if isinstance(v, list) else v) for k, v in raw.items()}
    raw["targets"] = tuple(tuple(t) for t in raw["targets"])
    raw["waypoints"] = tuple(tuple(w) for w in raw["waypoints"])
    return Scenario(**raw)


def dist_to_polyline(points, poly):
    """Minimum 3D distance from each point to a polyline (array of vertices)."""
    a, b = poly[:-1], poly[1:]
    ab = b - a
    n2 = (ab**2).sum(axis=1)
    n2[n2 == 0] = 1e-12
    best = np.full(len(points), np.inf)
    for i, p in enumerate(points):
        t = np.clip(((p - a) * ab).sum(axis=1) / n2, 0, 1)
        proj = a + t[:, None] * ab
        best[i] = np.linalg.norm(proj - p, axis=1).min()
    return best


rows = []
for f in sorted(OUT.glob("*.json")):
    name = f.stem
    d = json.loads(f.read_text())
    if not d.get("flown"):
        rows.append({"name": name, "flown": False})
        continue
    sc = load_scenario(name)
    cfg = sc.envelope()
    mission = parse_json((WORK / f"{name}_plan.json").read_text())
    L = np.array(d["log"])
    t, pos = L[:, 0], L[:, 1:4]
    airborne = np.nonzero(pos[:, 2] > 1.0)[0]
    if len(airborne) == 0:
        rows.append({"name": name, "flown": True, "completed": False, "note": "never left the ground"})
        continue
    i0, i1 = airborne[0], airborne[-1]
    dur = float(t[i1] - t[i0])
    planned = np.array([q for q, tag in path(mission, cfg, SAMPLE)])
    plan_len = float(np.linalg.norm(np.diff(planned, axis=0), axis=1).sum())
    flown_len = float(np.linalg.norm(np.diff(pos[i0 : i1 + 1], axis=0), axis=1).sum())
    h = sc.cfg["half"]
    fence_d = h - np.maximum(np.abs(pos[:, 0]), np.abs(pos[:, 1]))  # distance to the fence boundary (negative outside)
    plan_fence_d = h - np.maximum(np.abs(planned[:, 0]), np.abs(planned[:, 1]))
    sub = pos[i0 : i1 + 1][::5]
    xtrack = dist_to_polyline(sub, planned)
    ok_pred, _, cov_pred = _outcome(mission, sc)
    ok_fly, _, cov_fly = _outcome(mission, sc, flight=(pos[i0 : i1 + 1][::5], t[i0 : i1 + 1][::5] - t[i0]))
    rows.append(
        {
            "name": name,
            "flown": True,
            "completed": bool(d["completed"]),
            "kind": sc.kind,
            "dur_flown": dur,
            "dur_pred": plan_len / cfg.speed,
            "mean_speed": flown_len / dur,
            "min_fence_d": float(fence_d.min()),
            "plan_min_fence_d": float(plan_fence_d.min()),
            "max_alt_over": float(pos[:, 2].max() - cfg.alt_max),
            "xtrack_max": float(xtrack.max()),
            "xtrack_p95": float(np.percentile(xtrack, 95)),
            "ok_pred": bool(ok_pred),
            "ok_fly": bool(ok_fly),
            "cov_pred": cov_pred,
            "cov_fly": cov_fly,
            "margin": cfg.fence_margin,
        }
    )

flown = [r for r in rows if r.get("flown") and "kind" in r]
log(f"{len(rows)} flights logged; {len(flown)} completed take-off-to-landing logs, {sum(1 for r in rows if r.get('flown') and not r.get('completed'))} did not complete.\n")
by = {}
for r in flown:
    by.setdefault(r["name"].split("_")[0], []).append(r)
for grp, rs in by.items():
    log(f"== {grp} ({len(rs)} flights)")
    dur_ratio = np.array([r["dur_flown"] / r["dur_pred"] for r in rs])
    log(f"  flight time / monitor's estimate (path length at {12.0:.0f} m/s): median {np.median(dur_ratio):.2f}, range {dur_ratio.min():.2f} to {dur_ratio.max():.2f}")
    sp = np.array([r["mean_speed"] for r in rs])
    log(f"  mean ground+vertical speed over the flight: median {np.median(sp):.1f} m/s (assumed 12.0)")
    xt = np.array([r["xtrack_max"] for r in rs])
    log(f"  largest distance from the planned path: median {np.median(xt):.1f} m, worst {xt.max():.1f} m; 95th percentile within a flight: median {np.median([r['xtrack_p95'] for r in rs]):.1f} m")
    short = [r for r in rs if r["min_fence_d"] < r["margin"]]
    outside = [r for r in rs if r["min_fence_d"] < 0]
    log(f"  closest approach to the fence boundary: worst {min(r['min_fence_d'] for r in rs):.1f} m (margin {rs[0]['margin']:.0f} m); flights closer than the margin: {len(short)}, outside the fence: {len(outside)}")
    log(f"  highest altitude above the band's ceiling: worst {max(r['max_alt_over'] for r in rs):+.1f} m")
    agree = sum(r["ok_pred"] == r["ok_fly"] for r in rs)
    log(f"  task success predicted vs recomputed on the flown path: agree {agree}/{len(rs)}; predicted success {sum(r['ok_pred'] for r in rs)}, flown {sum(r['ok_fly'] for r in rs)}")
    cov = [(r["cov_pred"], r["cov_fly"]) for r in rs if r["cov_pred"] is not None and r["cov_fly"] is not None]
    if cov:
        diffs = np.array([b - a for a, b in cov])
        log(f"  area coverage flown minus predicted: mean {diffs.mean():+.3f}, worst {diffs.min():+.3f} (over {len(cov)} search flights)")
    log()

probes = [r for r in flown if r["name"].startswith("probe")]
if probes:
    log("Fence-edge probes (plan legal by the monitor: at least 5 m inside the fence; planned closest approach in brackets):")
    for r in probes:
        log(f"  {r['name']:<24} flown closest approach {r['min_fence_d']:+6.1f} m  (planned {r['plan_min_fence_d']:.1f} m)  max distance from planned path {r['xtrack_max']:.1f} m  max altitude over ceiling {r['max_alt_over']:+.1f} m")
sweep = sorted((r for r in flown if r["name"].startswith("speed_")), key=lambda r: r["name"])
if sweep:
    log()
    log("Fence-corner probe (planned 6.0 m inside the fence at the corner) at different speed limits:")
    for r in sweep:
        v = " ".join(r["name"].split("_")[-2:])
        log(f"  {v:<12} flown closest approach to the fence {r['min_fence_d']:+6.1f} m  (shortfall against the plan {6.0 - r['min_fence_d']:5.1f} m)  max distance from planned path {r['xtrack_max']:5.1f} m  flight time {r['dur_flown']:.0f} s")
(ROOT / "results").mkdir(exist_ok=True)
(ROOT / "results" / "flights.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
(ROOT / "results" / "flights.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
