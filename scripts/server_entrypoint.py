"""Запуск vLLM из подключённого JSON-файла без передачи секретов в аргументах."""

import json
import os
from pathlib import Path


def main():
    """Проверить параметры и заменить процесс сервером vLLM.

    :raises SystemExit: При запуске от root, отсутствии токена или неверной конфигурации.
    """
    if os.getuid() == 0:
        raise SystemExit("Refusing to run the inference server as root")
    if not os.environ.get("VLLM_API_KEY", "").strip():
        raise SystemExit("VLLM_API_KEY must be supplied at runtime")
    config_path = os.environ.get("INFERENCE_CONFIG", "/app/config/inference.json")
    config = json.loads(Path(config_path).read_text())
    expected_keys = {
        "model_path", "served_model_name", "dtype", "max_model_len", "max_num_seqs",
        "max_num_batched_tokens", "kv_cache_memory_bytes", "enforce_eager", "port",
    }
    if config.keys() != expected_keys:
        raise SystemExit("Inference config must contain exactly the documented fields")
    model = Path(config["model_path"])
    if not model.is_absolute() or not model.is_dir():
        raise SystemExit("Model path must be an existing absolute directory")
    manifest = json.loads(Path("/app/config/model.json").read_text())
    marker = model / ".ready.json"
    if not marker.is_file() or json.loads(marker.read_text()) != manifest:
        raise SystemExit("Model snapshot has not been prepared for the pinned revision")
    if not config["served_model_name"] or not isinstance(config["served_model_name"], str):
        raise SystemExit("served_model_name must be a nonempty string")
    if config["dtype"] not in ("bfloat16", "float16", "float32"):
        raise SystemExit("Unsupported dtype in inference config")
    if type(config["enforce_eager"]) is not bool:
        raise SystemExit("enforce_eager must be a boolean")
    numeric = ("max_model_len", "max_num_seqs", "max_num_batched_tokens", "kv_cache_memory_bytes", "port")
    if any(type(config[k]) is not int or config[k] <= 0 for k in numeric):
        raise SystemExit("Inference limits must be positive integers")
    if config["port"] > 65535:
        raise SystemExit("Invalid server port")
    command = ["vllm", "serve", str(model), "--host", "0.0.0.0"]
    for key in ("served_model_name", "dtype", *numeric):
        command.extend(["--" + key.replace("_", "-"), str(config[key])])
    command.append("--enforce-eager" if config["enforce_eager"] else "--no-enforce-eager")
    command.extend(["--no-enable-log-requests", "--no-enable-log-outputs", "--no-enable-log-deltas"])
    command.extend(["--no-log-error-stack", "--disable-uvicorn-access-log",
                    "--middleware", "privacy_logging.RequestAuditMiddleware",
                    "--log-config-file", "/app/config/logging.json"])
    # Отладочные настройки сбрасываются до импорта vLLM: они могут раскрывать запросы и ответы.
    os.environ.update({"VLLM_DEBUG_LOG_API_SERVER_RESPONSE": "false", "VLLM_SERVER_DEV_MODE": "0",
                       "VLLM_TRACE_FUNCTION": "0", "VLLM_LOG_MODEL_INSPECTION": "0",
                       "VLLM_LOGGING_LEVEL": "INFO", "VLLM_LOGGING_CONFIG_PATH": "/app/config/logging.json"})
    # Токены остаются в окружении процесса; окружение и данные запроса не сериализуются.
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
