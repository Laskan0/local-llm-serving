# Сборка из корня проекта: docker build -f downloader/Dockerfile ... .
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

WORKDIR /app
COPY downloader/requirements.txt /app/requirements.txt
COPY scripts/check_dependencies.py /app/scripts/check_dependencies.py
RUN python3 -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /app/requirements.txt \
    && python3 /app/scripts/check_dependencies.py /app/requirements.txt \
    && python3 -m pip check \
    && groupadd --gid 10001 app \
    && useradd --no-log-init --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app \
    && install -d -o 10001 -g 10001 -m 0750 /models /home/app/.cache

COPY config/model.json /app/config/model.json
COPY scripts/download_model.py /app/scripts/download_model.py
ENV HOME=/home/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/app/.cache/huggingface \
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_VERBOSITY=error

USER 10001:10001
ENTRYPOINT ["python3", "/app/scripts/download_model.py"]
CMD ["--manifest", "/app/config/model.json", "--output", "/models/qwen"]
