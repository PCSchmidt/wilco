"""Fly a stratified sample of plans in PX4 SITL, one container run per flight, and keep the logs.

    python sim/run_batch.py [speed_factor]

Host-side driver: writes plan and scenario JSON to sim/work/, runs sim/run_flight.sh in the simulator container,
and collects sim/out/<name>.json. Plans come from the oracle and sloppy stub planners and from fence-edge probes.
"""

import dataclasses
import json
import pathlib
import random
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wilco.monitor import State, vet  # noqa: E402
from wilco.planners import propose  # noqa: E402
from wilco.scenarios import Scenario, generate_library  # noqa: E402

SPEED = sys.argv[1] if len(sys.argv) > 1 else "10"
WORK, OUT = ROOT / "sim" / "work", ROOT / "sim" / "out"
WORK.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

lib = [s for s in generate_library() if isinstance(s, Scenario)]
rng = random.Random(3)
jobs = []  # (name, scenario, plan_text)
for kind, n in (("search", 8), ("degraded", 5), ("inspect", 8), ("transit", 8)):
    pool = [(i, s) for i, s in enumerate(lib) if s.kind == kind]
    for i, s in rng.sample(pool, n):
        jobs.append((f"oracle_{s.id}", s, propose("oracle", s, i)))
pool = [(i, s) for i, s in enumerate(lib)]
rng.shuffle(pool)
n_sloppy = 0
for i, s in pool:
    if n_sloppy >= 8:
        break
    text = propose("sloppy", s, i)
    if vet(text, s.envelope(), State(0.0, 0.0, 0.0, s.start_energy_wh)).approved:
        jobs.append((f"sloppy_{s.id}", s, text))
        n_sloppy += 1

# fence-edge probes: legal by the monitor's rule (at least 5 m inside the fence), flown at full speed
probe_base = next(s for s in lib if s.cfg["half"] == 1000.0)
for k, (label, steps) in enumerate(
    [
        ("east_edge_long", [{"type": "goto", "x": 994.0, "y": 0.0, "alt": 40.0}]),
        ("east_edge_short", [{"type": "goto", "x": 900.0, "y": 0.0, "alt": 40.0}, {"type": "goto", "x": 994.0, "y": 0.0, "alt": 40.0}]),
        ("corner", [{"type": "goto", "x": 994.0, "y": 0.0, "alt": 40.0}, {"type": "goto", "x": 994.0, "y": 994.0, "alt": 40.0}]),
        ("corner_diagonal", [{"type": "goto", "x": 994.0, "y": 994.0, "alt": 40.0}]),
        ("north_edge", [{"type": "goto", "x": 0.0, "y": 994.0, "alt": 40.0}]),
        ("climb_to_ceiling", [{"type": "goto", "x": 300.0, "y": 0.0, "alt": 120.0}]),
    ]
):
    plan = {"id": f"probe-{label}", "lost_link": "rtl", "steps": [{"type": "takeoff", "alt": 40}, *steps, {"type": "return"}]}
    sc = dataclasses.replace(probe_base, id=f"P{k}")
    jobs.append((f"probe_{label}", sc, json.dumps(plan)))

print(f"{len(jobs)} flights at speed factor {SPEED}", flush=True)
for n, (name, sc, text) in enumerate(jobs, 1):
    out = OUT / f"{name}.json"
    if out.exists():
        continue
    (WORK / f"{name}_plan.json").write_text(text)
    (WORK / f"{name}_scenario.json").write_text(json.dumps(dataclasses.asdict(sc)))
    cmd = [
        "docker", "run", "--rm", "-v", "px4work:/work", "-v", f"{ROOT}:/wilco", "px4-wilco",
        "bash", "/wilco/sim/run_flight.sh", f"/wilco/sim/work/{name}_plan.json", f"/wilco/sim/work/{name}_scenario.json", f"/wilco/sim/out/{name}.json", SPEED,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"})
    status = "ok" if out.exists() else f"NO OUTPUT rc={r.returncode} {r.stderr[-200:]}"
    print(f"[{n}/{len(jobs)}] {name}: {status}", flush=True)
