"""Score saved language-model plans with the harness.

    python scripts/score_llm.py results/llm/q7b_dev_L1_R2.jsonl [...] > results/llm_dev.txt

For each file: the first attempt (what the model does alone, vetted by the monitor) and the final attempt after
up to R corrections that quote the monitor's reasons back to the model.
"""

import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wilco.harness import score_text, wilson  # noqa: E402
from wilco.scenarios import Scenario, generate_library  # noqa: E402

BY_ID = {s.id: s for s in generate_library() if isinstance(s, Scenario)}


def pc(k, n):
    p, lo, hi = wilson(k, n)
    return f"{100 * p:5.1f}% [{lo * 100:4.1f},{hi * 100:5.1f}]"


for path in sys.argv[1:]:
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    n = len(rows)
    first = [score_text(BY_ID[r["scenario"]], r["attempts"][0]["text"]) for r in rows]
    final = [score_text(BY_ID[r["scenario"]], r["attempts"][-1]["text"]) for r in rows]
    name = pathlib.Path(path).stem
    print(f"{name}  ({n} scenarios)")
    for label, res in (("first attempt", first), ("after corrections", final)):
        by = {}
        for r, sc in zip(res, rows):
            k = BY_ID[sc["scenario"]].kind
            by.setdefault(k, [0, 0])
            by[k][0] += r.success_with_monitor
            by[k][1] += 1
        print(
            f"  {label:<18} malformed {sum(not r.parsed for r in res):>2}  unsafe {sum(r.unsafe for r in res):>2}  vetoed {sum(not r.approved for r in res):>2}  "
            f"unsafe approved {sum(r.unsafe and r.approved for r in res)}  success with monitor {pc(sum(r.success_with_monitor for r in res), n)}  "
            f"without {pc(sum(r.success_without_monitor for r in res), n)}  unsafe flown without monitor {pc(sum(r.unsafe_executed_without_monitor for r in res), n)}"
        )
        print("    success by kind: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(by.items())))
    veto_rules = {}
    for r in first:
        if not r.approved:
            for ru in r.rules:
                veto_rules[ru] = veto_rules.get(ru, 0) + 1
    print("  first-attempt vetoes by rule: " + (", ".join(f"{k} {v}" for k, v in sorted(veto_rules.items())) or "none"))
    need = sum(len(r["attempts"]) > 1 for r in rows)
    rec = sum(len(r["attempts"]) > 1 and r["attempts"][-1]["approved"] for r in rows)
    print(f"  plans needing a correction: {need}/{n}; approved after correction: {rec}/{need}")
    t_first = [r["attempts"][0]["seconds"] for r in rows]
    t_all = [sum(a["seconds"] for a in r["attempts"]) for r in rows]
    toks = [r["attempts"][0]["tokens"] for r in rows]
    print(f"  planning time (laptop GPU, cool start): first attempt median {np.median(t_first):.1f} s, p90 {np.percentile(t_first, 90):.1f} s; with corrections median {np.median(t_all):.1f} s; median {int(np.median(toks))} tokens\n")
