"""Проверки приватности форматтера логов и ASGI-аудита."""
import asyncio
import io
import json
import logging
import sys
import os
import subprocess
import unittest
from pathlib import Path
from http import HTTPStatus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from privacy_logging import RequestAuditMiddleware, SafeJsonFormatter


MARKER = "PRIVATE_CANARY_do_not_log_4a93f81b"


class FormatterTests(unittest.TestCase):
    def test_discarded_loggers_cannot_fall_back_to_raw_stderr(self):
        root = Path(__file__).resolve().parent.parent
        config = root / "config/logging.json"
        if not config.exists():
            config = Path("/app/config/logging.json")
        module_dir = Path(sys.modules[SafeJsonFormatter.__module__].__file__).parent
        code = ("import json,logging,logging.config; "
                f"logging.config.dictConfig(json.load(open({str(config)!r}))); "
                f"logging.getLogger('vllm.logging_utils.dump_input').critical({MARKER!r}); "
                f"logging.getLogger('uvicorn.access').critical({MARKER!r}); "
                f"logging.getLogger('vllm.test').error({MARKER!r})")
        run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=str(module_dir)), timeout=15)
        self.assertEqual(run.returncode, 0)
        self.assertNotIn(MARKER, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)["event"], "runtime_log")
        self.assertEqual(run.stderr, "")

    def test_message_arguments_and_exception_text_are_never_formatted(self):
        class HostileMessage:
            def __str__(self):
                raise AssertionError("message must not be formatted")

        try:
            raise ValueError(MARKER)
        except ValueError:
            record = logging.LogRecord("vllm.engine", logging.ERROR, __file__, 12,
                                       HostileMessage(), (MARKER,), sys.exc_info())
        text = SafeJsonFormatter().format(record)
        self.assertNotIn(MARKER, text)
        self.assertEqual(json.loads(text)["exception_type"], "ValueError")

    def test_malformed_record_cannot_trigger_logging_handle_error_dump(self):
        record = logging.LogRecord("project.audit", logging.INFO, __file__, 1, MARKER, (), None)
        record.audit_event = "request_completed"
        record.audit_method = [MARKER]
        text = SafeJsonFormatter().format(record)
        self.assertNotIn(MARKER, text)
        self.assertEqual(json.loads(text)["event"], "log_record_rejected")


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.logger = logging.getLogger("project.audit")
        self.previous = (self.logger.handlers[:], self.logger.level, self.logger.propagate)
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(SafeJsonFormatter())
        self.logger.handlers = [handler]
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    def tearDown(self):
        self.logger.handlers, level, self.logger.propagate = self.previous
        self.logger.setLevel(level)

    def scope(self, path="/v1/chat/completions"):
        return {"type": "http", "method": "POST", "path": path,
                "headers": [(b"authorization", MARKER.encode()), (b"x-request-id", MARKER.encode())],
                "query_string": MARKER.encode()}

    def records(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines()]

    async def test_stream_passes_through_and_logs_only_after_completion(self):
        messages = []

        async def receive():
            return {"type": "http.request", "body": MARKER.encode()}

        async def send(message):
            messages.append(message)

        async def app(scope, receive, send):
            self.assertEqual((await receive())["body"], MARKER.encode())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": MARKER.encode(), "more_body": True})
            self.assertEqual(self.records(), [])
            await send({"type": "http.response.body", "body": b"done", "more_body": False})

        await RequestAuditMiddleware(app)(self.scope(), receive, send)
        self.assertEqual(len(self.records()), 1)
        event = self.records()[0]
        self.assertEqual(event["status"], 200)
        self.assertEqual(event["outcome"], "completed")
        self.assertNotIn(MARKER, self.stream.getvalue())
        self.assertEqual(messages[1]["body"], MARKER.encode())
        self.assertEqual(dict(messages[0]["headers"])[b"x-audit-id"].decode(), event["request_id"])

    async def test_unhandled_error_records_one_safe_500_event(self):
        async def app(scope, receive, send):
            raise ValueError(MARKER)

        with self.assertRaises(ValueError):
            await RequestAuditMiddleware(app)(self.scope(), None, None)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0]["status"], 500)
        self.assertEqual(self.records()[0]["outcome"], "error")
        self.assertNotIn(MARKER, self.stream.getvalue())

    async def test_cancellation_records_disconnect_without_payload(self):
        async def app(scope, receive, send):
            raise asyncio.CancelledError(MARKER)

        with self.assertRaises(asyncio.CancelledError):
            await RequestAuditMiddleware(app)(self.scope(), None, None)
        self.assertEqual(self.records()[0]["outcome"], "disconnected")
        self.assertNotIn(MARKER, self.stream.getvalue())

    async def test_unknown_path_is_not_logged_verbatim_and_401_is_audited(self):
        async def send(message):
            pass

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": MARKER.encode()})

        await RequestAuditMiddleware(app)(self.scope("/v1/" + MARKER), None, send)
        self.assertEqual(self.records()[0]["route"], "other")
        self.assertEqual(self.records()[0]["status"], 401)
        self.assertNotIn(MARKER, self.stream.getvalue())

    async def test_startup_failure_does_not_log_lifespan_message(self):
        async def send(message):
            pass

        async def app(scope, receive, send):
            await send({"type": "lifespan.startup.failed", "message": MARKER})

        await RequestAuditMiddleware(app)({"type": "lifespan"}, None, send)
        self.assertEqual(self.records()[0]["event"], "service_startup_failed")
        self.assertNotIn(MARKER, self.stream.getvalue())

    async def test_http_status_enum_and_disconnect_after_complete_response(self):
        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": HTTPStatus.BAD_REQUEST, "headers": []})
            await send({"type": "http.response.body", "body": MARKER.encode()})
            await receive()

        await RequestAuditMiddleware(app)(self.scope(), receive, send)
        self.assertEqual(self.records()[0]["status"], 400)
        self.assertEqual(self.records()[0]["outcome"], "completed")
        self.assertNotIn(MARKER, self.stream.getvalue())


if __name__ == "__main__":
    unittest.main()
