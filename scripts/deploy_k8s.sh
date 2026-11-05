#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/kubectl.sh apply -f k8s/namespace.yaml
python3 scripts/create_k8s_secrets.py "$@"
bash scripts/kubectl.sh apply -k k8s
bash scripts/kubectl.sh -n kaspersky-llm rollout status deployment/vllm --timeout=900s
