"""Загрузка модели закреплённой ревизии с повторным использованием локальных файлов.

При наличии HF_TOKEN библиотека huggingface_hub читает его из окружения.
Значение токена не выводится в журнал.
"""

import argparse
import json
import os
from pathlib import Path

from huggingface_hub import snapshot_download


def main():
    """Проверить локальную модель либо скачать её и опубликовать маркер готовности.

    :raises RuntimeError: Если после скачивания отсутствуют обязательные файлы.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="/project/config/model.json")
    parser.add_argument("--output", default="/models/qwen")
    args = parser.parse_args()
    model = json.loads(Path(args.manifest).read_text())
    target = Path(args.output)
    marker = target / ".ready.json"
    expected = {"id": model["id"], "revision": model["revision"]}
    required = ("config.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors")
    if marker.is_file():
        try:
            ready = json.loads(marker.read_text()) == expected
        except (ValueError, OSError):
            ready = False
        if ready and all((target / name).is_file() for name in required):
            print("model_ready source=local", flush=True)
            return
    # Маркер готовности публикуется только после успешного завершения snapshot_download.
    # Локальные метаданные позволяют возобновить прерванную передачу.
    marker.unlink(missing_ok=True)
    print("model_download_started", flush=True)
    snapshot_download(
        repo_id=model["id"],
        revision=model["revision"],
        local_dir=str(target),
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja"],
        max_workers=2,
    )
    if not all((target / name).is_file() for name in required):
        raise RuntimeError("Downloaded model is missing required files")
    tmp = target / ".ready.json.tmp"
    tmp.write_text(json.dumps(expected) + "\n")
    os.replace(tmp, marker)
    print("model_ready source=download", flush=True)


if __name__ == "__main__":
    main()
