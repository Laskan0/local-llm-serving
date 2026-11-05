# Результаты проверок

В каталоге сохранены результаты конкретных запусков. Старые отчёты относятся к конфигурации на момент проверки; текущие команды находятся в корневом README.

- `01-smoke.json`, `02-smoke.json` — первые ответы API; соответствующие `*-resources.json` содержат замеры ресурсов.
- `02-download.json` — ревизия модели и проверка SHA256 после загрузки.
- `02-image-checks.json`, `04-image-checks.json`, `08-image-checks.json` — проверки зависимостей, пользователя и условий запуска образов.
- `03-first-download.log`, `03-cached-download.log` — первоначальная загрузка и повторное использование модели.
- `03-config-reload.json` — смена настроек с сохранением образа и PVC.
- `03-kubernetes-checks.json`, `04-kubernetes-checks.json`, `07-kubernetes.json` — проверки Kubernetes.
- `04-privacy-checks.json`, `07-privacy.json` — проверки ответов, ошибок, авторизации и отсутствия текстов запросов и токена в логах.
- `04-audit-sample.jsonl`, `07-audit-sample.jsonl` — примеры записей аудита.
- [Нагрузочный тест](05-benchmark.md), [анализ](05-analysis.md), [протокол](05-protocol.md) — методика и результаты измерений.
- `05-benchmark/` — сырые данные серий A-before, B-batch512, A-after и отдельной пробы pilot.
- `05-workload.json`, `05-workload-pilot.json` — синтетические входы; `05-summary.csv` — сводка метрик.
- `05-environment.json`, `05-host-observations.jsonl`, `05-server-observations.jsonl` — окружение и наблюдения во время нагрузки.
- `05-kubernetes-before.json`, `05-kubernetes-after.json`, `05-config-restore.json` — состояние до и после измерений.
- `07-smoke.json` — ответ API из нового профиля с новым PVC.
- `09-client-checks.json` — проверка терминального клиента: одиночный вопрос, история, сброс и ошибки.

Файлы `*-unit-tests.log` содержат вывод тестов на момент соответствующего запуска. В текущем наборе 18 тестов.
