"""Нагрузочный тест потокового API с фиксированной параллельностью.

Используется стандартная библиотека Python. Тексты ответов не сохраняются.
TTFT включает соединение, port-forward и ожидание в очереди.
"""

import argparse
import concurrent.futures
import hashlib
import json
import math
import socket
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def percentile(values, percent):
    """Вычислить эмпирический процентиль методом ближайшего ранга.

    :param values: Выборка числовых значений.
    :param percent: Процентиль в диапазоне от 0 до 100.
    :return: Значение процентиля либо None для пустой выборки.
    """
    return sorted(values)[max(0, math.ceil(len(values)*percent/100)-1)] if values else None


def parse_stream(response, started):
    """Прочитать SSE до EOF и собрать метрики без сохранения текста.

    :param response: Поток строк HTTP-ответа.
    :param started: Начальная отметка time.perf_counter().
    :return: Задержки, признаки завершения и число токенов.
    :rtype: dict
    :raises ValueError: Если сервер сообщил об ошибке потока.
    """
    first = None
    usage = None
    finish = None
    done = False
    chunks = 0
    for line in response:
        if not line.startswith(b'data:'):
            continue
        value = line[5:].strip()
        if value == b'[DONE]':
            done = True
            continue  # Чтение продолжается до EOF, включая последний кадр ASGI.
        item = json.loads(value)
        if item.get('error'):
            raise ValueError('stream_error')
        if item.get('usage'):
            usage = item['usage']
        for choice in item.get('choices', []):
            if choice.get('text'):
                if first is None:
                    first = time.perf_counter()-started
                chunks += 1
            if choice.get('finish_reason'):
                finish = choice['finish_reason']
    return {'ttft_s': first, 'latency_s': time.perf_counter()-started,
            'stream_done': done, 'content_chunks': chunks, 'finish_reason': finish,
            'prompt_tokens': usage.get('prompt_tokens') if usage else None,
            'output_tokens': usage.get('completion_tokens') if usage else None,
            'cached_prompt_tokens': (usage.get('prompt_tokens_details') or {}).get('cached_tokens') if usage else None}


def request(base_url, key, prompt, index, timeout, output_tokens):
    """Измерить один потоковый запрос без сохранения ответа.

    :param base_url: Базовый адрес сервера.
    :param key: API-токен.
    :param prompt: Входные идентификаторы токенов.
    :param index: Номер запроса.
    :param timeout: Таймаут ожидания сетевых данных.
    :param output_tokens: Требуемое число выходных токенов.
    :return: Метрики и результат проверки запроса.
    :rtype: dict
    """
    body = {'model': 'qwen', 'prompt': prompt, 'max_tokens': output_tokens,
            'temperature': 0, 'seed': 42, 'ignore_eos': True, 'stream': True,
            'stream_options': {'include_usage': True}}
    payload = json.dumps(body).encode()
    req = urllib.request.Request(base_url+'/v1/completions', data=payload,
        headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
    started = time.perf_counter()
    result = {'index': index, 'status': None, 'success': False, 'error': None}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result['status'] = response.status
            result.update(parse_stream(response, started))
        result['success'] = (result['status'] == 200 and result['stream_done'] and
            result['ttft_s'] is not None and result['prompt_tokens'] == len(prompt) and
            result['output_tokens'] == output_tokens and result['finish_reason'] == 'length')
        if not result['success']:
            result['error'] = 'incomplete_or_unexpected_usage'
    except urllib.error.HTTPError as error:
        result.update(status=error.code, error='http_error')
    except (TimeoutError, socket.timeout):
        result['error'] = 'timeout'
    except (OSError, ValueError, KeyError, TypeError):
        result['error'] = 'transport_or_stream_error'
    result.setdefault('latency_s', time.perf_counter()-started)
    return result


def kubectl(*args):
    """Выполнить команду kubectl в локальном контексте проекта.

    :param args: Аргументы команды без имени kubectl.
    :return: Стандартный вывод команды.
    :rtype: str
    """
    result = subprocess.run(['bash', str(ROOT/'scripts/kubectl.sh'), *args],
                            capture_output=True, text=True, timeout=30, cwd=ROOT)
    if result.returncode:
        raise RuntimeError('kubectl failed; payload suppressed')
    return result.stdout


def snapshot(pod):
    """Получить показатели cgroup серверного контейнера.

    :param pod: Имя Pod сервера.
    :return: Счётчики памяти и CPU.
    :rtype: dict
    """
    code = (
        "import json;from pathlib import Path; p=Path('/sys/fs/cgroup');"
        "pairs=lambda n:dict((k,int(v)) for k,v in (line.split() for line in (p/n).read_text().splitlines()));"
        "print(json.dumps({'memory_current_bytes':int((p/'memory.current').read_text()),"
        "'memory_peak_since_pod_start_bytes':int((p/'memory.peak').read_text()),"
        "'memory_stat':pairs('memory.stat'),'memory_events':pairs('memory.events'),'cpu_stat':pairs('cpu.stat')}))"
    )
    return json.loads(kubectl('-n','kaspersky-llm','exec',pod,'-c','server','--','python3','-c',code))


METRICS = ('vllm:prefix_cache_queries_total', 'vllm:prefix_cache_hits_total',
           'vllm:num_preemptions_total', 'vllm:prompt_tokens_total', 'vllm:generation_tokens_total')


def metrics(base_url):
    """Прочитать разрешённые счётчики из эндпоинта метрик.

    :param base_url: Базовый адрес сервера.
    :return: Суммы значений выбранных метрик.
    :rtype: dict
    """
    with urllib.request.urlopen(base_url+'/metrics', timeout=15) as response:
        lines = response.read().decode().splitlines()
    totals = {}
    for line in lines:
        if not line or line.startswith('#'):
            continue
        name = line.split('{',1)[0].split()[0]
        if name in METRICS:
            totals[name] = totals.get(name,0)+float(line.rsplit(' ',1)[1])
    return totals


def run_cell(args, key, prompts, length, concurrency, pod, output_dir):
    """Выполнить прогрев и измерения одной комбинации длины и параллельности.

    :param args: Параметры нагрузочного теста.
    :param key: API-токен.
    :param prompts: Подготовленные входы заданной длины.
    :param length: Число входных токенов.
    :param concurrency: Число одновременных клиентов.
    :param pod: Имя серверного Pod.
    :param output_dir: Каталог сырых результатов.
    :return: Сводка измерений.
    :rtype: dict
    :raises RuntimeError: Если прогрев завершился неуспешно.
    """
    name = f'input{length}-c{concurrency}'
    print('Warmup', name, flush=True)
    # Для прогрева выделены отдельные входы с той же параллельностью и обеими фазами вычислений.
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        warm = list(pool.map(lambda i: request(args.base_url,key,prompts[96+i],i,args.timeout,args.output_tokens),
                             range(concurrency)))
    if not all(x['success'] for x in warm):
        (output_dir/(name+'-warmup-failure.json')).write_text(json.dumps(warm,indent=2)+'\n')
        raise RuntimeError('Warmup failed; see metadata-only failure report')
    samples, sampler_errors = [], []
    stop = threading.Event()

    def sample():
        """Собирать показатели cgroup до установки сигнала остановки."""
        while not stop.is_set():
            try:
                value = snapshot(pod)
                value['offset_s'] = time.perf_counter()-origin
                samples.append(value)
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
                sampler_errors.append('cgroup_sample_failed')
            stop.wait(5)

    before = snapshot(pod)
    metrics_before = metrics(args.base_url)
    origin = time.perf_counter()
    thread = threading.Thread(target=sample, daemon=True)
    barrier = threading.Barrier(concurrency+1)
    def worker(worker_index):
        """Выполнить долю запросов после общего сигнала старта.

        :param worker_index: Номер клиента.
        :return: Метрики выполненных запросов.
        :rtype: list
        """
        barrier.wait()
        offset = {1:0,2:1,4:2}[concurrency]*args.requests
        return [request(args.base_url,key,prompts[offset+i],i,args.timeout,args.output_tokens)
                for i in range(worker_index,args.requests,concurrency)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker,i) for i in range(concurrency)]
        origin = time.perf_counter()
        thread.start()
        barrier.wait()
        records = [record for future in futures for record in future.result()]
        elapsed = time.perf_counter()-origin
    stop.set()
    thread.join(timeout=35)
    after = snapshot(pod)
    metrics_after = metrics(args.base_url)
    passed = [x for x in records if x['success']]
    samples = [dict(before,offset_s=0),*samples,dict(after,offset_s=elapsed)]
    stat = lambda key: {'median':statistics.median([x[key] for x in passed]) if passed else None,
                       'p95':percentile([x[key] for x in passed],95)}
    summary = {'scenario':name, 'input_tokens':length,'concurrency':concurrency,
        'requests':args.requests,'warmup_requests':concurrency,'warmup':warm,
        'successes':len(passed),'errors':len(records)-len(passed),'elapsed_s':elapsed,
        'ttft_s':stat('ttft_s'),'latency_s':stat('latency_s'),
        'requests_per_s':len(passed)/elapsed,
        'output_tokens_per_s':sum(x['output_tokens'] for x in passed)/elapsed,
        'sampled_memory_max_bytes':max(x['memory_current_bytes'] for x in samples),
        'sampled_working_set_max_bytes':max(x['memory_current_bytes']-x['memory_stat'].get('inactive_file',0) for x in samples),
        'memory_peak_since_pod_start_bytes':after['memory_peak_since_pod_start_bytes'],
        'memory_events_delta':{k:after['memory_events'][k]-v for k,v in before['memory_events'].items()},
        'cpu_stat_delta':{k:after['cpu_stat'][k]-v for k,v in before['cpu_stat'].items()},
        'metrics_before':metrics_before,'metrics_after':metrics_after,
        'metrics_delta':{k:metrics_after[k]-v for k,v in metrics_before.items() if k in metrics_after},
        'sampler_errors':sampler_errors,'memory_sample_interval_s':5}
    raw = {'summary':summary,'requests':sorted(records,key=lambda x:x['index']),'memory_samples':samples}
    (output_dir/(name+'.json')).write_text(json.dumps(raw,indent=2)+'\n')
    print(f"{name}: {len(passed)}/{len(records)} OK; TTFT median={summary['ttft_s']['median']}; "
          f"output tokens/s={summary['output_tokens_per_s']:.3f}",flush=True)
    return summary


def main():
    """Запустить серию нагрузки и сохранить метрики без текстов ответов."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:18000')
    parser.add_argument('--token-file',default='.cache/k8s-api.env')
    parser.add_argument('--workload',default='results/05-workload.json')
    parser.add_argument('--label',required=True)
    parser.add_argument('--results-dir',default='results/05-benchmark')
    parser.add_argument('--concurrency',type=int,nargs='+',default=[1,2,4])
    parser.add_argument('--lengths',type=int,nargs='+',default=[128,2048])
    parser.add_argument('--requests',type=int,default=12)
    parser.add_argument('--output-tokens',type=int,default=64)
    parser.add_argument('--timeout',type=int,default=240)
    args = parser.parse_args()
    if (not 1 <= args.requests <= 32 or any(c not in (1,2,4) for c in args.concurrency)
            or any(n not in (128,2048) for n in args.lengths)
            or args.requests % max(args.concurrency) or not 1<=args.output_tokens<=64
            or not args.label.replace('-','').isalnum()):
        raise SystemExit('Invalid workload limits, label or request count')
    key = (ROOT/args.token_file).read_text().strip().split('=',1)[1]
    workload_path = ROOT/args.workload
    workload = json.loads(workload_path.read_text())
    if workload['model'] != json.loads((ROOT/'config/model.json').read_text()):
        raise SystemExit('Workload revision does not match the pinned model')
    if any(len(workload['prompts'].get(str(n),[])) < 100 or
           any(len(p)!=n for p in workload['prompts'][str(n)]) for n in args.lengths):
        raise SystemExit('Expected 100 exact-length synthetic prompts per input size')
    pods = json.loads(kubectl('-n','kaspersky-llm','get','pods','-l','app=vllm','-o','json'))['items']
    ready = [p for p in pods if not p['metadata'].get('deletionTimestamp') and
             any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[]))]
    if len(ready)!=1:
        raise SystemExit('Expected exactly one Ready Pod')
    pod=ready[0]
    cm_name=next(v['configMap']['name'] for v in pod['spec']['volumes'] if v['name']=='inference-config')
    cm=json.loads(kubectl('-n','kaspersky-llm','get','configmap',cm_name,'-o','json'))
    output_dir=ROOT/args.results_dir/args.label
    if output_dir.exists():
        raise SystemExit('Results directory already exists; choose a new --results-dir or --label')
    output_dir.mkdir(parents=True,exist_ok=False)  # Существующие измерения не перезаписываются.
    report={'label':args.label,'started_utc':datetime.now(timezone.utc).isoformat(),
        'protocol':'closed-loop fixed workers, disjoint warmup prompts, full SSE to EOF; nearest-rank p95',
        'workload_sha256':hashlib.sha256(workload_path.read_bytes()).hexdigest(),
        'requested_output_tokens':args.output_tokens,'temperature':0,'ignore_eos':True,'seed':42,
        'request_timeout_s':args.timeout,'pod':pod['metadata']['name'],'pod_uid':pod['metadata']['uid'],
        'images':[{k:c[k] for k in ('name','image','imageID','restartCount')} for c in pod['status']['containerStatuses']],
        'resources':pod['spec']['containers'][0]['resources'],
        'configmap':cm_name,'config':cm['data'],'model':workload['model'],'cells':[]}
    destination=output_dir/'summary.json'
    destination.write_text(json.dumps(report,indent=2)+'\n')
    for length in args.lengths:
        for concurrency in args.concurrency:
            cell=run_cell(args,key,workload['prompts'][str(length)],length,concurrency,pod['metadata']['name'],output_dir)
            report['cells'].append(cell)
            destination.write_text(json.dumps(report,indent=2)+'\n')
    final=json.loads(kubectl('-n','kaspersky-llm','get','pod',pod['metadata']['name'],'-o','json'))
    initial_restarts=pod['status']['containerStatuses'][0]['restartCount']
    final_restarts=final['status']['containerStatuses'][0]['restartCount']
    report.update(finished_utc=datetime.now(timezone.utc).isoformat(),
        restart_count_before=initial_restarts,restart_count_after=final_restarts,
        all_passed=all(not c['errors'] and not c['sampler_errors'] and
                       not c['memory_events_delta'].get('oom_kill',0) for c in report['cells'])
                   and initial_restarts==final_restarts)
    destination.write_text(json.dumps(report,indent=2)+'\n')
    if not report['all_passed']:
        raise SystemExit('Benchmark completed with errors; all attempts retained in reports')
    print('PASS benchmark saved:',destination.relative_to(ROOT),flush=True)


if __name__=='__main__':
    main()
