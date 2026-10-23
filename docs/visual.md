# Planning from camera frames (the vision part of milestone 0.4)

A small vision-language model (Qwen2.5-VL-3B, 4-bit, on the laptop GPU) is given a rendered camera frame and the request "fly to the red vehicle in this image and circle it", and the result is scored against the vehicle's true position. **The 3B model can find the vehicle to within about 2 m when asked in its own bounding-box format, but when it must plan end to end it ignores the image and orbits the point under the camera, even when a 7B text model does the arithmetic. Letting the planner write out its arithmetic first recovers most of the loss (60% of test scenes against 73% with code doing the geometry).** Everything is simulated: frames come from Gazebo's headless camera, not a real sensor.

## Setup

- **Scenes.** 60 scenes (30 dev, 30 test, hash-locked in `eval/visual/LOCK.txt`; the test half was run once after the method was frozen): a downward-looking camera 80 m up (90 degree field of view, 640 x 480 pixels, 0.25 m per pixel, north up, east right) over flat ground with one red vehicle (4.5 x 2 m, about 18 x 8 pixels), 0 to 3 vehicles of other colours, 0 to 2 red *non-vehicle* objects (8 x 8 m red buildings, the decoys), 0 to 3 grey buildings and 3 to 8 trees. Frames are rendered by Gazebo (`sim/visual/`, headless, software rendering); 60 PNGs are in `eval/visual/frames/`.
- **Camera model, checked.** Rendered test frames put both known vehicles within 2 pixels of the predicted positions, so `x = cam_x + (u - 320) * 0.25`, `y = cam_y + (240 - v) * 0.25` is exact up to rendering.
- **Success.** The estimate of the vehicle's position is the centre of the plan's first orbit; a plan succeeds at tolerance T if the monitor approves it, it has an orbit, and the estimate is within T metres of the truth (T = 5, 10, 15 or 25 m; the orbit radius is 25 m, so 15 m is "orbiting around the vehicle, not around something else"). Intervals are 95% Wilson; 30 scenes per half, so every interval is wide.
- **Configurations** (all with the same model, temperature 0): *end to end*: the image and request go to the model, which answers with a mission as schema-constrained JSON; *ground + code*: the model is asked only for the vehicle's bounding box in its native format, and code converts the box centre to metres and builds the plan; *two steps, model plans*: the model locates the vehicle, then the same model plans from its own pixel position; *two steps, 7B plans*: the model locates it, Qwen2.5-7B (text only) plans, schema-constrained; *two steps, 7B plans with reasoning*: as the last, but the planner writes out its arithmetic in plain text before the JSON.
- **Baselines without learning.** *Oracle* (true position). *Red region of vehicle size*: threshold the red pixels, pick the connected region of vehicle area (60 to 400 pixels). This uses the known size of the objects, which the model is not told. *Largest red region*. *Image centre*: orbit the point under the camera.

## Results on the locked test half

| Method | Success at 5 m | at 15 m | at 25 m | Median position error |
| --- | --- | --- | --- | --- |
| oracle | 100% | 100% [88.6, 100] | 100% | 0 m |
| red region of vehicle size | 100% | 100% [88.6, 100] | 100% | 0.8 m |
| largest red region | 26.7% | 26.7% [14.2, 44.4] | 33.3% | 46.2 m |
| image centre | 6.7% | 6.7% [1.8, 21.3] | 6.7% | 49.4 m |
| VLM-3B, end to end | 3.3% | 3.3% [0.6, 16.7] | 6.7% | 50.8 m |
| VLM-3B, two steps, model plans | 3.3% | 3.3% [0.6, 16.7] | 6.7% | 52.9 m |
| VLM-3B + 7B planner (constrained JSON) | 0.0% | 3.3% [0.6, 16.7] | 3.3% | 61.0 m |
| VLM-3B + 7B planner with written arithmetic | 53.3% | 60.0% [42.3, 75.4] | 70.0% | 3.3 m (26 of 30 plans had a position) |
| **VLM-3B ground + code geometry** | **66.7%** | **73.3% [55.6, 85.8]** | **83.3%** | **2.4 m** |

Time per plan (laptop GPU, not a Jetson): ground + code 0.9 s; end to end 1.7 s; model plans 4.5 s; 7B constrained 11 s; with written arithmetic 28 s.

Failure breakdown at 15 m: *ground + code*: 22 successes, **5 went to a red building** (the decoy), 3 had no object near the planned point. *With written arithmetic*: 18 successes, 5 red-building decoys, 4 gave no usable plan (no orbit or unparseable), 3 off. *End to end and the two constrained variants*: about 21 to 24 of 30 planned a point where there is no object at all.

## What this shows

- **The model can localise.** In its native grounding format ("output the bounding box in JSON"), median error was 2.4 m on test (1.2 m on dev) with a vehicle 18 pixels long. The main error is semantic, not geometric: asked for the red *car*, it boxes the red *building* in 5 of 30 scenes, the same decoy that defeats the naive red-region baseline.
- **End to end it does not use the image.** Reading the plans: the model orbits the camera's own coordinates (quoted in the request) or a number it made up. Giving it the exact pixel-to-metres formula, a visual-specific system prompt, and even its own pixel position as text did not change that (3.3%).
- **The arithmetic fails inside constrained JSON, not in the model's ability.** With the pixel position given, a 7B text model writing the mission straight into schema-constrained JSON got 3.3%; allowed to write out the calculation first, it reached 60%, with a 3.3 m median error. The extra cost is 28 seconds per plan instead of 1 to 11.
- **Design implication.** Use the vision model as a detector (pixel position out), do the geometry in code, and give the model the planning only where it can reason in text. That pipeline reached 73%; a color-and-size detector reached 100%, which says what this task needs and not that classical detectors are better in general (it exploits the known object size and flat, high-contrast scenes).

## Dev history (30 dev scenes)

End to end with the text planner's system prompt and no formula: 3.3% at 15 m (the model also added a search step with spacing 1.6, which the monitor vetoed); end to end with a visual system prompt and the formula: 3.3%; ground + code: 76.7% (median 1.2 m); two steps (model plans): 3.3%; 7B constrained: 3.3% (23% at 25 m); 7B with written arithmetic, first version: every plan vetoed as a schema error because the model omitted the mission `id` (the strict parser did its job), then 66.7% after the prompt named the required keys. My first ground prompt asked for a schema-constrained answer and got "not found" for every scene; the model's native format was what worked. Those intermediate results are in `results/visual/`.

## What this does not establish

- **Synthetic scenes** (boxes and cylinders on a plane, one light direction, no clutter, no weather, no scale or blur), one camera height, a 30-scene test half. A real camera, real vehicles and real terrain are much harder, and the red-building decoy is one specific hard case I chose.
- **One vision model, one size** (3B, 4-bit). A larger model might plan end to end; I did not test one. Greedy decoding, one run per configuration.
- **The prompts were developed on the dev half** (the history above); the test half was run once with them frozen, but 30 scenes cannot separate 73% from 60% with confidence (the intervals overlap).
- **The red-building decoy is also my choice of definition of "red vehicle"**; a different request ("the red thing") would not have this failure.
- **No flight was simulated** for these plans; success is geometric (where the orbit is centred), approved by the monitor, not flown in PX4.

## Reproduce

```
python scripts/build_visual.py                      # scenes, lock, Gazebo worlds
# render the frames in the simulator container: bash sim/visual/render_all.sh (see docs/scope.md for the image)
# serve Qwen2.5-VL-3B with its mmproj on port 8083 (and a 7B text model on 8081 for the planner variants)
python scripts/run_visual.py --split dev --baselines-only
python scripts/run_visual.py --split dev --url http://127.0.0.1:8083 --label qwen25vl3b --mode ground
python scripts/run_visual.py --split test ...       # once, with the method frozen
pytest tests/test_visual.py
```
