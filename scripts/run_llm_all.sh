#!/bin/bash
# Run the language-model planner conditions for one split, for both Qwen2.5 models, one server at a time.
#   bash scripts/run_llm_all.sh dev     # prompt and conditions may be tuned on this half
#   bash scripts/run_llm_all.sh test    # run once, after everything is frozen
set -u
SPLIT=$1
ROOT=/c/Dev/AIEdgePhysicalProjects
SRV=$ROOT/triagebox/tools/llama-b11700/llama-server.exe
PY=$ROOT/wilco/.venv/Scripts/python.exe
cd $ROOT/wilco
run_model() {
  LABEL=$1; MODEL=$2; PORT=$3
  $SRV -m "$MODEL" -ngl 99 -c 12288 --port $PORT --host 127.0.0.1 --no-webui --seed 0 > /tmp/llama_$LABEL.log 2>&1 &
  PID=$!
  until curl -s http://127.0.0.1:$PORT/health | grep -q ok; do sleep 2; done
  $PY scripts/run_llm.py --url http://127.0.0.1:$PORT --label $LABEL --split $SPLIT --limits 0 --retries 0 > /tmp/run_${LABEL}_${SPLIT}_L0.log 2>&1
  $PY scripts/run_llm.py --url http://127.0.0.1:$PORT --label $LABEL --split $SPLIT --limits 1 --retries 2 > /tmp/run_${LABEL}_${SPLIT}_L1.log 2>&1
  kill $PID 2>/dev/null; wait $PID 2>/dev/null
}
pkill -f llama-server 2>/dev/null; sleep 3
run_model q7b $ROOT/triagebox/models/qwen2.5-7b-instruct/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf 8081
run_model q1p5b $ROOT/triagebox/models/qwen2.5-1.5b-instruct/qwen2.5-1.5b-instruct-q8_0.gguf 8082
echo ALL_DONE
