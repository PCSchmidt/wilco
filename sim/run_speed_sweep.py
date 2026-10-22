"""Fly the fence-corner probe at several speed limits (and repeat the full-speed one) to see how overshoot scales.

    python sim/run_speed_sweep.py [sim_speed_factor]
"""

import dataclasses
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wilco.scenarios import Scenario, generate_library  # noqa: E402

SIM = sys.argv[1] if len(sys.argv) > 1 else "10"
lib = [s for s in generate_library() if isinstance(s, Scenario)]
base = next(s for s in lib if s.cfg["half"] == 1000.0)
plan = {"id": "speed-corner", "lost_link": "rtl", "steps": [
    {"type": "takeoff", "alt": 40}, {"type": "goto", "x": 994.0, "y": 0.0, "alt": 40.0}, {"type": "goto", "x": 994.0, "y": 994.0, "alt": 40.0}, {"type": "return"}]}
work, out = ROOT / "sim" / "work", ROOT / "sim" / "out"
for mode, tag, v in (("plain", "v4", 4), ("plain", "v8", 8), ("plain", "v12a", 12), ("plain", "v12b", 12), ("carrot", "v4", 4), ("carrot", "v8", 8), ("carrot", "v12a", 12), ("carrot", "v12b", 12)):
    name = f"speed_corner_{mode}_{tag}"
    if (out / f"{name}.json").exists():
        continue
    (work / f"{name}_plan.json").write_text(json.dumps(plan))
    (work / f"{name}_scenario.json").write_text(json.dumps(dataclasses.asdict(dataclasses.replace(base, id=name[:8]))))
    cmd = ["docker", "run", "--rm", "-e", f"EXEC_SPEED={v}", *(["-e", "EXEC_CARROT=1"] if mode == "carrot" else []), "-v", "px4work:/work", "-v", f"{ROOT}:/wilco", "px4-wilco", "bash", "/wilco/sim/run_flight.sh",
           f"/wilco/sim/work/{name}_plan.json", f"/wilco/sim/work/{name}_scenario.json", f"/wilco/sim/out/{name}.json", SIM]
    subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    print(name, "ok" if (out / f"{name}.json").exists() else "NO OUTPUT", flush=True)
