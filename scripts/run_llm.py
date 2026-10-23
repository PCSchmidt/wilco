"""Ask a llama.cpp server for a mission for every scenario of a split and save the attempts.

    python scripts/run_llm.py --url http://127.0.0.1:8081 --label q7b --split dev --limits 1 --retries 2

Writes results/llm/<label>_<split>_L<limits>_R<retries>.jsonl, one line per scenario with every attempt's text,
latency and token count. Score the file with scripts/score_llm.py.
"""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wilco.llm import PROMPT_VERSION, LlamaPlanner  # noqa: E402
from wilco.scenarios import Scenario, digest, generate_library, split  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--url", required=True)
ap.add_argument("--label", required=True)
ap.add_argument("--split", choices=["dev", "test"], required=True)
ap.add_argument("--limits", type=int, choices=[0, 1], required=True)
ap.add_argument("--retries", type=int, default=0)
ap.add_argument("--limit", type=int, default=0, help="only the first N scenarios (smoke tests)")
args = ap.parse_args()

lib = generate_library()
dev, test = split(lib)
lock = (ROOT / "eval" / "LOCK.txt").read_text()
assert digest(dev) in lock and digest(test) in lock, "scenario library no longer matches eval/LOCK.txt"
items = [s for s in (dev if args.split == "dev" else test) if isinstance(s, Scenario)]
if args.limit:
    items = items[: args.limit]
planner = LlamaPlanner(args.url, with_limits=bool(args.limits), max_retries=args.retries)
out = ROOT / "results" / "llm" / f"{args.label}_{args.split}_L{args.limits}_R{args.retries}.jsonl"
out.parent.mkdir(parents=True, exist_ok=True)
done = set()
if out.exists():  # resume: keep completed scenarios (same prompt version), redo the rest
    for line in open(out, encoding="utf-8"):
        row = json.loads(line)
        if row.get("prompt_version") == PROMPT_VERSION:
            done.add(row["scenario"])
    kept = [line for line in open(out, encoding="utf-8") if json.loads(line)["scenario"] in done]
    out.write_text("".join(kept), encoding="utf-8")
with open(out, "a", encoding="utf-8") as f:
    for i, sc in enumerate(items):
        if sc.id in done:
            continue
        run = planner.plan(sc)
        f.write(json.dumps({"scenario": sc.id, "prompt_version": PROMPT_VERSION, "attempts": [a.__dict__ for a in run.attempts]}) + "\n")
        f.flush()
        a = run.attempts[-1]
        print(f"[{i + 1}/{len(items)}] {sc.id} {sc.kind}: attempts {len(run.attempts)} approved {a.approved} {a.seconds:.1f}s {a.tokens} tokens {a.error}", flush=True)
