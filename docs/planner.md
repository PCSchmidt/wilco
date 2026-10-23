# A language-model planner against the monitor (milestone 0.4)

Two local Qwen2.5 models (7B Q4_K_M and 1.5B Q8_0, served by llama.cpp on the laptop GPU) plan the locked scenarios, constrained to the mission schema, and the monitor and harness score what they propose. Three results matter. **No plan from either model that was unsafe got past the monitor.** **Telling the 7B model the vehicle's limits made it worse, not better.** **The 1.5B model cannot do this task with constrained output: it loops.** The planner is text-only: the scenario library has no camera frames, so the "vision" part of the original plan is not exercised.

## Setup

- **Planner.** The request text goes to the model with a fixed system prompt (the step types, the coordinate frame, the camera footprint, guidance on row spacing and standoff). Output is constrained by a JSON schema (llama.cpp turns it into a grammar), so it parses unless it is cut off; whether it is safe is the monitor's question. Temperature 0, seed 0, at most 1,500 tokens.
- **Conditions.** *Limits not stated*: the model sees only the request. *Limits stated*: the prompt adds the fence, altitude band, battery energy and reserve, and the cost of flying. With limits stated, a vetoed plan is also sent back, with the monitor's reasons (deduplicated, with the numbers), for up to two corrections.
- **Scoring.** The same harness as milestone 0.2 (task success needs the monitor to approve and the plan to do the job; "unsafe" is judged by the independent oracle; 64 mission scenarios in the locked test half). The first attempt is scored alone and again after any corrections.
- **Procedure.** The prompt and conditions were developed on the dev half (66 scenarios) over three versions. The test half was then run once, with prompt version 3 frozen. Every attempt of every run is saved in `results/llm/` (no request failed).

## Results on the locked test half

Full output: [results/llm_test.txt](../results/llm_test.txt), taxonomy in [results/llm_test_taxonomy.txt](../results/llm_test_taxonomy.txt). Task success with the monitor, with 95% Wilson intervals; stub planners from milestone 0.2 for reference (oracle 93.8%, the sloppy literal planner 78.1%, timid 0%).

| Planner | Malformed | Plans that are unsafe | Success, first attempt | Success after corrections | Unsafe plans that would have flown without the monitor | Unsafe plans the monitor approved |
| --- | --- | --- | --- | --- | --- | --- |
| 7B, limits not stated | 0 | 14 | 71.9% [59.9, 81.4] | (no corrections) | 21.9% | 0 |
| 7B, limits stated | 0 | 16 | 50.0% [38.1, 61.9] | 53.1% [41.1, 64.8] | 25.0%, then 10.9% | 0 |
| 1.5B, limits not stated | 8 | 26 | 35.9% [25.3, 48.2] | (no corrections) | 40.6% | 0 |
| 1.5B, limits stated | 43 | 7 | 18.8% [11.1, 30.0] | 20.3% [12.3, 31.7] | 10.9%, then 9.4% | 0 |

The dev half gave the same picture (7B: 66.7% without limits, 54.5% with, 57.6% after corrections; 1.5B: 27.3% and 18.2%).

### 1. The monitor held

Across 4 configurations on both halves, including every corrected plan, **0 unsafe plans were approved**. Without the monitor, the share of all plans that would have flown unsafe (limits not stated) was 21.9% for the 7B model and 40.6% for the 1.5B model. This is the monitor against these models on these scenarios, with the caveats of milestone 0.2 (the oracle and the monitor are both mine).

### 2. Telling the model the limits made it worse

For the 7B model, success fell from 71.9% to 50.0% when the limits were stated (the same 64 scenarios; 16 succeeded only without limits, 2 only with them; exact paired sign test p = 0.0013; the dev half pointed the same way, 13 against 5, p = 0.096). The taxonomy of first attempts shows where it went:

| First-attempt outcome (7B, 64 scenarios) | Limits not stated | Limits stated |
| --- | --- | --- |
| success | 46 | 32 |
| vetoed by the monitor (battery, duration, geofence) | 14 | 16 |
| approved but covers too little of the area | 0 | 6 |
| approved but a reachable vehicle was missed | 0 | 4 |
| approved but the orbit is wrong (centre, standoff, turns) | 4 | 5 |
| approved but the extent is wrong | 0 | 1 |

With the limits in front of it the model tried to fit the battery and got it wrong in both directions: more plans that still break the energy limit (15 plans with a battery veto against 8), and a new group of plans that cut the search so short it no longer did the job (10 plans that cover too little or miss a vehicle, against none). I did not find the cause of the first group beyond that: the model does not compute the cost of a plan it writes.

### 3. Corrections help a little

Quoting the monitor's reasons back (up to two rounds) turned 9 of the 16 vetoed 7B plans into approved ones, but success rose only from 50.0% to 53.1%, because the approved replacements are safe and often still do not do the job (they shrink the search). The unsafe share that would have flown fell from 25.0% to 10.9%. For the 1.5B model corrections recovered 1 of 50.

### 4. The 1.5B model loops

With limits stated, 42 of 64 runs of the 1.5B model hit the 1,500-token cap: it repeats search and orbit steps (about 26 to 36 steps in the capped outputs, against the schema's cap of 40) until it is cut off, so 43 plans did not parse. With limits not stated, 4 runs did the same. Constrained decoding guarantees syntax, not that the model stops. A 1.5B model with this prompt is not a usable planner here; its 12 successes (limits stated) are all transit scenarios.

### Failure modes seen in the 7B model's plans (from reading them)

- Arithmetic: an area given as "x from -871 to -74" planned with a width of 127 instead of 797.
- Swapped coordinates: a structure at x 82, y -268 circled at x -268, y 82 (twice in the dev half).
- Standoff off by a metre or less: a radius of 25 around a 16 m structure (needs 26) fails my 10 m rule; the monitor does not check this, because it is task quality, not safety.
- Over-fine search rows (5 to 10 m) chosen to be thorough, which the battery cannot afford; mostly fixed by the v3 guidance, not entirely.
- Ignoring "search only the part that fits": the whole requested area is planned, a part of it outside the fence (the geofence vetoes).

## Prompt history on the dev half

| Version | Change | 7B, limits not stated | 7B, limits stated (first, after corrections) |
| --- | --- | --- | --- |
| v1 | first prompt; feedback pasted one line per leg | not kept | run aborted: the feedback overflowed the context (a plan with 80 rows produced 500 identical battery vetoes, 9,500 tokens) |
| v2 | feedback deduplicated and numeric | 53.0% | 27.3%, 48.5% |
| v3 (final) | guidance on row spacing, width and height arithmetic, and standoff; corrected scenario text for inspection | 66.7% | 54.5%, 57.6% |

The v2 to v3 gain mixes two changes: the prompt guidance and a fix to my scenario text. The inspection scenarios said "about 40 m across" for a structure whose radius was 39.6 m, so a model that did what the text said circled inside the structure; no model succeeded on an inspection (0 of 15) until I corrected the text to 2r across. That changed the scenario library's hash lock (new `eval/LOCK.txt`). It was done before any model touched the test half, and I re-ran the stub planners on both halves to confirm their results did not change (they ignore the text: all numbers identical).

## Latency

Median time for a first plan on the laptop GPU (RTX A4500, not thermally controlled, not a Jetson): 7B 2.1 s (90th percentile 5.1 s) with limits not stated, 6.9 s with limits stated; 1.5B 1.4 s and 17.6 s (the capped runs). The 7B figure with limits stated was measured after the other runs on the same GPU, which collapses its clocks when hot (see the environment notes), so it probably overstates the cost of the longer prompt; I did not separate the two. These are laptop numbers; the Jetson comparison is milestone 0.5.

## What this does not establish

- **One author's scenarios and one prompt family.** 64 test scenarios, written by me; a differently worded request or system prompt can move these numbers a lot (it did, between v2 and v3). The 7B result that stating limits hurts is for this prompt; a prompt that has the model compute the energy budget step by step, or that gives it a tool for it, might reverse it. I did not try.
- **Greedy decoding only,** one seed. No variance across samples.
- **Only Qwen2.5 text models.** No vision-language model, no camera frames, no fine-tuning (the source plan's before-and-after fine-tuning on synthetic missions was not attempted), no larger models.
- **The monitor's perfect record is against these plans.** Models that find plans designed to pass the monitor while being unsafe in a way the oracle also misses are outside what was tested.
- **Success definitions are mine** (half coverage, 30 m visit radius, 10 m standoff) and were set before scoring; the corrections loop is the only place a model sees the monitor's reasoning.

## Reproduce

```
# start llama.cpp servers as in scripts/run_llm_all.sh (paths to the Qwen2.5 GGUF files are in that script)
bash scripts/run_llm_all.sh dev        # about 40 minutes
bash scripts/run_llm_all.sh test       # the locked half, run once
python scripts/score_llm.py results/llm/q7b_test_L0_R0.jsonl results/llm/q7b_test_L1_R2.jsonl
python scripts/taxonomy_llm.py results/llm/q7b_test_L1_R2.jsonl
pytest tests/test_llm.py               # the client, against a stub server
```
