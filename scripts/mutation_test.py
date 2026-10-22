"""Mutation test: break the monitor in specific ways and check the test suite notices.

Each mutant is one textual edit. The suite is run on it; "killed" means at least one test failed. A survivor is
either a gap in the tests or an equivalent mutant (a change that cannot alter behaviour); the table says which
and why. Run from the repo root: python scripts/mutation_test.py
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "wilco"

MUTANTS = [
    ("monitor.py", "fence margin ignored on legs", "self.fence, self.cfg.fence_margin):", "self.fence, 0.001):"),
    ("monitor.py", "battery check skipped on legs", "        if check_bat:\n            self.check_battery(b, step)", "        if False:\n            self.check_battery(b, step)"),
    ("monitor.py", "return energy ignores distance", "c.safety_factor * (c.k_h * math.hypot(p[0] - c.home[0], p[1] - c.home[1]) + c.k_down * p[2])", "c.safety_factor * (c.k_down * p[2])"),
    ("monitor.py", "safety factor dropped from legs", "return c.safety_factor * (c.k_h * h + c.k_up", "return 1.0 * (c.k_h * h + c.k_up"),
    ("monitor.py", "reserve ignored", "- extra_return - self.cfg.reserve_wh\n", "- extra_return\n"),
    ("monitor.py", "orbit end battery check removed", "            tr.check_battery(tr.pos, i)\n", "            pass\n"),
    ("monitor.py", "orbit energy not charged", "tr.used += tr.cfg.safety_factor * cfg.k_h * arc", "tr.used += 0.0"),
    ("monitor.py", "orbit circle margin dropped", "geo.circle_ok((s.x, s.y), s.radius, list(cfg.fence), cfg.fence_margin)", "geo.circle_ok((s.x, s.y), s.radius, list(cfg.fence), 0.001)"),
    ("monitor.py", "upper altitude not checked", "if not c.alt_min <= z <= c.alt_max:", "if not c.alt_min <= z:"),
    ("monitor.py", "lower altitude not checked", "if not c.alt_min <= z <= c.alt_max:", "if not z <= c.alt_max:"),
    ("monitor.py", "takeoff altitude unchecked", "tr.fly_to((cfg.home[0], cfg.home[1], s.alt), i)", "tr.fly_to((cfg.home[0], cfg.home[1], s.alt), i, check_alt=False)"),
    ("monitor.py", "lawnmower last row missing", "int(math.ceil(s.height / s.spacing)) + 1", "int(math.ceil(s.height / s.spacing))"),
    ("monitor.py", "duration comparison inverted", "if tr.length / cfg.speed > cfg.max_duration_s:", "if tr.length / cfg.speed < cfg.max_duration_s:"),
    ("monitor.py", "final reserve check removed", "if tr.energy0 - tr.used < cfg.reserve_wh:\n        tr.veto(\"battery\", \"final\"", "if False:\n        tr.veto(\"battery\", \"final\""),
    ("monitor.py", "step after return allowed", "tr.veto(\"sequence\", \"after_return\"", "(lambda *a: None)(\"sequence\", \"after_return\""),
    ("monitor.py", "stale/future evidence accepted", "if not 0 <= age <= p.max_age_s:", "if not age <= p.max_age_s:"),
    ("monitor.py", "old evidence accepted", "if not 0 <= age <= p.max_age_s:", "if not 0 <= age:"),
    ("monitor.py", "untrusted source accepted", "if e.source not in p.trusted_sources:", "if False:"),
    ("monitor.py", "claimed position not compared", "if not err <= p.max_position_error_m:", "if False:"),
    ("monitor.py", "NaN confidence accepted", "if not e.confidence >= p.min_confidence:", "if e.confidence < p.min_confidence:"),
    ("monitor.py", "class mismatch accepted", "if e.cls != cls:", "if False:"),
    ("monitor.py", "fails open", "    except Exception as e:  # fail closed\n        return Decision((Veto(\"schema\", \"internal\", f\"monitor error, plan rejected: {type(e).__name__}: {e}\"),))", "    except Exception as e:  # fail closed\n        return Decision(())"),
    ("monitor.py", "track action skips fence/energy", "        tr.fly_to((e.x, e.y, s.alt), 0)\n", "        pass\n"),
    ("schema.py", "booleans accepted as numbers", "if isinstance(v, bool) or not isinstance(v, (int, float)):", "if not isinstance(v, (int, float)):"),
    ("schema.py", "unknown fields accepted", "    extra = set(d) - allowed\n    if extra:", "    extra = set()\n    if extra:"),
    ("schema.py", "non-finite numbers accepted", "    if not math.isfinite(v):", "    if False:"),
    ("geometry.py", "ray-casting side flipped", "if p[0] < x:", "if p[0] > x:"),
    ("geometry.py", "boundary distance uses max", "return min(point_seg_dist(p, poly[i], poly[(i + 1) % n]) for i in range(n))", "return max(point_seg_dist(p, poly[i], poly[(i + 1) % n]) for i in range(n))"),
    ("geometry.py", "segment crossing test removed", "    return all(seg_seg_dist(a, b, poly[i], poly[(i + 1) % n]) >= margin for i in range(n))", "    return True"),
]


def run_tests() -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True, timeout=900)
    return r.returncode == 0


def main() -> None:
    assert run_tests(), "the unmutated suite must pass first"
    rows = []
    for fname, label, old, new in MUTANTS:
        path = SRC / fname
        original = path.read_text(encoding="utf-8")
        assert original.count(old) == 1, f"{label}: pattern occurs {original.count(old)} times"
        path.write_text(original.replace(old, new), encoding="utf-8")
        try:
            survived = run_tests()
        finally:
            path.write_text(original, encoding="utf-8")
        rows.append((label, "SURVIVED" if survived else "killed"))
        print(f"{rows[-1][1]:9} {label}", flush=True)
    killed = sum(s == "killed" for _, s in rows)
    print(f"\n{killed}/{len(rows)} mutants killed")


if __name__ == "__main__":
    main()
