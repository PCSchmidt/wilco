"""Generate the scenario library, split it into dev and test halves, and write the hash lock.

    python scripts/build_scenarios.py

Writes eval/scenarios.json (every scenario) and eval/LOCK.txt (SHA-256 of the canonical JSON of each half). The
test half is scored once, after the harness is final; the lock shows it was not edited in between.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wilco.scenarios import SEED, digest, generate_library, split, to_json  # noqa: E402

lib = generate_library()
dev, test = split(lib)
(ROOT / "eval").mkdir(exist_ok=True)
(ROOT / "eval" / "scenarios.json").write_text(to_json(lib) + "\n", encoding="utf-8")
(ROOT / "eval" / "LOCK.txt").write_text(
    f"seed {SEED}\nscenarios {len(lib)} (dev {len(dev)}, test {len(test)})\ndev  sha256 {digest(dev)}\ntest sha256 {digest(test)}\n",
    encoding="utf-8",
)
print((ROOT / "eval" / "LOCK.txt").read_text())
