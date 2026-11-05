"""Проверка ответа локального API без вывода токена, запроса и ответа модели."""

import json
import argparse
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    """Дождаться готовности API, проверить ответ и сохранить метаданные.

    :raises RuntimeError: Если сервер не готов или ответ пуст.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=int, default=180)
    parser.add_argument("--output", default=".cache/reports/smoke.json", help="Result JSON path")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token-file", help="Private env file containing VLLM_API_KEY")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    token_file = Path(args.token_file) if args.token_file else root / ".cache/smoke.env"
    key = token_file.read_text().strip().split("=", 1)[1]
    base_url = args.base_url.rstrip("/")
    deadline = time.monotonic() + args.wait_seconds
    while True:
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=3) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Server did not become ready within the allotted time")
        time.sleep(2)
    request = urllib.request.Request(
        base_url + "/v1/chat/completions",
        data=json.dumps({
            "model": "qwen",
            "messages": [{"role": "user", "content": "Reply with one word: ready"}],
            "max_tokens": 16,
            "temperature": 0,
        }).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    start = time.monotonic()
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
        status = response.status
    content = result["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Model response was empty")
    summary = {
        "status": status,
        "nonempty_response": True,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "usage": result.get("usage"),
    }
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
