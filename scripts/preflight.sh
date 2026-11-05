#!/usr/bin/env bash
# Проверка окружения без изменений и без вывода kubeconfig или секретов.
set -euo pipefail

printf 'Host architecture: '
uname -m
docker --version
docker info --format 'Engine: {{.OSType}}/{{.Architecture}} CPUs={{.NCPU}} RAM_bytes={{.MemTotal}}'
printf '\nKubernetes client:\n'
kubectl version --client=true
printf '\nCurrent Kubernetes context:\n'
kubectl config current-context || printf 'No Kubernetes context configured yet.\n'
printf '\nWorkspace disk space:\n'
df -h .
