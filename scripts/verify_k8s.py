"""Проверка конфигурации и состояния Kubernetes без чтения значений Secret."""

import json
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def kubectl(*arguments):
    """Выполнить команду kubectl в локальном контексте проекта.

    :param arguments: Аргументы команды без имени kubectl.
    :return: Стандартный вывод команды.
    :rtype: str
    """
    return subprocess.check_output(["bash", str(ROOT / "scripts/kubectl.sh"), *arguments],
                                   text=True, timeout=45, cwd=ROOT)


def main():
    """Проверить готовый Pod, ресурсы, тома и параметры безопасности."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".cache/reports/kubernetes.json")
    args = parser.parse_args()
    namespace = "kaspersky-llm"
    pods = json.loads(kubectl("-n", namespace, "get", "pods", "-l", "app=vllm", "-o", "json"))["items"]
    ready_pods = [p for p in pods if not p["metadata"].get("deletionTimestamp") and
                  any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", []))]
    if len(ready_pods) != 1:
        raise SystemExit("Expected exactly one Ready inference Pod")
    pod = ready_pods[0]
    spec = pod["spec"]
    init = next(c for c in spec["initContainers"] if c["name"] == "download-model")
    server = spec["containers"][0]
    pvc = json.loads(kubectl("-n", namespace, "get", "pvc", "model-cache", "-o", "json"))
    service = json.loads(kubectl("-n", namespace, "get", "service", "vllm", "-o", "json"))
    init_status = next(c for c in pod["status"]["initContainerStatuses"] if c["name"] == "download-model")
    runtime_code = (
        "import os,json; from pathlib import Path; "
        "p=Path('/models/qwen'); c=Path('/sys/fs/cgroup'); "
        "argv=Path('/proc/1/cmdline').read_bytes().decode().split('\\0'); "
        "ms=dict(line.split() for line in (c/'memory.stat').read_text().splitlines()); "
        "print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),"
        "'marker':json.loads((p/'.ready.json').read_text()),"
        "'weights_size_bytes':(p/'model.safetensors').stat().st_size,"
        "'weights_mtime_ns':(p/'model.safetensors').stat().st_mtime_ns,"
        "'marker_mtime_ns':(p/'.ready.json').stat().st_mtime_ns,"
        "'effective_max_model_len':int(argv[argv.index('--max-model-len')+1]),"
        "'memory_current_bytes':int((c/'memory.current').read_text()),"
        "'anon_bytes':int(ms['anon']),'file_bytes':int(ms['file']),"
        "'inactive_file_bytes':int(ms['inactive_file']),"
        "'memory_peak_bytes':int((c/'memory.peak').read_text()) if (c/'memory.peak').exists() else None}))"
    )
    runtime = json.loads(kubectl("-n", namespace, "exec", pod["metadata"]["name"], "-c", "server",
                                 "--", "python3", "-c", runtime_code))
    config_volume = next(v["configMap"]["name"] for v in spec["volumes"] if v["name"] == "inference-config")
    cm = json.loads(kubectl("-n", namespace, "get", "configmap", config_volume, "-o", "json"))
    config = json.loads(cm["data"]["inference.json"])
    env_server = {e["name"]: e for e in server["env"]}
    env_init = {e["name"]: e for e in init["env"]}
    mounts_server = {m["name"]: m for m in server["volumeMounts"]}
    mounts_init = {m["name"]: m for m in init["volumeMounts"]}
    security = spec["securityContext"]
    checks = {
        "pvc_bound": pvc["status"]["phase"] == "Bound",
        "init_completed": init_status["state"].get("terminated", {}).get("exitCode") == 0,
        "nonroot_effective_uid_gid": runtime["uid"] == runtime["gid"] == 10001,
        "pod_nonroot_enforced": security["runAsNonRoot"] and security["runAsUser"] == 10001,
        "restricted_containers": all(c["securityContext"].get("allowPrivilegeEscalation") is False
                                     and c["securityContext"].get("readOnlyRootFilesystem") is True
                                     and "ALL" in c["securityContext"]["capabilities"]["drop"]
                                     for c in (init, server)),
        "no_service_account_token": spec["automountServiceAccountToken"] is False,
        "no_service_environment_links": spec["enableServiceLinks"] is False,
        "server_model_mount_readonly": mounts_server["model-cache"]["readOnly"] is True,
        "downloader_model_mount_writable": not mounts_init["model-cache"].get("readOnly", False),
        "model_revision_matches": runtime["marker"] == json.loads((ROOT / "config/model.json").read_text()),
        "server_api_secret_reference": env_server["VLLM_API_KEY"]["valueFrom"]["secretKeyRef"]["name"] == "vllm-api",
        "downloader_hf_secret_reference": env_init["HF_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "hf-access",
        "hf_token_not_in_server_environment_spec": "HF_TOKEN" not in env_server,
        "api_token_not_in_downloader_environment_spec": "VLLM_API_KEY" not in env_init,
        "configmap_matches_source": config == json.loads((ROOT / "k8s/inference.json").read_text()),
        "effective_context_matches_configmap": runtime["effective_max_model_len"] == config["max_model_len"],
        "service_internal": service["spec"]["type"] == "ClusterIP",
        "exactly_one_ready_pod": len(ready_pods) == 1,
    }
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "checks": checks,
              "all_passed": all(checks.values()), "pod": pod["metadata"]["name"],
              "pod_uid": pod["metadata"]["uid"], "pvc_uid": pvc["metadata"]["uid"],
              "pvc_volume": pvc["spec"]["volumeName"], "storage_class": pvc["spec"]["storageClassName"],
              "configmap": config_volume, "inference_config": config,
              "resources": server["resources"],
              "runtime": runtime, "images": [{"name": c["name"], "image_id": c["imageID"]}
                                              for c in pod["status"]["containerStatuses"]]}
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    if not report["all_passed"]:
        raise SystemExit("Kubernetes verification failed; see the chosen report")


if __name__ == "__main__":
    main()
