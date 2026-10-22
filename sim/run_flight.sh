#!/bin/bash
# Fly one plan in PX4 SITL (built-in physics). Usage: run_flight.sh <plan.json> <scenario.json> <out.json> [speed_factor]
# Runs inside the simulator container with the PX4 volume at /work and this repo at /wilco.
set -u
PLAN=$1; SCEN=$2; OUT=$3; SPEED=${4:-5}
cd /work/PX4-Autopilot
pkill -f "bin/px4" >/dev/null 2>&1 || true
rm -rf /work/PX4-Autopilot/build/px4_sitl_default/rootfs/eeprom /work/PX4-Autopilot/build/px4_sitl_default/rootfs/parameters*.bson 2>/dev/null
export PX4_SIM_SPEED_FACTOR=$SPEED
nohup make px4_sitl sihsim_quadx > /work/px4.log 2>&1 &
PX4PID=$!
sleep 12
timeout 1500 python3 /wilco/sim/executor.py "$PLAN" "$SCEN" "$OUT"
RC=$?
pkill -f "bin/px4" >/dev/null 2>&1 || true
pkill -f "make px4_sitl" >/dev/null 2>&1 || true
sleep 1
exit $RC
