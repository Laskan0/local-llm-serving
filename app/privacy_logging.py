"""Безопасное логирование и аудит обращений к vLLM.

Тексты сообщений, аргументов и исключений не форматируются. Поля аудита
формируются внутри модуля. Тела запросов и ответов не буферизуются.
"""

import asyncio
import json
import logging
import math
import re
import time
import uuid
from datetime import datetime, timezone


ROUTES = frozenset(("/v1/chat/completions", "/v1/completions", "/v1/models", "/v1/embeddings"))
METHODS = frozenset(("GET", "POST", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS"))
OUTCOMES = frozenset(("completed", "error", "disconnected"))
LIFECYCLE = frozenset(("service_ready", "service_stopped", "service_startup_failed", "service_shutdown_failed"))


class SafeJsonFormatter(logging.Formatter):
    """Форматтер JSON с разрешёнными полями и без текстов сообщений."""
    def format(self, record):
        # Методы getMessage(), formatException(), str(msg) и str(exc) не вызываются.
        # Сообщения библиотек могут содержать запросы, заголовки и идентификаторы токенов.
        """Сформировать безопасную запись JSON.

        :param record: Запись logging с метаданными события.
        :return: JSON без текста сообщения, аргументов и исключения.
        :rtype: str
        """
        try:
            result = {"timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                      "level": logging.getLevelName(record.levelno) if record.levelno in (10, 20, 30, 40, 50) else "INFO"}
            event = getattr(record, "audit_event", None)
            if record.name == "project.audit" and event == "request_completed":
                request_id = getattr(record, "audit_id", "")
                status = getattr(record, "audit_status", 500)
                duration = getattr(record, "audit_duration_ms", 0.0)
                result.update({
                    "event": "request_completed",
                    "request_id": request_id if isinstance(request_id, str) and re.fullmatch(r"[0-9a-f]{32}", request_id) else "invalid",
                    "method": record.audit_method if record.audit_method in METHODS else "OTHER",
                    "route": record.audit_route if record.audit_route in ROUTES else "other",
                    "status": int(status) if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else 500,
                    "duration_ms": round(duration, 3) if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0 else 0,
                    "outcome": record.audit_outcome if record.audit_outcome in OUTCOMES else "error",
                })
            elif record.name == "project.audit" and event in LIFECYCLE:
                result["event"] = event
            else:
                name = record.name
                result.update({"event": "runtime_log",
                               "source": name if isinstance(name, str) and re.fullmatch(r"(?:vllm|uvicorn|torch)(?:\.[A-Za-z_][A-Za-z0-9_]*)*", name) else "python",
                               "line": record.lineno if type(record.lineno) is int else 0})
                if record.exc_info and record.exc_info[0]:
                    kind = record.exc_info[0].__name__
                    result["exception_type"] = kind if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", kind) else "Exception"
            return json.dumps(result, separators=(",", ":"), allow_nan=False)
        except Exception:
            # Стандартный handleError может вывести msg и args при ошибке форматирования.
            # Даже некорректная запись не должна раскрывать содержимое сообщения.
            return '{"event":"log_record_rejected","level":"ERROR"}'


class RequestAuditMiddleware:
    """ASGI-обёртка для аудита обращений без чтения тел сообщений."""
    def __init__(self, app):
        """Создать обёртку приложения.

        :param app: Исходное ASGI-приложение.
        """
        self.app = app
        self.logger = logging.getLogger("project.audit")

    async def __call__(self, scope, receive, send):
        """Передать ASGI-события приложению и записать итог обращения.

        :param scope: Описание соединения ASGI.
        :param receive: Функция получения входящих событий.
        :param send: Функция отправки исходящих событий.
        """
        if scope["type"] == "lifespan":
            events = {"lifespan.startup.complete": "service_ready", "lifespan.shutdown.complete": "service_stopped",
                      "lifespan.startup.failed": "service_startup_failed", "lifespan.shutdown.failed": "service_shutdown_failed"}

            async def lifecycle_send(message):
                """Записать событие жизненного цикла без текста ошибки.

                :param message: Исходящее событие ASGI.
                """
                event = events.get(message["type"])
                if event:
                    self.logger.info("lifecycle", extra={"audit_event": event})
                await send(message)

            return await self.app(scope, receive, lifecycle_send)
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope.get("path", ""), scope.get("method", "")
        if path in ("/health", "/metrics", "/ping") and method in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        audit_id = uuid.uuid4().hex
        start = time.monotonic()
        status, complete, disconnected, outcome = 500, False, False, "completed"

        async def observed_receive():
            """Получить событие и отметить отключение клиента.

            :return: Исходное событие ASGI.
            :rtype: dict
            """
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message):
            """Передать событие ответа и отметить его завершение.

            :param message: Исходящее событие ASGI.
            """
            nonlocal status, complete
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-audit-id"]
                message["headers"] = headers + [(b"x-audit-id", audit_id.encode("ascii"))]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                complete = True

        try:
            await self.app(scope, observed_receive, observed_send)
        except asyncio.CancelledError:
            outcome = "disconnected"
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            if outcome == "completed" and not complete:
                outcome = "disconnected"
            self.logger.info("request", extra={
                "audit_event": "request_completed", "audit_id": audit_id,
                "audit_method": method if method in METHODS else "OTHER",
                "audit_route": path if path in ROUTES else "other",
                "audit_status": status, "audit_duration_ms": (time.monotonic() - start) * 1000,
                "audit_outcome": outcome,
            })
