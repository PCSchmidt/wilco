# The executor in simulation (milestone 0.3)

A small executor flies vetted missions in PX4 SITL and logs where the vehicle actually went, so the monitor's and harness's predictions can be checked against flights instead of arithmetic. What it found: **the 5 m fence margin did not hold in flight**, and a plain waypoint executor left the fence at a corner the plan cleared by 6 m. A rate-limited setpoint fixed it in this setup. Duration estimates were also about 46% short. Everything is simulated (PX4's built-in physics, not Gazebo), so these are measurements of this simulated quadcopter and this controller, not of any real vehicle.

## What was built

- `sim/executor.py` runs in the simulator container. It first asks the monitor to vet the plan and refuses a plan that is vetoed (tested: it does not even connect to the vehicle). It then turns steps into offboard position setpoints over MAVLink (mission x = east, y = north, altitude above home), takes off, flies each waypoint (an orbit is a polygon of 6-degree steps, a search is its serpentine rows), lands at home, and logs the vehicle's position at its message rate. It decides nothing itself.
- `sim/run_flight.sh` starts a clean PX4 SITL (`sihsim_quadx`, speed factor 10), runs the executor, and stops PX4. `sim/run_batch.py` flies a stratified sample; `sim/run_speed_sweep.py` flies the fence-corner probe at several speeds with and without the rate-limited setpoint. `scripts/analyze_flights.py` compares flights with predictions and writes [results/flights.txt](../results/flights.txt).
- `harness._outcome` now accepts a flown trajectory, so the same scoring code that predicted success scores the flight.

## What was flown

43 flights in one batch: 29 oracle plans (8 search, 5 degraded, 8 inspect, 8 transit, picked at random), 8 approved sloppy plans, and 6 fence-edge probes (one of them vetoed, correctly, because its altitude exceeded that scenario's ceiling, so the executor refused to fly it). Then 8 more corner-probe flights for the speed study.

A note on the data: my first attempt at the 43-flight batch stalled for two hours on a cleanup step and then ran twice at once because my restart did not stop the first batch. The two batches shared one PX4 working directory, so I discarded every log from them and flew the whole batch again as a single process. All numbers here come from that clean run (and from a smoke flight before it).

## Results (plain executor: the setpoint is the next waypoint)

| What the monitor or harness assumed | What was flown |
| --- | --- |
| Cruise speed 12 m/s, so flight time = path length ÷ 12 | Flight time was 1.46 times that (median; range 1.34 to 2.72 across 42 flights): the vehicle averaged 8.3 m/s over a whole flight including climb, acceleration, corners and landing |
| The vehicle stays within the 5 m fence margin of the planned path | Largest distance from the planned path: median 8.8 m per flight, worst 20.4 m. Closest approach to the fence boundary fell below the 5 m margin in 3 of 29 oracle flights (worst 1.5 m); no oracle flight left the fence |
| A plan with 6 m of clearance at a corner stays inside | **It did not**: the fence-corner probe, planned 6.0 m inside the fence, flew **5.2 m outside** it |
| Altitude stays in the band | No flight exceeded the ceiling; the lower bound was not checked in this analysis |
| Coverage and success predicted by the footprint model | Recomputed on the flown paths, success agreed for all 42 flights, and area coverage differed by at most 0.008 |
| Energy reserve | **Not tested**: PX4's simulator here does not give a battery model comparable with the monitor's energy numbers, so the energy rule is still unvalidated against anything but my own arithmetic |

The agreement on predicted success is a weaker result than it looks: it shows the flown path stays within the camera footprint of the planned one (the footprint is tens of metres wide), using the same footprint model that made the prediction. It says nothing about whether a real camera would see a target.

## The fence margin, and a fix

The corner probe (two 994 m legs, 6 m inside a 1,000 m fence, turning 90 degrees at the corner), repeated at several speed limits. "Shortfall" is how much of the planned 6.0 m clearance was lost (planned closest approach minus flown closest approach).

| Executor | Speed limit | Closest approach to the fence | Shortfall against the plan |
| --- | --- | --- | --- |
| plain | 4 m/s | -2.4 m (outside) | 8.4 m |
| plain | 8 m/s | -6.2 m (outside) | 12.2 m |
| plain | 12 m/s (two runs) | -9.3 m, -8.4 m (outside) | 15.3 m, 14.4 m |
| rate-limited | 4 m/s | +3.3 m | 2.7 m |
| rate-limited | 8 m/s | +4.9 m | 1.1 m |
| rate-limited | 12 m/s (two runs) | +1.9 m, +5.0 m | 4.1 m, 1.0 m |

(An earlier set of plain runs, before I widened the executor's time budget, gave similar overshoots of 11.7 to 16.6 m between 6 and 12 m/s; its 4 m/s run is discarded because the budget made the executor give up before reaching the corner.)

Reading it: sending the vehicle straight at the next waypoint makes it overshoot corners by 8 to 15 m, more at higher speed, so a 5 m margin in the monitor says nothing about where the vehicle actually flies. Replacing the step with a setpoint that moves toward the waypoint at the speed limit (a "carrot") cut the shortfall to 1 to 4 m, and the vehicle stayed inside the fence in all four rate-limited runs, though at 12 m/s with one run only 1.9 m from the boundary. Two runs per speed show run-to-run spread of 1 to 3 m, so the numbers are indicative, not a fit.

The honest conclusion is a coupling between the monitor and the executor: the monitor's margin is only meaningful together with a stated tracking-error bound of the executor that will fly the plan. With the plain executor it would need to be about 15 m or more; with the rate-limited one about 5 m was enough in these four flights. The monitor does not enforce or know this yet, and the batch above was flown with the plain executor.

## What this does not establish

- **One simulated vehicle and controller.** PX4's built-in simulator (SIH) with default tuning; a real vehicle, wind, GPS error and other controllers will differ, most likely for the worse.
- **Small samples where it matters.** The overshoot study is 2 flights per speed and one geometry (a right-angle corner at the fence); the 43-flight batch has 3 close approaches among 29 oracle flights. No confidence intervals were computed.
- **No Gazebo and no camera.** Detection was never tested; the harness's footprint model is still an assumption about a real sensor.
- **Energy and battery are untested** (above), and so are lost-link behaviour and wind.
- **The lower altitude bound and the largest path deviations** (up to 20.4 m on one oracle flight) were not analysed in detail.
- **Simulation at 10 times real time.** The sweeps were run with PX4's lockstep speed factor set to 10; the smoke flight gave the same path at 5 and 10, but nothing was compared against 1 times real time.

## Reproduce

Needs Docker with the simulator image built as described in `docs/scope.md`.

```
python sim/run_batch.py 10            # about an hour
python sim/run_speed_sweep.py 10      # about 15 minutes
python scripts/analyze_flights.py
pytest tests/test_executor.py         # executor logic against a stub, no simulator
```
