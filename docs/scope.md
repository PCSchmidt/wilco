# WILCO scope (draft, 2026-10-21)

Language-tasked drone autonomy where a model proposes and a deterministic monitor disposes. This page fixes what to build first, what can be done on the laptop alone, and what is still unknown. Nothing is built.

## Question the project answers

When a small vision-language model turns an operator's sentence into a mission, how often is the proposal unsafe or wrong, how often does an independent safety monitor catch it, and what does the monitor cost in missions that were fine? The model is treated as an unreliable sensor; the product is the measured failure taxonomy, not the demo.

## Guardrail

Search, inspection and reporting only. No weapons release, no terminal guidance, no target engagement logic, in code, prompts, scenarios or docs. Any flight outside simulation is optional and only under the pilot's own legal authority (in the US, FAA Part 107).

## Milestones

| # | Milestone | Needs | Done when |
| --- | --- | --- | --- |
| 0.0 | Simulator feasibility: PX4 SITL running headless in a container, one scripted flight read back over MAVLink | Docker (works) | A takeoff-hold-land log on this laptop, with the real build and run time recorded |
| 0.1 | Mission schema and safety monitor: a typed mission (waypoints, pattern, report triggers) and a deterministic checker for geofence, altitude band, battery reserve, lost-link action, and "no action without evidence" | Laptop, no simulator | Property tests pass; every veto carries a machine-readable reason; a fuzzer finds no mission the monitor accepts that violates a rule |
| 0.2 | Scenario library and scoring harness: scripted scenarios with ground-truth outcomes, run against a stub planner (oracle, random, adversarial) | Laptop | Scores for the stubs bracket what a real model can do (oracle near perfect, adversarial maximally vetoed) |
| 0.3 | Executor: behaviour-tree nodes (search, orbit, report, return) flying the mission in SITL | Simulator from 0.0 | Scenario outcomes from real simulated flight agree with the harness's predicted outcomes |
| 0.4 | VLM planner: a 3B-class vision-language model (Qwen2.5-VL is the candidate) producing constrained JSON missions from text plus a camera frame | Laptop GPU; Jetson later | Unsafe-proposal and veto rates with intervals over the scenario library, with a failure taxonomy |
| 0.5 | Hardware in the loop on the Jetson: planner latency and the same autonomy container against simulated physics | Jetson (not bought) | Latency table desktop vs Jetson |

The deliberate order puts the safety monitor and the scoring harness before any model, so the model's results mean something when it arrives.

## What is laptop-only

0.0 to 0.4. Only 0.5 (and any flight) needs hardware.

## Risks and unknowns

- **Simulator on Windows.** PX4 with Gazebo is Linux-first. Docker Desktop's Linux engine runs, so a headless container is the route; whether Gazebo's camera sensor renders usefully without a GPU in the container is unknown and decides how real the VLM's input can be. Fallback: feed the VLM rendered or recorded frames outside the simulator and say so.
- **Scenario realism.** 200 scripted scenarios written by one person are a test of the monitor, not of the world. State this wherever results appear, and hold out a test set with a hash lock as TriageBox does.
- **The monitor can only be as good as its rules.** "No action without evidence" needs a precise definition (what counts as evidence, from which sensor) or it is vacuous. Writing that definition is the first design task of 0.1.
- **Version drift.** PX4, Gazebo, ROS 2 and MAVSDK versions move; pin them and record them.
- **Scope creep.** The source plan lists fine-tuning on synthetic missions, multi-vehicle work and a real flight; none is needed for the core question.

## Deep-dive part

The failure taxonomy of the planner and the veto-cost curve (missions rejected that were safe), reported with intervals, plus the monitor's property tests as evidence that "provable" is limited to the stated rules and not to the world.
