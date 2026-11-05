"""Создание Kubernetes Secret без передачи значений через аргументы процесса или Git."""

import argparse
import base64
import json
import os
import secrets
import subprocess
from pathlib import Path


def main():
    """Подготовить API-токен и передать секреты Kubernetes через stdin.

    :raises SystemExit: При некорректном токене или ошибке создания Secret.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--hf-token-file", help="Optional private file containing HF token")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    cache = root / ".cache"
    cache.mkdir(exist_ok=True)
    token_file = cache / "k8s-api.env"
    if not token_file.exists():
        descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write("VLLM_API_KEY=" + secrets.token_urlsafe(32) + "\n")
    token_file.chmod(0o600)
    api_token = token_file.read_text().strip().removeprefix("VLLM_API_KEY=")
    if not api_token or "\n" in api_token:
        raise SystemExit("Invalid local API token file")
    hf_token = Path(args.hf_token_file).read_text().strip() if args.hf_token_file else os.environ.get("HF_TOKEN", "")
    items = []
    for name, token in (("vllm-api", api_token), ("hf-access", hf_token)):
        items.append({"apiVersion": "v1", "kind": "Secret",
                      "metadata": {"name": name, "namespace": "kaspersky-llm"},
                      "type": "Opaque", "data": {"token": base64.b64encode(token.encode()).decode()}})
    command = ["bash", str(root / "scripts/kubectl.sh"), "apply", "-f", "-"]
    # Значения Secret передаются только через stdin kubectl, без аргументов командной строки.
    result = subprocess.run(command, input=json.dumps({"apiVersion": "v1", "kind": "List", "items": items}),
                            text=True, capture_output=True)
    if result.returncode:
        raise SystemExit("Secret creation failed; check namespace, cluster access and permissions")
    print("Kubernetes API and HF Secrets configured (values not displayed)")


if __name__ == "__main__":
    main()
