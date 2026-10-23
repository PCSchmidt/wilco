#!/bin/bash
# Render one camera frame per scene world into /wilco/eval/visual/frames/<id>.png (runs in the simulator container).
pip install -q pillow 2>&1 | tail -1
mkdir -p /wilco/eval/visual/frames
for w in /wilco/sim/visual/worlds/*.sdf; do
  id=$(basename $w .sdf)
  [ -f /wilco/eval/visual/frames/$id.png ] && continue
  nohup gz sim -s -r --headless-rendering $w > /tmp/gz_$id.log 2>&1 &
  for i in $(seq 1 60); do gz topic -l 2>/dev/null | grep -q nadir/image && break; sleep 1; done
  sleep 2
  timeout 60 gz topic -e -n 1 -t /nadir/image --json-output > /tmp/frame_$id.json 2>/dev/null
  python3 - "$id" <<'PY'
import sys, json, base64
from PIL import Image
i = sys.argv[1]
m = json.loads(open(f"/tmp/frame_{i}.json").read().strip().splitlines()[0])
Image.frombytes("RGB", (m["width"], m["height"]), base64.b64decode(m["data"])).save(f"/wilco/eval/visual/frames/{i}.png", optimize=True)
PY
  pkill -f "gz sim" ; sleep 1
  echo "$id done"
done
echo RENDER_DONE
