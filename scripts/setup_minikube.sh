#!/usr/bin/env bash
# Локальная установка Minikube для macOS ARM64 без изменения системных инструментов.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  printf 'This helper installs the tested macOS ARM64 build only.\n' >&2
  exit 1
fi
mkdir -p .cache/bin
if [[ ! -f .cache/bin/minikube ]]; then
  curl -fLsS --max-time 180 \
    https://github.com/kubernetes/minikube/releases/download/v1.39.0/minikube-darwin-arm64 \
    -o .cache/bin/minikube
fi
python3 - <<'PY'
import hashlib, json
from pathlib import Path
config = json.loads(Path('config/local-cluster.json').read_text())
path = Path('.cache/bin/minikube')
if hashlib.sha256(path.read_bytes()).hexdigest() != config['minikube_darwin_arm64_sha256']:
    raise SystemExit('Minikube SHA256 mismatch; do not run this binary')
path.chmod(0o755)
print('Minikube SHA256 verified')
PY
.cache/bin/minikube version
