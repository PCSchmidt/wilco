"""Plan from camera frames with a vision-language model, and score against the true vehicle position.

    python scripts/run_visual.py --split dev --url http://127.0.0.1:8083 --label qwen25vl3b    # needs the VLM server
    python scripts/run_visual.py --split dev --baselines-only                                  # no model

For each scene the model sees the rendered frame plus the request text (camera position, orientation, scale, and
"fly to the red vehicle and circle it"), and must answer with a mission as schema-constrained JSON. The plan is
vetted by the monitor, then scored: the estimate of the vehicle's position is the centre of the plan's first orbit,
and a plan succeeds at tolerance T if the monitor approves it, it has an orbit, and the estimate is within T metres
of the true position (T = 5, 10, 15, 25 m; an orbit of radius 25 m is meant to be flown around the vehicle).
Baselines (no learning): the perfect oracle; a colour-blob detector that picks the red region whose area matches a
vehicle (this uses the known object size, which the model is not told); the naive largest red region; and always
orbiting the image centre.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import pathlib
import re
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wilco import visual as V  # noqa: E402
from wilco.harness import wilson  # noqa: E402
from wilco.llm import LlamaPlanner  # noqa: E402
from wilco.monitor import Envelope, State, vet  # noqa: E402

TOLS = (5.0, 10.0, 15.0, 25.0)


def extract_json(reply: str) -> str:
    """The last JSON object that has a 'steps' list in a reply that may have prose before it; '' if there is none."""
    dec = json.JSONDecoder()
    best = ""
    for i, ch in enumerate(reply):
        if ch == "{":
            try:
                obj, end = dec.raw_decode(reply[i:])
            except ValueError:
                continue
            if isinstance(obj, dict) and "steps" in obj:
                best = reply[i : i + end]
    return best


def chat_plain(planner: LlamaPlanner, messages: list, max_tokens: int = 200) -> tuple[str, float, int]:
    """A plain chat call (no output schema): the model answers in its own grounding format."""
    import urllib.request

    body = {"messages": messages, "temperature": 0.0, "seed": 0, "max_tokens": max_tokens, "cache_prompt": True}
    req = urllib.request.Request(f"{planner.url}/v1/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=planner.timeout) as r:
        data = json.load(r)
    return data["choices"][0]["message"]["content"], time.monotonic() - t0, int(data.get("usage", {}).get("completion_tokens", 0))


def envelope(scene: V.Scene) -> Envelope:
    h = scene.half
    return Envelope(fence=((-h, -h), (h, -h), (h, h), (-h, h)))


def plan_json(x: float, y: float) -> str:
    return json.dumps({"id": "p", "lost_link": "rtl", "steps": [
        {"type": "takeoff", "alt": 50}, {"type": "orbit", "x": round(x, 1), "y": round(y, 1), "radius": 25, "turns": 2, "alt": 50},
        {"type": "report", "cls": "vehicle"}, {"type": "return"}]})


def red_components(img: np.ndarray) -> list[tuple[int, float, float]]:
    """Connected regions of strongly red pixels as (area, u, v) of their centroids (4-connected flood fill)."""
    mask = (img[..., 0] > 170) & (img[..., 1] < 90) & (img[..., 2] < 90)
    seen = np.zeros_like(mask)
    out = []
    h, w = mask.shape
    for v0 in range(h):
        for u0 in range(w):
            if mask[v0, u0] and not seen[v0, u0]:
                stack, pts = [(v0, u0)], []
                seen[v0, u0] = True
                while stack:
                    v, u = stack.pop()
                    pts.append((u, v))
                    for dv, du in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nv, nu = v + dv, u + du
                        if 0 <= nv < h and 0 <= nu < w and mask[nv, nu] and not seen[nv, nu]:
                            seen[nv, nu] = True
                            stack.append((nv, nu))
                a = np.array(pts)
                out.append((len(pts), float(a[:, 0].mean()), float(a[:, 1].mean())))
    return out


def baseline_estimates(scene: V.Scene, img: np.ndarray) -> dict[str, tuple[float, float] | None]:
    comps = red_components(img)
    t = scene.target
    est: dict[str, tuple[float, float] | None] = {"oracle": (t.x, t.y), "image centre": (scene.cam_x, scene.cam_y)}
    est["largest red region"] = V.pixel_to_world(scene, comps[0][1], comps[0][2]) if comps else None
    if comps:
        comps.sort(key=lambda c: -c[0])
        big = max(comps, key=lambda c: c[0])
    sized = [c for c in comps if 60 <= c[0] <= 400]  # a vehicle is about 144 pixels, a red building about 1000
    est["red region of vehicle size"] = V.pixel_to_world(scene, sized[0][1], sized[0][2]) if sized else None
    if comps:
        est["largest red region"] = V.pixel_to_world(scene, max(comps, key=lambda c: c[0])[1], max(comps, key=lambda c: c[0])[2])
    return est


def score(scene: V.Scene, text: str) -> dict:
    env = envelope(scene)
    dec = vet(text, env, State(0.0, 0.0, 0.0, env.battery_wh))
    est = V.planned_target(text)
    t = scene.target
    err = None if est is None else math.hypot(est[0] - t.x, est[1] - t.y)
    has_orbit = False
    try:
        has_orbit = any(s.get("type") == "orbit" for s in json.loads(text)["steps"])
    except Exception:
        pass
    return {"approved": dec.approved, "rules": sorted(dec.rules), "estimate": est, "error_m": err, "has_orbit": has_orbit,
            **{f"ok@{int(T)}": bool(dec.approved and has_orbit and err is not None and err <= T) for T in TOLS}}


def classify(scene: V.Scene, r: dict) -> str:
    """Why a plan did not succeed at 15 m (or 'success')."""
    if r["ok@15"]:
        return "success"
    if r["estimate"] is None:
        return "no plan or no orbit/goto"
    if not r["approved"]:
        return "vetoed: " + ", ".join(r["rules"])
    ex, ey = r["estimate"]
    t = scene.target
    dx, dy, tx, ty = ex - scene.cam_x, ey - scene.cam_y, t.x - scene.cam_x, t.y - scene.cam_y
    for name, (a, b) in {"mirrored east-west": (-dx, dy), "mirrored north-south": (dx, -dy), "x and y swapped": (dy, dx), "rotated 180": (-dx, -dy)}.items():
        if math.hypot(a - tx, b - ty) <= 10:
            return f"axis or sign error ({name})"
    near = min(scene.objects, key=lambda o: math.hypot(o.x - ex, o.y - ey))
    d = math.hypot(near.x - ex, near.y - ey)
    if near is not t and d <= 12:
        return f"went to a distractor ({near.kind}, {near.color})"
    if d > 12:
        return "no object near the planned point (localisation off)"
    return "found the vehicle but the position is more than 15 m off"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--url")
    ap.add_argument("--label", default="vlm")
    ap.add_argument("--baselines-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--planner-url", help="twostep only: a separate (text) model that does the planning step")
    ap.add_argument("--mode", choices=["e2e", "ground", "twostep", "twostep-cot"], default="e2e", help="e2e: the model plans from the image; ground: the model only gives the vehicle's pixel position and code builds the plan; twostep: the model locates the vehicle, then plans from its own pixel position (arithmetic included); twostep-cot: the same, but the planner may write out its arithmetic in plain text before the JSON")
    args = ap.parse_args()

    scenes = V.from_json((ROOT / "eval" / "visual" / "scenes.json").read_text(encoding="utf-8"))
    dev, test = V.split(scenes)
    lock = (ROOT / "eval" / "visual" / "LOCK.txt").read_text()
    assert V.digest(dev) in lock and V.digest(test) in lock, "scenes no longer match eval/visual/LOCK.txt"
    chosen = dev if args.split == "dev" else test
    if args.limit:
        chosen = chosen[: args.limit]
    from PIL import Image

    imgs = {s.id: np.asarray(Image.open(ROOT / "eval" / "visual" / "frames" / f"{s.id}.png").convert("RGB")) for s in chosen}
    lines: list[str] = []

    def log(s=""):
        print(s, flush=True)
        lines.append(s)

    def table(name, results, scenes_):
        n = len(results)
        cells = [f"{int(T)} m {100 * sum(r[f'ok@{int(T)}'] for r in results) / n:5.1f}% [{100 * wilson(sum(r[f'ok@{int(T)}'] for r in results), n)[1]:4.1f},{100 * wilson(sum(r[f'ok@{int(T)}'] for r in results), n)[2]:5.1f}]" for T in TOLS]
        errs = [r["error_m"] for r in results if r["error_m"] is not None]
        log(f"  {name:<30} success at " + "   ".join(cells) + f"   median error {np.median(errs):6.1f} m ({len(errs)}/{n} with a position)" if errs else f"  {name:<30} no positions")

    log(f"Visual tasking, {args.split} half: {len(chosen)} scenes. Intervals are 95% Wilson; success needs a monitor-approved plan with an orbit centred within T of the true vehicle.\n")
    base = {s.id: baseline_estimates(s, imgs[s.id]) for s in chosen}
    for name in ("oracle", "red region of vehicle size", "largest red region", "image centre"):
        res = []
        for s in chosen:
            e = base[s.id][name]
            res.append(score(s, plan_json(*e) if e else "{}"))
        table(name + " (baseline)", res, chosen)

    if not args.baselines_only:
        planner = LlamaPlanner(args.url, with_limits=False, max_retries=0)
        rows, results = [], []
        for n, s in enumerate(chosen, 1):
            png = (ROOT / "eval" / "visual" / "frames" / f"{s.id}.png").read_bytes()
            content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}, {"type": "text", "text": V.request_text(s)}]
            t0 = time.monotonic()
            try:
                if args.mode == "e2e":
                    text, dt, toks = planner._chat([{"role": "system", "content": V.VISUAL_SYSTEM}, {"role": "user", "content": content}])
                elif args.mode in ("twostep", "twostep-cot"):
                    reply, dt1, toks1 = chat_plain(planner, [{"role": "user", "content": [content[0], {"type": "text", "text": V.GROUND_PROMPT}]}])
                    box = re.search(r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]", reply)
                    if box:
                        x1, y1, x2, y2 = (int(g) for g in box.groups())
                        told = f"\n\nThe red vehicle is at pixel (u, v) = ({(x1 + x2) // 2}, {(y1 + y2) // 2}) in the image."
                        if args.mode == "twostep-cot":
                            pl = LlamaPlanner(args.planner_url, with_limits=False) if args.planner_url else planner
                            ask = V.request_text(s) + told + "\n\nFirst work out the mission-frame x and y of the vehicle from its pixel position, showing the arithmetic step by step in a few lines. Then give the mission as one JSON object with exactly the keys id (any short string), lost_link and steps."
                            reply2, dt2, toks2 = chat_plain(pl, [{"role": "system", "content": V.VISUAL_SYSTEM.replace("Reply with one JSON object only.", "Work out coordinates in plain text first, then give one JSON object.")}, {"role": "user", "content": ask}], max_tokens=700)
                            text = extract_json(reply2)
                        else:
                            text, dt2, toks2 = (LlamaPlanner(args.planner_url, with_limits=False) if args.planner_url else planner)._chat([{"role": "system", "content": V.VISUAL_SYSTEM}, {"role": "user", "content": V.request_text(s) + told}])
                        dt, toks = dt1 + dt2, toks1 + toks2
                    else:
                        text, dt, toks = "{}", dt1, toks1
                else:
                    reply, dt, toks = chat_plain(planner, [{"role": "user", "content": [content[0], {"type": "text", "text": V.GROUND_PROMPT}]}])
                    box = re.search(r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]", reply)
                    if box:
                        x1, y1, x2, y2 = (int(g) for g in box.groups())
                        text = plan_json(*V.pixel_to_world(s, (x1 + x2) / 2, (y1 + y2) / 2))
                    else:
                        text = "{}"
                err = ""
            except Exception as e:  # recorded, scored as no plan
                text, dt, toks, err = "", time.monotonic() - t0, 0, f"{type(e).__name__}: {str(e)[:200]}"
            r = score(s, text)
            r["why"] = classify(s, r)
            rows.append({"scene": s.id, "text": text, "seconds": dt, "tokens": toks, "error": err, **r})
            results.append(r)
            print(f"[{n}/{len(chosen)}] {s.id} err {r['error_m']} {r['why']}", flush=True)
        out = ROOT / "results" / "visual" / f"{args.label}_{args.mode}_p{V.VISUAL_PROMPT_VERSION}_{args.split}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        log()
        table(args.label, results, chosen)
        counts: dict[str, int] = {}
        for r in results:
            counts[r["why"]] = counts.get(r["why"], 0) + 1
        log("\n  outcome at 15 m:")
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
            log(f"    {v:>3}  {k}")
        errs = sorted(r["error_m"] for r in results if r["error_m"] is not None)
        if errs:
            log(f"\n  position error (m): median {np.median(errs):.1f}, 25th {np.percentile(errs, 25):.1f}, 75th {np.percentile(errs, 75):.1f}, 90th {np.percentile(errs, 90):.1f}")
        log(f"  seconds per plan: median {np.median([r['seconds'] for r in rows]):.1f}, tokens median {int(np.median([r['tokens'] for r in rows]))}")
    (ROOT / "results" / f"visual_{args.split}{'_baselines' if args.baselines_only else ''}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
