#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
server_image=$(python3 -c 'import json; print(json.load(open("config/images.json"))["server"])')
downloader_image=$(python3 -c 'import json; print(json.load(open("config/images.json"))["downloader"])')
docker build --platform linux/arm64 --tag "$server_image" --file Dockerfile .
docker build --platform linux/arm64 --tag "$downloader_image" --file downloader/Dockerfile .
