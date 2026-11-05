#!/usr/bin/env bash
# Диагностический запуск официального CPU-образа вне Kubernetes.
set -euo pipefail
cd "$(dirname "$0")/.."
image=$(python3 -c 'import json; print(json.load(open("config/runtime.json"))["diagnostic_image"])')
test -f .cache/models/qwen/.ready.json || {
  printf 'Model download has not completed.\n' >&2
  exit 1
}
mkdir -p .cache
if [[ ! -f .cache/smoke.env ]]; then
  (umask 077; python3 -c 'import secrets; print("VLLM_API_KEY=" + secrets.token_urlsafe(32))' > .cache/smoke.env)
fi
docker run -d --name kaspersky-vllm-smoke \
  --platform linux/arm64 --user 1000:1000 \
  --memory 6g --cpus 6 --shm-size 512m \
  --publish 127.0.0.1:8000:8000 \
  --env-file .cache/smoke.env \
  --env HOME=/tmp --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 \
  --env HF_HUB_DISABLE_TELEMETRY=1 \
  --env VLLM_CPU_OMP_THREADS_BIND=0-4 --env OMP_NUM_THREADS=5 \
  --mount "type=bind,source=$PWD/.cache/models/qwen,target=/models/qwen,readonly" \
  "$image" /models/qwen \
  --served-model-name qwen \
  --dtype bfloat16 --max-model-len 4096 \
  --max-num-seqs 4 --max-num-batched-tokens 1024 \
  --kv-cache-memory-bytes 268435456 --enforce-eager \
  --no-enable-log-requests --no-enable-log-outputs --no-enable-log-deltas
