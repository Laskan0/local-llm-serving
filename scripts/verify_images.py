"""Проверка контейнерных образов без запуска дополнительного сервера модели."""

import json
import argparse
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RESTRICTED = ["--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]


def main():
    """Проверить зависимости, права и ограничения запуска контейнерных образов."""
    current = json.loads((ROOT / "config/images.json").read_text())
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-image", default=current["server"])
    parser.add_argument("--downloader-image", default=current["downloader"])
    parser.add_argument("--output", default=".cache/reports/images.json")
    args = parser.parse_args()
    server_image = args.server_image
    downloader_image = args.downloader_image
    results = []

    def check(name, args, expected=0, contains=None):
        """Запустить контейнер и сохранить результат проверки.

        :param name: Название проверки.
        :param args: Аргументы docker run.
        :param expected: Ожидаемый код завершения.
        :param contains: Необязательная подстрока в выводе.
        """
        run = subprocess.run(["docker", "run", "--rm", *RESTRICTED, *args],
                             capture_output=True, text=True, timeout=90, cwd=ROOT)
        output = run.stdout + run.stderr
        passed = run.returncode == expected and (contains is None or contains in output)
        results.append({"name": name, "passed": passed, "exit_code": run.returncode,
                        "output": output.strip()})
        print(f"{'PASS' if passed else 'FAIL'} {name}", flush=True)

    for image in (server_image, downloader_image):
        check(f"{image}: dependency lock", ["--entrypoint", "python3", image,
              "/app/scripts/check_dependencies.py", "/app/requirements.txt"], contains="dependency_lock_ok")
        check(f"{image}: default user and home permissions", ["--entrypoint", "python3", image, "-c",
              "import os; from pathlib import Path; assert os.getuid()==10001; assert os.getgid()==10001; "
              "p=Path('/home/app/.cache/probe'); p.write_text('ok'); p.unlink(); print('uid_gid_cache_ok')"],
              contains="uid_gid_cache_ok")

    with tempfile.TemporaryDirectory(prefix="volume-probe-", dir=ROOT / ".cache") as directory:
        check("downloader: mounted volume permissions", ["--mount", f"type=bind,source={directory},target=/models",
              "--entrypoint", "python3", downloader_image, "-c",
              "from pathlib import Path; p=Path('/models/probe'); p.write_text('ok'); p.unlink(); print('volume_write_ok')"],
              contains="volume_write_ok")

    check("downloader: local model without network", ["--mount",
          f"type=bind,source={ROOT / '.cache/models'},target=/models,readonly", downloader_image],
          contains="model_ready source=local")
    check("server: reject missing API token", [server_image], expected=1,
          contains="VLLM_API_KEY must be supplied at runtime")
    check("server: reject root override", ["--user", "0:0", server_image], expected=1,
          contains="Refusing to run the inference server as root")
    check("server: detect dependency drift", ["--entrypoint", "python3", server_image, "-c",
          "from pathlib import Path; import subprocess; "
          "s=Path('/app/requirements.txt').read_text().replace('vllm==0.27.1+cpu','vllm==0.0.0'); "
          "Path('/tmp/drift.txt').write_text(s); "
          "r=subprocess.run(['python3','/app/scripts/check_dependencies.py','/tmp/drift.txt'],capture_output=True,text=True); "
          "assert r.returncode != 0 and 'vllm:' in r.stderr; print('dependency_drift_rejected')"],
          contains="dependency_drift_rejected")

    images = []
    for image in (server_image, downloader_image):
        info = json.loads(subprocess.check_output(["docker", "image", "inspect", image], text=True))[0]
        images.append({"tag": image, "id": info["Id"], "architecture": info["Architecture"],
                       "size_bytes": info["Size"], "user": info["Config"]["User"]})
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "images": images,
              "checks": results, "all_passed": all(r["passed"] for r in results)}
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    if not report["all_passed"]:
        raise SystemExit("Some image checks failed; see the chosen output report")


if __name__ == "__main__":
    main()
