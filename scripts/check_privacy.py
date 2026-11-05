"""Проверка авторизации и приватности логов работающего сервиса Kubernetes.

Контрольные тексты и токены остаются в памяти. В отчёт попадают только
статусы, метаданные аудита и результаты проверок. kubectl logs объединяет
stdout и stderr контейнера.
"""

import argparse
import collections
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def kubectl(*arguments):
    """Выполнить команду kubectl в локальном контексте проекта.

    :param arguments: Аргументы команды без имени kubectl.
    :return: Стандартный вывод команды.
    :rtype: str
    """
    result = subprocess.run(["bash", str(ROOT / "scripts/kubectl.sh"), *arguments],
                            capture_output=True, text=True, timeout=45, cwd=ROOT)
    if result.returncode:
        raise RuntimeError("kubectl operation failed; no response payload displayed")
    return result.stdout


def main():
    """Выполнить HTTP-сценарии и проверить логи на тексты и секреты."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--output", default=".cache/reports/privacy.json")
    args = parser.parse_args()
    key = (ROOT / ".cache/k8s-api.env").read_text().strip().split("=", 1)[1]
    pod_list = json.loads(kubectl("-n", "kaspersky-llm", "get", "pods", "-l", "app=vllm", "-o", "json"))["items"]
    pods = [p for p in pod_list if not p["metadata"].get("deletionTimestamp") and
            any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", []))]
    if len(pods) != 1:
        raise SystemExit("Expected one Ready Pod before privacy tests")
    pod = pods[0]["metadata"]["name"]
    run_id = uuid.uuid4().hex
    markers = {name: name.upper() + "_PRIVATE_" + run_id
               for name in ("input", "output", "stream", "header", "query", "path", "error", "malformed", "wrong_auth")}
    since = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    cases = []

    def request(name, data=None, path="/v1/chat/completions", auth="correct", expected=(200,), stream=False):
        """Выполнить контрольный запрос и сохранить безопасные метаданные.

        :param name: Имя сценария.
        :param data: Тело запроса или готовые байты.
        :param path: Путь API.
        :param auth: Режим передачи токена.
        :param expected: Допустимые коды HTTP.
        :param stream: Признак потокового ответа.
        :return: Тело ответа для проверки в памяти и идентификатор аудита.
        """
        headers = {"Content-Type": "application/json", "X-Request-Id": markers["header"],
                   "User-Agent": markers["header"]}
        if auth != "missing":
            headers["Authorization"] = "Bearer " + (key if auth == "correct" else markers["wrong_auth"])
        payload = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
        url = args.base_url.rstrip("/") + path
        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=180)
        except urllib.error.HTTPError as error:
            response = error
        parts, saw_done, chunks = [], False, 0
        with response:
            status = response.code
            audit_id = response.headers.get("X-Audit-Id", "")
            if stream and status == 200:
                for line in response:
                    if not line.startswith(b"data:"):
                        continue
                    value = line[5:].strip()
                    if value == b"[DONE]":
                        saw_done = True
                        # Поток HTTP читается до EOF. Закрытие сразу после
                        # [DONE] может отменить ASGI до отправки последнего пустого кадра тела.
                        continue
                    item = json.loads(value)
                    if item.get("error"):
                        raise RuntimeError("Streaming API returned an error")
                    for choice in item.get("choices", []):
                        content = choice.get("delta", {}).get("content")
                        if content:
                            chunks += 1
                            parts.append(content)
            else:
                raw = response.read()
                if status == 200 and isinstance(data, dict) and "messages" in data:
                    item = json.loads(raw)
                    parts.append(item["choices"][0]["message"]["content"] or "")
        content = "".join(parts)
        marker_seen = markers["stream" if stream else "output"] in content if status == 200 and parts else None
        passed = status in expected and bool(audit_id)
        if name == "normal":
            passed &= marker_seen is True
        if stream:
            passed &= saw_done and chunks > 0 and marker_seen is True
        cases.append({"name": name, "status": status, "expected_statuses": list(expected),
                      "audit_id": audit_id, "passed": bool(passed),
                      "response_marker_seen": marker_seen, "stream_complete": saw_done if stream else None,
                      "stream_content_chunks": chunks if stream else None})
        print(f"{'PASS' if passed else 'FAIL'} HTTP {name}: status={status}", flush=True)

    def body(output, streaming=False):
        """Сформировать контрольное тело запроса.

        :param output: Контрольный текст ответа.
        :param streaming: Признак потоковой генерации.
        :return: Параметры запроса.
        :rtype: dict
        """
        return {"model": "qwen", "messages": [{"role": "user", "content": markers["input"] +
                "\nReturn only this exact string, without quotes or explanation: " + output}],
                "max_tokens": 96, "temperature": 0, "stream": streaming}

    query = "?trace=" + urllib.parse.quote(markers["query"])
    request("normal", body(markers["output"]), path="/v1/chat/completions" + query)
    request("streaming", body(markers["stream"], True), stream=True)
    request("invalid_schema", {"model": "qwen", "messages": markers["error"]}, expected=(400, 422))
    unknown = body(markers["output"])
    unknown["model"] = markers["error"]
    request("unknown_model", unknown, expected=(404,))
    request("malformed_json", b'{"messages": [' + markers["malformed"].encode(), expected=(400, 422))
    request("wrong_token", body(markers["output"]), auth="wrong", expected=(401,))
    request("missing_token", body(markers["output"]), auth="missing", expected=(401,))
    request("unknown_path", path="/v1/" + markers["path"] + query, expected=(404,))

    records, raw_logs = [], ""
    deadline = time.monotonic() + 8
    while True:
        raw_logs = kubectl("-n", "kaspersky-llm", "logs", pod, "-c", "server", "--since-time=" + since)
        records = []
        for line in raw_logs.splitlines():
            start = line.find('{"')
            if start < 0:
                continue
            try:
                value = json.loads(line[start:])
            except ValueError:
                continue
            if value.get("event") == "request_completed":
                records.append(value)
        found = {r.get("request_id") for r in records}
        if all(c["audit_id"] in found for c in cases) or time.monotonic() >= deadline:
            break
        time.sleep(0.2)

    # Сырой объединённый лог остаётся локальным даже при утечке токена из-за ошибки.
    private = ROOT / ".cache/stage4-privacy.log"
    fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(raw_logs)
    private.chmod(0o600)
    forbidden = dict(markers, api_token=key)
    leaked_categories = [name for name, value in forbidden.items() if value and value in raw_logs]
    counts = collections.Counter(r.get("request_id") for r in records)
    by_id = {r.get("request_id"): r for r in records}
    allowed = {"timestamp", "level", "event", "request_id", "method", "route", "status", "duration_ms", "outcome"}
    for case in cases:
        record = by_id.get(case["audit_id"], {})
        case["one_matching_audit_event"] = counts[case["audit_id"]] == 1
        case["audit_status_matches"] = record.get("status") == case["status"]
        case["audit_schema_safe"] = set(record) == allowed and record.get("outcome") == "completed"
        case["server_generated_id"] = case["audit_id"] != markers["header"] and len(case["audit_id"]) == 32
        case["passed"] &= all(case[k] for k in ("one_matching_audit_event", "audit_status_matches", "audit_schema_safe", "server_generated_id"))
    all_passed = not leaked_categories and all(c["passed"] for c in cases)
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "pod": pod,
              "cases": cases, "payload_and_token_scan_passed": not leaked_categories,
              "leaked_categories": leaked_categories, "audit_event_count": len(records), "all_passed": all_passed,
              "scope": "Normal/SSE/validation/routing/auth HTTP paths; combined container stdout/stderr. Native crashes and SIGKILL are not exhaustively tested."}
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    if all_passed:
        sample = [by_id[c["audit_id"]] for c in cases]
        output.with_name(output.stem + "-audit.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sample))
        print("PASS payload/token scan and one safe audit record per request")
    else:
        raise SystemExit("Privacy/auth checks failed; inspect report and private cache log without publishing it")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.SubprocessError):
        raise SystemExit("Could not complete privacy tests. Check Ready Pod and localhost port-forward; request contents are not displayed.") from None
