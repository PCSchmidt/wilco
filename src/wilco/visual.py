"""Frame-conditioned tasking: the request is only meaningful together with a camera image.

Each scene is a patch of ground seen by a downward-looking survey camera 80 m up (90 degree horizontal field of view,
640 x 480 pixels). It holds one red vehicle (the target), other vehicles in other colours, red non-vehicle objects,
grey buildings and trees. The request is "fly to the red vehicle in this image and circle it", so a planner has to
find the vehicle in the frame, turn its pixel position into metres in the mission frame, and plan the flight.

Camera model (checked against rendered frames: both test vehicles landed within 2 pixels of the prediction):
  north is up in the image and east is right; the image covers 160 m east-west by 120 m north-south centred on the
  camera's ground position; 0.25 m per pixel;  x_world = cam_x + (u - 320) / 4,  y_world = cam_y + (240 - v) / 4.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field

CAM_ALT = 80.0
W_PX, H_PX = 640, 480
M_PER_PX = 0.25
HALF_W, HALF_H = 80.0, 60.0
SEED = 20261023

COLORS = {
    "red": (0.9, 0.1, 0.1),
    "blue": (0.1, 0.2, 0.9),
    "white": (0.95, 0.95, 0.95),
    "yellow": (0.95, 0.85, 0.1),
    "black": (0.05, 0.05, 0.05),
    "gray": (0.5, 0.5, 0.5),
    "green": (0.05, 0.3, 0.05),
}


@dataclass(frozen=True)
class Obj:
    kind: str  # vehicle | red_building | building | tree
    color: str
    x: float
    y: float
    yaw: float
    size: tuple  # (length, width, height) for boxes, (radius, radius, height) for trees


@dataclass(frozen=True)
class Scene:
    id: str
    cam_x: float
    cam_y: float
    half: float  # fence half-size of the envelope the plan is judged against
    objects: tuple = field(default_factory=tuple)

    @property
    def target(self) -> Obj:
        return next(o for o in self.objects if o.kind == "vehicle" and o.color == "red")


def pixel_to_world(scene_or_cam, u: float, v: float) -> tuple[float, float]:
    cx, cy = (scene_or_cam.cam_x, scene_or_cam.cam_y) if hasattr(scene_or_cam, "cam_x") else scene_or_cam
    return cx + (u - W_PX / 2) * M_PER_PX, cy + (H_PX / 2 - v) * M_PER_PX


def world_to_pixel(scene: Scene, x: float, y: float) -> tuple[float, float]:
    return W_PX / 2 + (x - scene.cam_x) / M_PER_PX, H_PX / 2 - (y - scene.cam_y) / M_PER_PX


def _place(rng: random.Random, taken: list, margin: float, clearance: float) -> tuple[float, float]:
    for _ in range(500):
        dx = rng.uniform(-HALF_W + margin, HALF_W - margin)
        dy = rng.uniform(-HALF_H + margin, HALF_H - margin)
        if all(math.hypot(dx - tx, dy - ty) >= clearance for tx, ty in taken):
            taken.append((dx, dy))
            return dx, dy
    raise RuntimeError("could not place an object")


def make_scene(rng: random.Random, idx: int) -> Scene:
    half = rng.choice([600.0, 1000.0, 1500.0])
    cam_x, cam_y = round(rng.uniform(-0.6 * half, 0.6 * half), 1), round(rng.uniform(-0.6 * half, 0.6 * half), 1)
    taken: list = []
    objs: list[Obj] = []

    def add(kind, color, size, margin, clearance):
        dx, dy = _place(rng, taken, margin, clearance)
        objs.append(Obj(kind, color, round(cam_x + dx, 2), round(cam_y + dy, 2), round(rng.uniform(0, math.pi), 2), size))

    add("vehicle", "red", (4.5, 2.0, 1.5), 8.0, 14.0)
    for _ in range(rng.choice([0, 1, 1, 2, 3])):
        add("vehicle", rng.choice(["blue", "white", "yellow", "black"]), (4.5, 2.0, 1.5), 8.0, 14.0)
    for _ in range(rng.choice([0, 0, 1, 1, 2])):
        add("red_building", "red", (8.0, 8.0, 5.0), 10.0, 16.0)
    for _ in range(rng.choice([0, 1, 2, 3])):
        add("building", "gray", (rng.uniform(10, 16), rng.uniform(10, 16), rng.uniform(6, 12)), 12.0, 18.0)
    for _ in range(rng.randint(3, 8)):
        add("tree", "green", (rng.uniform(2.0, 3.0), 0.0, 8.0), 6.0, 10.0)
    rng.shuffle(objs)
    return Scene(f"V{idx:03d}", cam_x, cam_y, half, tuple(objs))


def generate(n: int = 60, seed: int = SEED) -> list[Scene]:
    rng = random.Random(seed)
    return [make_scene(rng, i) for i in range(n)]


def split(scenes: list[Scene]) -> tuple[list[Scene], list[Scene]]:
    return [s for i, s in enumerate(scenes) if i % 2 == 0], [s for i, s in enumerate(scenes) if i % 2 == 1]


def to_json(scenes: list[Scene]) -> str:
    return json.dumps([asdict(s) for s in scenes], sort_keys=True, separators=(",", ":"))


def digest(scenes: list[Scene]) -> str:
    return hashlib.sha256(to_json(scenes).encode()).hexdigest()


def from_json(text: str) -> list[Scene]:
    out = []
    for d in json.loads(text):
        d["objects"] = tuple(Obj(**{**o, "size": tuple(o["size"])}) for o in d["objects"])
        out.append(Scene(**d))
    return out


# ------------------------------------------------------------------------------------------------ rendering (SDF)
def world_sdf(scene: Scene) -> str:
    def box(name, o: Obj):
        r, g, b = COLORS[o.color]
        return (
            f'<model name="{name}"><static>true</static><pose>{o.x} {o.y} {o.size[2] / 2} 0 0 {o.yaw}</pose><link name="l"><visual name="v"><geometry>'
            f"<box><size>{o.size[0]} {o.size[1]} {o.size[2]}</size></box></geometry><material><ambient>{r} {g} {b} 1</ambient><diffuse>{r} {g} {b} 1</diffuse></material></visual></link></model>"
        )

    def tree(name, o: Obj):
        r, g, b = COLORS[o.color]
        return (
            f'<model name="{name}"><static>true</static><pose>{o.x} {o.y} {o.size[2] / 2} 0 0 0</pose><link name="l"><visual name="v"><geometry>'
            f"<cylinder><radius>{o.size[0]}</radius><length>{o.size[2]}</length></cylinder></geometry><material><ambient>{r} {g} {b} 1</ambient><diffuse>{r} {g} {b} 1</diffuse></material></visual></link></model>"
        )

    body = "\n    ".join((tree if o.kind == "tree" else box)(f"o{i}", o) for i, o in enumerate(scene.objects))
    return f"""<?xml version="1.0"?>
<sdf version="1.9">
  <world name="survey">
    <physics name="p" type="ode"><max_step_size>0.01</max_step_size><real_time_factor>1.0</real_time_factor></physics>
    <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
    <plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors"><render_engine>ogre2</render_engine></plugin>
    <plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
    <light type="directional" name="sun"><cast_shadows>true</cast_shadows><pose>0 0 100 0 0 0</pose><diffuse>0.9 0.9 0.9 1</diffuse><specular>0.2 0.2 0.2 1</specular><direction>-0.4 0.2 -0.9</direction></light>
    <model name="ground"><static>true</static><link name="l"><visual name="v"><geometry><plane><normal>0 0 1</normal><size>4000 4000</size></plane></geometry><material><ambient>0.35 0.5 0.25 1</ambient><diffuse>0.35 0.5 0.25 1</diffuse></material></visual></link></model>
    {body}
    <model name="nadir_cam"><static>true</static><pose>{scene.cam_x} {scene.cam_y} {CAM_ALT} 0 1.5708 1.5708</pose><link name="l"><sensor name="cam" type="camera"><camera><horizontal_fov>1.5708</horizontal_fov><image><width>{W_PX}</width><height>{H_PX}</height></image><clip><near>0.1</near><far>500</far></clip></camera><always_on>1</always_on><update_rate>5</update_rate><topic>nadir/image</topic></sensor></link></model>
  </world>
</sdf>
"""


# ------------------------------------------------------------------------------------------------ the request
REQUEST = (
    "This image is from a downward-looking survey camera at x = {cx:.0f} m, y = {cy:.0f} m (mission frame, x east, y north), "
    "{alt:.0f} m above the ground. North is up in the image and east is to the right. The image is {w} x {h} pixels and covers "
    "{cw:.0f} m east-west by {ch:.0f} m north-south, centred on the camera's ground position, so one pixel is {mpp} m.\n\n"
    "To turn a pixel (u, v) (u counted from the left edge, v from the top edge) into the mission frame: x = {cx:.0f} + (u - 320) * 0.25 and y = {cy:.0f} + (240 - v) * 0.25.\n\n"
    "Request: fly to the red vehicle in this image and circle it at a radius of 25 m for 2 turns at 50 m altitude, then return home."
)

VISUAL_SYSTEM = """You plan drone missions from a camera image and a request. Reply with one JSON object only.

Coordinates are metres in a local frame: x is east, y is north, the origin is the take-off point (home). Altitude is metres above home.

Step types:
- takeoff {alt}: climb straight up from home.
- goto {x, y, alt}: fly to a point.
- orbit {x, y, radius, turns, alt}: circle the point (x, y).
- report {cls}: a standing instruction to report any detection of class cls (for example "vehicle").
- return {}: fly home and land.

A mission has exactly one takeoff as its first step, a return as its last step, and nothing after the return. Set lost_link to "rtl".
Include only the steps the request asks for. Find the object in the image first, then work out its position in the mission frame."""

GROUND_PROMPT = "Locate the small red car (a vehicle about 4.5 m long, not a red building and not any other object) in this image. Output its bounding box in JSON."


def request_text(scene: Scene) -> str:
    return REQUEST.format(cx=scene.cam_x, cy=scene.cam_y, alt=CAM_ALT, w=W_PX, h=H_PX, cw=2 * HALF_W, ch=2 * HALF_H, mpp=M_PER_PX)


VISUAL_PROMPT_VERSION = 2  # 1: the text planner's system prompt and no formula; 2: visual system prompt and the pixel formula


def planned_target(plan_text: str) -> tuple[float, float] | None:
    """Where the plan goes to look at the vehicle: the centre of its first orbit, else its last goto, else None."""
    try:
        steps = json.loads(plan_text)["steps"]
    except (ValueError, KeyError, TypeError):
        return None
    for s in steps:
        if isinstance(s, dict) and s.get("type") == "orbit":
            return float(s["x"]), float(s["y"])
    gotos = [s for s in steps if isinstance(s, dict) and s.get("type") == "goto"]
    return (float(gotos[-1]["x"]), float(gotos[-1]["y"])) if gotos else None
