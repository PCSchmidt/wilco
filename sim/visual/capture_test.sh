#!/bin/bash
cd /tmp
pip install -q pillow 2>&1 | tail -1
nohup gz sim -s -r --headless-rendering /wilco/sim/visual/test_world.sdf > /tmp/gz.log 2>&1 &
for i in $(seq 1 60); do gz topic -l 2>/dev/null | grep -q nadir/image && break; sleep 2; done
gz topic -l | head
timeout 60 gz topic -e -n 1 -t /nadir/image --json-output > /tmp/frame.json 2>/tmp/frame.err
ls -la /tmp/frame.json
python3 - <<'PY'
import json, base64
from PIL import Image
txt=open("/tmp/frame.json").read().strip().splitlines()[0]
m=json.loads(txt)
print({k:(v if k!="data" else len(v)) for k,v in m.items() if k!="header"})
w,h=m["width"],m["height"]; raw=base64.b64decode(m["data"])
print("bytes",len(raw),"expect",w*h*3)
im=Image.frombytes("RGB",(w,h),raw); im.save("/wilco/sim/visual/test_frame.png")
import numpy as np
a=np.asarray(im); import numpy as np
a=np.asarray(im).astype(int)
red=(a[...,0]>180)&(a[...,1]<90)&(a[...,2]<90); blue=(a[...,2]>150)&(a[...,0]<110)
for name,m in (("red",red),("blue",blue)):
    ys,xs=np.nonzero(m); print(name,"centroid pixel (u,v) = (%.0f, %.0f)"%(xs.mean(),ys.mean()),"pixels",m.sum())
print("expected red (360,220) blue (200,280)")
print("mean rgb",a.reshape(-1,3).mean(0).round(1),"unique colours",len(np.unique(a.reshape(-1,3),axis=0)))
PY
pkill -f "gz sim" || true
