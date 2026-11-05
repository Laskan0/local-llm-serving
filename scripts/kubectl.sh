#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
profile=$(python3 -c 'import json; print(json.load(open("config/local-cluster.json"))["profile"])')
exec kubectl --kubeconfig "$PWD/.cache/kubeconfig" --context "${LLM_CLUSTER_PROFILE:-$profile}" "$@"
