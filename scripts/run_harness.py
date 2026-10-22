"""Score the stub planners and reporters on the dev or test half of the scenario library.

    python scripts/run_harness.py dev
    python scripts/run_harness.py test      # run once, after the harness is final

Writes results/harness_<split>.txt and .json. Refuses to score the test half if eval/LOCK.txt no longer matches.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wilco.harness import run_evidence, run_mission, summarize, wilson  # noqa: E402
from wilco.planners import MISSION_PLANNERS, REPORTERS  # noqa: E402
from wilco.scenarios import EvidenceCase, Scenario, digest, generate_library, split  # noqa: E402

split_name = sys.argv[1]
assert split_name in ("dev", "test")
lib = generate_library()
dev, test = split(lib)
lock = (ROOT / "eval" / "LOCK.txt").read_text()
for name, items in (("dev", dev), ("test", test)):
    assert digest(items) in lock, f"{name} half no longer matches eval/LOCK.txt"
items = dev if split_name == "dev" else test
missions = [s for s in items if isinstance(s, Scenario)]
cases = [s for s in items if isinstance(s, EvidenceCase)]

out: list[str] = []
data: dict = {"split": split_name}


def log(s=""):
    print(s)
    out.append(s)


def pc(k, n):
    p, lo, hi = wilson(k, n)
    return f"{100 * p:5.1f}% [{100 * lo:4.1f},{100 * hi:5.1f}]"


log(f"Scenario harness, {split_name} half: {len(missions)} mission scenarios, {len(cases)} evidence cases. Stub planners only (no model).")
log("Intervals are 95% Wilson. 'with monitor' = a vetoed plan does not fly; 'without' = every parseable plan flies.\n")
kinds: dict[str, int] = {}
for sc in missions:
    kinds[sc.kind] = kinds.get(sc.kind, 0) + 1
log("Mission scenarios by kind: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) + "\n")

data["missions"] = {}
for p in MISSION_PLANNERS:
    res = [run_mission(p, sc, i) for i, sc in enumerate(missions)]
    s = summarize(res)
    data["missions"][p] = s
    n = s["n"]
    log(f"{p}")
    log(f"  proposals: malformed {s['malformed']}, parsed-but-unsafe {s['unsafe']}, safe {n - s['malformed'] - s['unsafe']}  (of {n})")
    log(f"  monitor: approved {s['approved']}, vetoed {s['vetoed']}; unsafe plans approved {s['unsafe_approved']}; safe plans vetoed {s['safe_vetoed']}")
    log(f"  task success without monitor {pc(s['success_without_monitor'], n)}   with monitor {pc(s['success_with_monitor'], n)}   unsafe plans flown without monitor {pc(s['unsafe_executed_without_monitor'], n)}")
    log("  success by kind (without -> with monitor): " + ", ".join(f"{k} {v['success_without']}->{v['success_with']}/{v['n']}" for k, v in s["by_kind"].items()))
    if s["veto_rules"]:
        log("  vetoes by rule: " + ", ".join(f"{k} {v}" for k, v in sorted(s["veto_rules"].items())))
    if s["median_time_to_detect_s"] is not None:
        log(f"  median time to first detection among successes: {s['median_time_to_detect_s']:.0f} s")
    log()

log("Run-time evidence gate (what the executor may report), by reporter:")
log(f"  situations: " + ", ".join(f"{k} {sum(1 for c in cases if c.situation == k)}" for k in sorted({c.situation for c in cases})))
cfg = missions[0].envelope()
data["evidence"] = {}
for r in REPORTERS:
    res = [run_evidence(r, c, i, cfg) for i, c in enumerate(cases)]
    acted = [x for x in res if x.acted]
    harmful = [x for x in acted if x.allowed and not x.legit]
    missed = [x for x in acted if not x.allowed and x.legit]
    allowed_ok = [x for x in acted if x.allowed and x.legit]
    by_sit = {}
    for x in harmful:
        by_sit[x.situation] = by_sit.get(x.situation, 0) + 1
    data["evidence"][r] = {"acted": len(acted), "harmful_allowed": len(harmful), "legit_denied": len(missed), "legit_allowed": len(allowed_ok), "harmful_by_situation": by_sit}
    log(f"  {r:<11} acted {len(acted):>3}: allowed-but-not-real {len(harmful):>2} {by_sit}, real-but-denied {len(missed):>2}, real-and-allowed {len(allowed_ok):>2}")

(ROOT / "results").mkdir(exist_ok=True)
(ROOT / "results" / f"harness_{split_name}.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
(ROOT / "results" / f"harness_{split_name}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
