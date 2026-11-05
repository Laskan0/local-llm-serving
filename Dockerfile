# CPU-сборка vLLM и нативные библиотеки наследуются от проверенного базового образа.
# Digest закрепляет базовый образ, requirements.txt — все Python-пакеты.
# CPU-пакеты не подбираются из общего индекса PyPI.
FROM vllm/vllm-openai-cpu:v0.27.1-arm64@sha256:e6745d7ba6610f637c6f22fc06cd730342e50245b6c46767235600483adfbbde

USER root
WORKDIR /app
COPY requirements.txt /app/requirements.txt
COPY scripts/check_dependencies.py /app/scripts/check_dependencies.py
RUN uv pip install --offline --no-index --no-deps -r /app/requirements.txt \
    && python3 /app/scripts/check_dependencies.py /app/requirements.txt \
    && uv pip check \
    && groupadd --gid 10001 app \
    && useradd --no-log-init --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app \
    && install -d -o 10001 -g 10001 -m 0750 /models /home/app/.cache

COPY config/model.json config/inference.json /app/config/
COPY scripts/server_entrypoint.py /app/scripts/server_entrypoint.py
COPY app/privacy_logging.py /app/privacy_logging.py
COPY config/logging.json /app/config/logging.json

ENV HOME=/home/app \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    VLLM_LOGGING_CONFIG_PATH=/app/config/logging.json \
    HF_HOME=/home/app/.cache/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    VLLM_CPU_OMP_THREADS_BIND=0-4 \
    OMP_NUM_THREADS=5

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python3", "/app/scripts/server_entrypoint.py"]
CMD []
