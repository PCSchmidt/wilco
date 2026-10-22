# WILCO

Language-tasked drone autonomy where a model proposes and a deterministic monitor disposes. Scope: search, inspection and reporting only. There is no step type that engages, attacks or releases anything, and none will be added.

**Status:** milestone 0.1 (the safety monitor) is done as tested logic in Python. Nothing flies yet: no simulator integration, no planner model, no hardware. See [docs/scope.md](docs/scope.md) for the plan and [docs/monitor.md](docs/monitor.md) for what the monitor checks, what has been tested and what it does not establish.

## What is here

- `src/wilco/schema.py`: strict parsing of a mission (a closed set of step types; unknown or non-finite input is rejected).
- `src/wilco/monitor.py`: geofence, altitude band, battery reserve with straight-home cost, duration, sequence rules, and a run-time evidence gate for reports and tracking; fails closed.
- `tests/`: hand-written cases, property tests against an independent brute-force oracle, threshold tests, fuzzing. `scripts/mutation_test.py`: 29 injected faults, 24 caught, 5 shown equivalent.

## Run

```
pip install pytest hypothesis
pytest
```

## Limits

Everything rests on an assumed energy model and a convex fence; the oracle is my own second implementation, not an independent specification; no wind, terrain, obstacle or dynamics model. The simulator route (PX4 SITL in a container, with a Gazebo camera) was checked for feasibility on a laptop and is described in the scope document.
