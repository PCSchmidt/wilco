# WILCO

Language-tasked drone autonomy where a model proposes and a deterministic monitor disposes. Scope: search, inspection and reporting only. There is no step type that engages, attacks or releases anything, and none will be added.

**Status:** milestones 0.1 (the safety monitor) and 0.2 (a 200-scenario library with a hash-locked test half and a scoring harness, bracketed by stub planners) are done as tested logic in Python. Milestone 0.3 flew 43 vetted missions in PX4 simulation and found that the monitor's 5 m fence margin did not hold with a plain waypoint executor (a plan 6 m inside the fence flew 5 m outside it) but did with a rate-limited one ([docs/executor.md](docs/executor.md)). No language-model planner, no camera, no hardware. On the locked test half no stub planner got an unsafe plan past the monitor, and the evidence gate stopped every fabricated or forged report but cannot stop a perception false alarm. See [docs/harness.md](docs/harness.md) and [docs/scope.md](docs/scope.md) for the plan and [docs/monitor.md](docs/monitor.md) for what the monitor checks, what has been tested and what it does not establish.

## What is here

- `src/wilco/schema.py`: strict parsing of a mission (a closed set of step types; unknown or non-finite input is rejected).
- `src/wilco/monitor.py`: geofence, altitude band, battery reserve with straight-home cost, duration, sequence rules, and a run-time evidence gate for reports and tracking; fails closed.
- `src/wilco/scenarios.py`, `planners.py`, `harness.py`, `oracle.py`: the scenario library, stub planners and reporters, scoring, and the brute-force oracle used as ground truth. `eval/` holds the locked library.
- `tests/`: hand-written cases, property tests against an independent brute-force oracle, threshold tests, fuzzing. `scripts/mutation_test.py`: 29 injected faults, 24 caught, 5 shown equivalent.

## Run

```
pip install pytest hypothesis numpy
pytest
```

## Limits

Everything rests on an assumed energy model and a convex fence; the oracle is my own second implementation, not an independent specification; no wind, terrain, obstacle or dynamics model. The simulator route (PX4 SITL in a container, with a Gazebo camera) was checked for feasibility on a laptop and is described in the scope document.
