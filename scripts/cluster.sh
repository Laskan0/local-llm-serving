#!/usr/bin/env bash
# Состояние Minikube и kubeconfig хранятся внутри проекта.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
export MINIKUBE_HOME="$PWD/.cache/minikube"
export KUBECONFIG="$PWD/.cache/kubeconfig"
mkdir -p "$MINIKUBE_HOME"
read -r profile kube_version cpu_count memory_mib < <(python3 - <<'PY'
import json
from pathlib import Path
c = json.loads(Path('config/local-cluster.json').read_text())
print(c['profile'], c['kubernetes_version'], c['cpus'], c['memory_mb'])
PY
)
profile=${LLM_CLUSTER_PROFILE:-$profile}
action=${1:-status}
case "$action" in
  start)
    docker_memory=$(docker info --format '{{.MemTotal}}')
    python3 - "$docker_memory" "$memory_mib" <<'PY'
import sys
if int(sys.argv[1]) < (int(sys.argv[2]) + 256) * 1024 * 1024:
    raise SystemExit('Insufficient Docker VM memory. Set Docker Desktop memory to 10 GiB before starting this cluster.')
PY
    .cache/bin/minikube start --profile "$profile" --driver docker \
      --container-runtime containerd --kubernetes-version "$kube_version" \
      --cpus "$cpu_count" --memory "$memory_mib" --embed-certs
    chmod 600 "$KUBECONFIG"
    ;;
  load-images)
    server_image=$(python3 -c 'import json; print(json.load(open("config/images.json"))["server"])')
    downloader_image=$(python3 -c 'import json; print(json.load(open("config/images.json"))["downloader"])')
    .cache/bin/minikube --profile "$profile" image load "$server_image"
    .cache/bin/minikube --profile "$profile" image load "$downloader_image"
    ;;
  status|stop)
    .cache/bin/minikube --profile "$profile" "$action"
    ;;
  *)
    printf 'Usage: bash scripts/cluster.sh {start|load-images|status|stop}\n' >&2
    exit 2
    ;;
esac
