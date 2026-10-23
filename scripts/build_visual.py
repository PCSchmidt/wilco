"""Generate the visual scene set, write the hash lock and the Gazebo worlds.

    python scripts/build_visual.py            # then render the frames with sim/visual/render_all.sh in the container

Writes eval/visual/scenes.json, eval/visual/LOCK.txt (SHA-256 of the dev and test halves) and sim/visual/worlds/*.sdf.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wilco import visual as V  # noqa: E402

scenes = V.generate()
dev, test = V.split(scenes)
(ROOT / "eval" / "visual").mkdir(parents=True, exist_ok=True)
(ROOT / "eval" / "visual" / "scenes.json").write_text(V.to_json(scenes) + "\n", encoding="utf-8")
(ROOT / "eval" / "visual" / "LOCK.txt").write_text(
    f"seed {V.SEED}\nscenes {len(scenes)} (dev {len(dev)}, test {len(test)})\ndev  sha256 {V.digest(dev)}\ntest sha256 {V.digest(test)}\n", encoding="utf-8"
)
wd = ROOT / "sim" / "visual" / "worlds"
wd.mkdir(parents=True, exist_ok=True)
for s in scenes:
    (wd / f"{s.id}.sdf").write_text(V.world_sdf(s), encoding="utf-8")
print((ROOT / "eval" / "visual" / "LOCK.txt").read_text())
