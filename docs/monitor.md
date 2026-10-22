# The safety monitor (milestone 0.1)

A deterministic checker that sits between a mission planner (eventually a vision-language model) and the vehicle. It reads a plan as data, judges it against a fixed envelope, and returns approve or a list of vetoes, each naming the rule, the step, the observed value and the limit. The planner cannot configure it, and it fails closed: any error while checking is a veto.

This page says exactly what the rules are, what has been checked, and what the word "provable" does and does not cover here. Nothing has flown. Everything below is about the logic, tested in Python against a brute-force re-implementation; no simulator or vehicle is involved yet.

## What is checked

A plan is JSON with a closed set of step types: `takeoff`, `goto`, `search_lawnmower`, `orbit`, `report`, `return`. Unknown step types and fields, wrong types, booleans-as-numbers, non-finite numbers and out-of-range numbers are rejected before any rule runs (`schema`). There is no step that engages, attacks or releases anything; adding one would require changing the schema.

| Rule | Definition |
| --- | --- |
| `sequence` | Exactly one `takeoff`, and it is first. The last step is `return`, and nothing follows a `return`. |
| `bad_param` | Search spacing at least `min_spacing`, width positive, at most `max_rows` rows; orbit radius at least `orbit_min_radius`, turns positive. |
| `geofence` | Every leg's endpoints are inside the fence and at least `fence_margin` from its boundary, and so is the whole leg; an orbit's whole circle keeps the margin. The fence must be convex (checked when the envelope is built). |
| `altitude` | Every waypoint altitude (and every orbit altitude) is within `[alt_min, alt_max]`. The ground legs of takeoff and landing are exempt. |
| `battery` | After every leg, remaining energy minus the cost of flying straight home and landing is at least the reserve. Costs are multiplied by a safety factor. The lost-link action does not change this: the reserve always assumes a flight home. |
| `duration` | Estimated flight time (path length over cruise speed) within the limit. |
| `lost_link` | The declared lost-link action is `rtl` or `land`. (With a convex fence a straight path home from any allowed point stays inside, so no separate path check is needed.) |
| `evidence` | See below. |

### No action without evidence

Defined as a run-time gate on two executor actions, not a property of the static plan. A `report` step in a plan is only a standing instruction ("report any vehicle you detect") and cannot carry a position or evidence. When the executor wants to act, it asks the monitor:

- **Report** a detection of class C at position P citing evidence ids: allowed only if every cited id exists in the sensor log, comes from a trusted source (the onboard camera, not the planner), has class C, has confidence at least `min_confidence` (a NaN fails), is no older than `max_age_s` and not from the future, and lies within `max_position_error_m` of P. At least one id is required, and one bad citation spoils the report.
- **Track** (fly to a detection's position at the current altitude): the same evidence test, plus fence, altitude and energy checks from the vehicle's current state.

The evidence log is written by perception, outside the model. If the model could write it, the rule would be vacuous.

## What has been checked

- **Hand-written cases** (`tests/test_monitor.py`): one or more per rule and per evidence failure, the malformed-plan list, fail-closed behaviour, config validation.
- **An independent oracle** (`src/wilco/oracle.py`): walks the flight in 0.5 m steps with a half-plane fence test and its own path and energy bookkeeping. 400 random plans per run (the generator approves 740 of 3,000, so the test exercises approvals): whenever the monitor approves, the oracle must find the plan safe. No failure.
- **No gratuitous vetoes**: 400 random plans that are safe by construction (inside 400 m, altitudes in the band, at most four waypoints) are all approved.
- **Monotonicity**: tightening the envelope (more reserve, larger safety factor, shorter duration, narrower altitude band, bigger margin, smaller battery, smaller fence) never turns a veto into an approval.
- **Thresholds** (`tests/test_tightness.py`): bisecting battery size and fence size, the monitor flips at the same value as the oracle (within 1% of energy, within 1 m of fence) for goto, search and orbit plans. Random plans almost never land near a threshold, so without this test a monitor that was slightly lax or strict would pass.
- **Fuzzing**: arbitrary JSON, arbitrary text, and valid plans with one field replaced by junk never raise and are never wrongly approved.
- **Mutation testing** (`scripts/mutation_test.py`, `docs/mutation_results.txt`): 29 hand-made faults (dropped margins, skipped energy checks, inverted comparisons, accepted untrusted or stale evidence, fail-open handling, loosened parsing, broken geometry); the suite kills 24. The first run killed 21 of 29 and showed three real test gaps (the straight-home energy rule at run time, the exact energy threshold, the orbit margin), closed by the threshold tests. The five survivors are equivalent for the static checker: the final-reserve check and the orbit-end check are implied by the checks at later waypoints (the energy used plus the cost of flying home never decreases along a path); the non-finite check is implied by the range check (NaN and infinity fail the bounds test); flipping the ray-casting side does not change the inside/outside answer (left and right crossing parity are equal); and the segment-crossing test is implied by endpoint margins on a convex fence. The two energy checks are kept so that a veto names the step that caused it.

One thing the testing changed: my first orbit rule bounded the return cost at the farthest point of the circle. The orbit threshold test showed it was about 10% stricter than the oracle, and reasoning about it showed the end of the orbit is always the tightest point, so the rule now checks exactly there.

## What this does not establish

- **"Provable" means provable against the stated rules and model.** The energy model (per-metre costs, a safety factor) is an assumption, not a measurement of any vehicle. Wind, terrain, obstacles, other aircraft, GPS error beyond the fence margin, communications loss behaviour of real firmware, and vehicle dynamics are not modelled. A plan the monitor approves can still be unsafe in the world.
- **The oracle is not independent in the strong sense.** It is my second implementation of my own reading of the rules, sharing the energy constants and my understanding of the geometry. It catches coding mistakes; it cannot catch a rule that is wrongly defined.
- **Convex fences only.** A non-convex area (an exclusion zone inside the fence, say) needs the segment test and a real return-path check, which the code partly has (`geometry.segment_ok` is exact for simple polygons) but the envelope refuses to build and the tests do not cover.
- **Static plus two runtime actions.** Re-checking arbitrary motion commands at run time, a moving clock, and battery drift against the estimate are not implemented.
- **Evidence trust is by label.** The monitor believes the `source` field; protecting the log from tampering is the executor's job and is not built.
- **No simulator yet.** Whether the executor's real behaviour matches the monitor's path expansion is milestone 0.3.

## Reproduce

```
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # or pip install pytest hypothesis
pytest                                   # 63 tests, about 10 s
python scripts/mutation_test.py          # about 2 minutes
```
