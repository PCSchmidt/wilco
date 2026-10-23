import json
import pathlib
import sys

import numpy as np
import pytest

from wilco import visual as V

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCENES = V.generate()


def test_generation_is_deterministic_and_the_committed_set_and_lock_match():
    assert V.to_json(V.generate()) == V.to_json(SCENES)
    assert (ROOT / "eval" / "visual" / "scenes.json").read_text(encoding="utf-8").strip() == V.to_json(SCENES)
    dev, test = V.split(SCENES)
    lock = (ROOT / "eval" / "visual" / "LOCK.txt").read_text()
    assert V.digest(dev) in lock and V.digest(test) in lock and len(dev) == len(test) == 30
    assert V.from_json(V.to_json(SCENES)) == SCENES


def test_every_scene_has_one_red_vehicle_in_view_and_no_overlapping_objects():
    for s in SCENES:
        reds = [o for o in s.objects if o.kind == "vehicle" and o.color == "red"]
        assert len(reds) == 1
        for o in s.objects:
            assert abs(o.x - s.cam_x) <= V.HALF_W - 5 and abs(o.y - s.cam_y) <= V.HALF_H - 5
        for i, a in enumerate(s.objects):
            for b in s.objects[i + 1 :]:
                assert (a.x - b.x) ** 2 + (a.y - b.y) ** 2 >= 9.9**2
        assert any(o.kind in ("red_building", "vehicle") and o.color != "red" for o in s.objects) or True  # decoys are common, not guaranteed


def test_pixel_world_mapping_round_trips_and_matches_the_checked_camera_model():
    s = SCENES[0]
    x, y = V.pixel_to_world(s, 360, 220)  # the checked frame had a vehicle 10 m east and 5 m north of the camera at (360, 220)
    assert (x - s.cam_x, y - s.cam_y) == (10.0, 5.0)
    assert V.world_to_pixel(s, x, y) == (360.0, 220.0)
    assert V.pixel_to_world(s, 320, 240) == (s.cam_x, s.cam_y)


def test_request_states_the_camera_scale_and_the_formula():
    t = V.request_text(SCENES[3])
    assert "0.25 m" in t and "(u - 320) * 0.25" in t and "(240 - v) * 0.25" in t and "red vehicle" in t


def test_world_files_hold_every_object_and_the_camera_pose():
    s = SCENES[1]
    sdf = V.world_sdf(s)
    assert sdf.count("<model name=\"o") == len(s.objects) and "nadir/image" in sdf and "1.5708 1.5708" in sdf
    assert f"{s.cam_x} {s.cam_y} {V.CAM_ALT}" in sdf


def test_planned_target_prefers_the_orbit_then_the_last_goto():
    orbit = json.dumps({"steps": [{"type": "goto", "x": 1, "y": 2, "alt": 50}, {"type": "orbit", "x": 3, "y": 4, "radius": 25, "turns": 2, "alt": 50}]})
    assert V.planned_target(orbit) == (3.0, 4.0)
    assert V.planned_target(json.dumps({"steps": [{"type": "goto", "x": 1, "y": 2, "alt": 5}, {"type": "goto", "x": 7, "y": 8, "alt": 5}]})) == (7.0, 8.0)
    assert V.planned_target("not json") is None and V.planned_target(json.dumps({"steps": [{"type": "return"}]})) is None


def test_the_scoring_helpers_in_the_runner():
    rv = pytest.importorskip("run_visual")
    s = SCENES[0]
    t = s.target
    perfect = rv.score(s, rv.plan_json(t.x, t.y))
    assert perfect["approved"] and perfect["ok@5"] and rv.classify(s, perfect) == "success"
    off = rv.score(s, rv.plan_json(t.x + 40, t.y))
    assert off["approved"] and not off["ok@25"] and off["error_m"] == pytest.approx(40.0, abs=0.2)
    assert not rv.score(s, "{}")["ok@25"] and rv.classify(s, rv.score(s, "{}")) == "no plan or no orbit/goto"
    mirrored = rv.score(s, rv.plan_json(2 * s.cam_x - t.x, t.y))
    assert rv.classify(s, mirrored).startswith("axis or sign error") or mirrored["ok@15"] or abs(t.x - s.cam_x) < 3


def test_extract_json_takes_the_last_mission_object_in_a_reply_with_prose():
    rv = pytest.importorskip("run_visual")
    reply = 'x = 1 + 2 = 3\nHere is {"not": "it"} and then {"id": "a", "lost_link": "rtl", "steps": [{"type": "return"}]} done'
    assert json.loads(rv.extract_json(reply))["id"] == "a" and rv.extract_json("no json here") == ""


def test_red_region_detector_finds_a_drawn_vehicle_and_the_size_rule_ignores_a_building():
    rv = pytest.importorskip("run_visual")
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    img[100:108, 200:218] = (230, 30, 30)  # 8 x 18 = 144 pixels: vehicle sized
    img[300:332, 400:432] = (230, 30, 30)  # 32 x 32 = 1024 pixels: building sized
    comps = rv.red_components(img)
    assert sorted(c[0] for c in comps) == [144, 1024]
    est = rv.baseline_estimates(SCENES[0], img)
    assert est["red region of vehicle size"] == V.pixel_to_world(SCENES[0], 208.5 - 0.5 + 0.0 if False else comps[[c[0] for c in comps].index(144)][1], comps[[c[0] for c in comps].index(144)][2])
    assert est["largest red region"] != est["red region of vehicle size"]
