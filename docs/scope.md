# WILCO scope (draft, 2026-10-21)

Language-tasked drone autonomy where a model proposes and a deterministic monitor disposes. This page fixes what to build first, what can be done on the laptop alone, and what is still unknown. Nothing is built.

## Question the project answers

When a small vision-language model turns an operator's sentence into a mission, how often is the proposal unsafe or wrong, how often does an independent safety monitor catch it, and what does the monitor cost in missions that were fine? The model is treated as an unreliable sensor; the product is the measured failure taxonomy, not the demo.

## Guardrail

Search, inspection and reporting only. No weapons release, no terminal guidance, no target engagement logic, in code, prompts, scenarios or docs. Any flight outside simulation is optional and only under the pilot's own legal authority (in the US, FAA Part 107).

## Milestones

| # | Milestone | Needs | Done when |
| --- | --- | --- | --- |
| 0.0 | Simulator feasibility: PX4 SITL running headless in a container, one scripted flight read back over MAVLink | Docker (works) | Met for the built-in simulator; Gazebo camera frames confirmed published, content unchecked (see Risks) |
| 0.1 (done, Python, see monitor.md) | Mission schema and safety monitor: a typed mission (waypoints, pattern, report triggers) and a deterministic checker for geofence, altitude band, battery reserve, lost-link action, and "no action without evidence" | Laptop, no simulator | Property tests pass; every veto carries a machine-readable reason; a fuzzer finds no mission the monitor accepts that violates a rule |
| 0.2 (done, see harness.md) | Scenario library and scoring harness: scripted scenarios with ground-truth outcomes, run against a stub planner (oracle, random, adversarial) | Laptop | Scores for the stubs bracket what a real model can do (oracle near perfect, adversarial maximally vetoed) |
| 0.3 | Executor: behaviour-tree nodes (search, orbit, report, return) flying the mission in SITL | Simulator from 0.0 | Scenario outcomes from real simulated flight agree with the harness's predicted outcomes |
| 0.4 | VLM planner: a 3B-class vision-language model (Qwen2.5-VL is the candidate) producing constrained JSON missions from text plus a camera frame | Laptop GPU; Jetson later | Unsafe-proposal and veto rates with intervals over the scenario library, with a failure taxonomy |
| 0.5 | Hardware in the loop on the Jetson: planner latency and the same autonomy container against simulated physics | Jetson (not bought) | Latency table desktop vs Jetson |

The deliberate order puts the safety monitor and the scoring harness before any model, so the model's results mean something when it arrives.

## What is laptop-only

0.0 to 0.4. Only 0.5 (and any flight) needs hardware.

## Risks and unknowns

- **Simulator on Windows (checked 2026-10-22, milestone 0.0 mostly met).** In Docker Desktop's Linux engine, using `px4io/px4-dev-simulation-jammy` (1.35 GB image) with PX4 v1.17.0 built from source in a Docker volume: (a) PX4's built-in physics simulator (`sihsim_quadx`) flew a scripted takeoff and landing over MAVLink from Python (pymavlink): arm and takeoff accepted, peak 10.62 m for a 10 m command, landed at 0.23 m; (b) that image lacks Gazebo Harmonic, so `gz_x500` failed until `gz-harmonic` was installed from the OSRF apt repository, after which `gz_x500_mono_cam` built and ran headless with no GPU, and the camera topic published frames (one message read, sequence number 136). Not yet checked: that the frames are non-blank and at a usable resolution, flight under Gazebo physics, the ROS 2 bridge, and VLM input from those frames. The container is the reproducible route; it needs a Dockerfile in the repo so the Harmonic install is not a manual step. Build time was not measured reliably.
- **Scenario realism.** 200 scripted scenarios written by one person are a test of the monitor, not of the world. State this wherever results appear, and hold out a test set with a hash lock as TriageBox does.
- **The monitor can only be as good as its rules.** "No action without evidence" needs a precise definition (what counts as evidence, from which sensor) or it is vacuous. Writing that definition is the first design task of 0.1.
- **Version drift.** PX4, Gazebo, ROS 2 and MAVSDK versions move; pin them and record them.
- **Scope creep.** The source plan lists fine-tuning on synthetic missions, multi-vehicle work and a real flight; none is needed for the core question.

## Deep-dive part

The failure taxonomy of the planner and the veto-cost curve (missions rejected that were safe), reported with intervals, plus the monitor's property tests as evidence that "provable" is limited to the stated rules and not to the world.
