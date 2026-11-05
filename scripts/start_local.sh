#!/usr/bin/env bash
# Локальный запуск собственного образа сервера без Kubernetes.
set -euo pipefail
cd "$(dirname "$0")/.."
test -f .cache/models/qwen/.ready.json || {
  printf 'Model is not ready. Run the downloader before starting the server.\n' >&2
  exit 1
}
if docker container inspect kaspersky-vllm-local >/dev/null 2>&1; then
  printf 'Container kaspersky-vllm-local already exists. Check its status before restarting.\n' >&2
  exit 1
fi
mkdir -p .cache
if [[ ! -f .cache/smoke.env ]]; then
  (umask 077; python3 -c 'import secrets; print("VLLM_API_KEY=" + secrets.token_urlsafe(32))' > .cache/smoke.env)
fi
docker run -d --name kaspersky-vllm-local \
  --label project=kaspersky-llm --label stage=2 \
  --platform linux/arm64 --memory 6g --cpus 6 --shm-size 512m \
  --cap-drop ALL --security-opt no-new-privileges \
  --publish 127.0.0.1:8000:8000 \
  --env-file .cache/smoke.env \
  --mount "type=bind,source=$PWD/.cache/models/qwen,target=/models/qwen,readonly" \
  "$(python3 -c 'import json; print(json.load(open("config/images.json"))["server"])')"
