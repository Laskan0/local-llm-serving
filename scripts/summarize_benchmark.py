"""Построение Markdown- и CSV-сводок по трём сохранённым сериям нагрузки."""

import argparse
import csv
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABELS = ('A-before','B-batch512','A-after')
GIB = 1024**3


def load_reports(results_dir):
    """Проверить метрики и сопоставимость сохранённых серий.

    :param results_dir: Каталог с сериями A-before, B-batch512 и A-after.
    :return: Проверенные отчёты по именам серий.
    :rtype: dict
    :raises AssertionError: Если данные неполны или конфигурации несопоставимы.
    """
    reports = {label:json.loads((results_dir/label/'summary.json').read_text()) for label in LABELS}
    for label,report in reports.items():
        assert report['all_passed'],f'{label}: contains errors'
        expected = {(n,c) for n in (128,2048) for c in ((1,2,4) if label=='A-before' else (4,))}
        assert {(c['input_tokens'],c['concurrency']) for c in report['cells']}==expected
        assert all(c['requests']==12 and c['successes']==12 for c in report['cells'])
        for cell in report['cells']:
            raw=json.loads((results_dir/label/(cell['scenario']+'.json')).read_text())
            assert raw['summary']==cell and len(raw['requests'])==12
            assert all(r['success'] and r['status']==200 and r['stream_done'] and
                       r['output_tokens']==64 and r['prompt_tokens']==cell['input_tokens'] and
                       0<r['ttft_s']<=r['latency_s'] for r in raw['requests'])
            assert math.isclose(cell['output_tokens_per_s'],sum(r['output_tokens'] for r in raw['requests'])/cell['elapsed_s'])
    baseline = reports['A-before']
    a_config = json.loads(baseline['config']['inference.json'])
    for label,report in reports.items():
        assert report['workload_sha256']==baseline['workload_sha256']
        assert report['model']==baseline['model'] and report['resources']==baseline['resources']
        assert [i['imageID'] for i in report['images']]==[i['imageID'] for i in baseline['images']]
        expected = dict(a_config,max_num_batched_tokens=512) if label=='B-batch512' else a_config
        assert json.loads(report['config']['inference.json'])==expected
        assert {k:v for k,v in report['config'].items() if k!='inference.json'}=={k:v for k,v in baseline['config'].items() if k!='inference.json'}
    return reports


def plot(reports,output_prefix):
    # Необязательная зависимость для графиков не импортируется генератором нагрузки.
    """Построить графики TTFT и пропускной способности.

    :param reports: Проверенные отчёты серий.
    :param output_prefix: Префикс путей PNG и описания окружения.
    """
    os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.cache/matplotlib'))
    os.environ.setdefault('XDG_CACHE_HOME',str(ROOT/'.cache/rendering'))
    Path(os.environ['MPLCONFIGDIR']).mkdir(parents=True,exist_ok=True)
    Path(os.environ['XDG_CACHE_HOME']).mkdir(parents=True,exist_ok=True)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    figure,axes=plt.subplots(2,2,figsize=(12,8),layout='constrained')
    colors={128:'#267a80',2048:'#a85a32'}
    for length in (128,2048):
        cells=[c for c in reports['A-before']['cells'] if c['input_tokens']==length]
        axes[0,0].plot([c['concurrency'] for c in cells],[c['ttft_s']['median'] for c in cells],marker='o',color=colors[length],label=f'{length} input tokens')
        axes[0,1].plot([c['concurrency'] for c in cells],[c['output_tokens_per_s'] for c in cells],marker='o',color=colors[length],label=f'{length} input tokens')
    axes[0,0].set(title='Baseline: median time to first content',ylabel='Seconds')
    axes[0,1].set(title='Baseline: total output throughput',ylabel='Output tokens / second')
    for ax in axes[0]:
        ax.set(xlabel='Concurrent clients',xticks=[1,2,4]);ax.legend();ax.grid(axis='y',alpha=.2)
    for index,label in enumerate(LABELS):
        cells=sorted([c for c in reports[label]['cells'] if c['concurrency']==4],key=lambda c:c['input_tokens'])
        x=[i+(index-1)*.24 for i in range(2)]
        axes[1,0].bar(x,[c['ttft_s']['median'] for c in cells],width=.23,label=label)
        axes[1,1].bar(x,[c['output_tokens_per_s'] for c in cells],width=.23,label=label)
    axes[1,0].set(title='C=4: A/B/A median TTFT',ylabel='Seconds')
    axes[1,1].set(title='C=4: A/B/A total throughput',ylabel='Output tokens / second')
    for ax in axes[1]:
        ax.set(xticks=[0,1],xticklabels=['128 input','2048 input']);ax.legend();ax.grid(axis='y',alpha=.2)
    figure.suptitle('Qwen2.5-1.5B · vLLM CPU · MacBook Air M4 / 16 GB\n12 requests per point; 64 output tokens; descriptive single-run measurements',fontsize=13)
    figure.savefig(str(output_prefix)+'-benchmark.png',dpi=160)
    plt.close(figure)
    Path(str(output_prefix)+'-plot-environment.json').write_text(json.dumps({'matplotlib':matplotlib.__version__},indent=2)+'\n')


def main():
    """Проверить сопоставимость серий и записать CSV, Markdown и необязательный график."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--plot',action='store_true',help='Optional: render PNG using locally installed matplotlib')
    parser.add_argument('--results-dir',default='results/05-benchmark')
    parser.add_argument('--output-prefix',default='results/05')
    args=parser.parse_args()
    results_dir=ROOT/args.results_dir
    output_prefix=ROOT/args.output_prefix
    output_prefix.parent.mkdir(parents=True,exist_ok=True)
    relative=lambda p:os.path.relpath(p,output_prefix.parent)
    reports=load_reports(results_dir)
    rows=[]
    for label,report in reports.items():
        for cell in report['cells']:
            rows.append({'series':label,'input_tokens':cell['input_tokens'],'concurrency':cell['concurrency'],
                'n':cell['requests'],'errors':cell['errors'],'ttft_median_s':cell['ttft_s']['median'],
                'ttft_p95_s':cell['ttft_s']['p95'],'latency_median_s':cell['latency_s']['median'],
                'latency_p95_s':cell['latency_s']['p95'],'output_tokens_per_s':cell['output_tokens_per_s'],
                'requests_per_s':cell['requests_per_s'],'elapsed_s':cell['elapsed_s'],
                'sampled_memory_max_gib':cell['sampled_memory_max_bytes']/GIB,
                'sampled_working_set_max_gib':cell['sampled_working_set_max_bytes']/GIB,
                'memory_peak_since_pod_start_gib':cell['memory_peak_since_pod_start_bytes']/GIB,
                'oom_kills':cell['memory_events_delta'].get('oom_kill',0),
                'preemptions':cell['metrics_delta'].get('vllm:num_preemptions_total'),
                'prefix_cache_hit_tokens':cell['metrics_delta'].get('vllm:prefix_cache_hits_total')})
    with Path(str(output_prefix)+'-summary.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    lines=['# Нагрузочные измерения: фактические результаты','',
        '120 измеряемых запросов, по 12 в точке; ещё 30 прогревочных запросов исключены из производительности. Пробные 4 + 1 прогрев в итоговые таблицы не включены.',
        '', f"Протокол: [05-protocol.md]({relative(ROOT/'results/05-protocol.md')}). Машиночитаемая таблица: [CSV]({output_prefix.name}-summary.csv).",
        '', '## Исходная конфигурация: пакет 1024','',
        '| Вход | Клиенты | TTFT med / p95, с | Latency med / p95, с | Выход, ток/с | Запросы/с | Ошибки |',
        '|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        if r['series']=='A-before':
            lines.append(f"| {r['input_tokens']} | {r['concurrency']} | {r['ttft_median_s']:.3f} / {r['ttft_p95_s']:.3f} | {r['latency_median_s']:.3f} / {r['latency_p95_s']:.3f} | {r['output_tokens_per_s']:.3f} | {r['requests_per_s']:.4f} | {r['errors']} |")
    lines += ['', '## Сравнение настройки при четырёх клиентах','',
        '| Вход | Серия | Пакет | TTFT med / p95, с | Latency med / p95, с | Выход, ток/с |',
        '|---:|---|---:|---:|---:|---:|']
    for length in (128,2048):
        for label in LABELS:
            r=next(r for r in rows if r['series']==label and r['input_tokens']==length and r['concurrency']==4)
            lines.append(f"| {length} | {label} | {512 if label=='B-batch512' else 1024} | {r['ttft_median_s']:.3f} / {r['ttft_p95_s']:.3f} | {r['latency_median_s']:.3f} / {r['latency_p95_s']:.3f} | {r['output_tokens_per_s']:.3f} |")
    lines += ['', '## Память и устойчивость','',
        '| Серия | Вход | Клиенты | Max sampled current, ГиБ | Max sampled working set, ГиБ | OOM kill | Preemption | Prefix hit tokens |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['series']} | {r['input_tokens']} | {r['concurrency']} | {r['sampled_memory_max_gib']:.3f} | {r['sampled_working_set_max_gib']:.3f} | {r['oom_kills']} | {r['preemptions']} | {r['prefix_cache_hit_tokens']} |")
    lines += ['', 'Current включает файловый кэш; working set = current − inactive_file. Пик выборки не гарантирует обнаружение кратковременного пика. memory.peak с начала жизни Pod и все cgroup-счётчики сохранены в сырых JSON.',
        '', '## Изменения относительно первого A','']
    for length in (128,2048):
        select=lambda label:next(r for r in rows if r['series']==label and r['input_tokens']==length and r['concurrency']==4)
        a,b,a2=[select(label) for label in LABELS]
        change=lambda x,k:100*(x[k]/a[k]-1)
        lines += [f"- Вход {length}: B относительно A-before — TTFT {change(b,'ttft_median_s'):+.1f}%, throughput {change(b,'output_tokens_per_s'):+.1f}%. A-after относительно A-before — TTFT {change(a2,'ttft_median_s'):+.1f}%, throughput {change(a2,'output_tokens_per_s'):+.1f}%."]
    lines += ['', 'При N=12 nearest-rank p95 равен максимуму. Один проход A/B/A не даёт статистически надёжного выбора оптимума; разброс повторного A обязательно учитывается при интерпретации.',
        '', '## Проверяемость','']
    for label,report in reports.items():
        lines += [f"- [{label}: summary.json]({relative(results_dir/label/'summary.json')}), время {report['started_utc']} — {report['finished_utc']}; рестарты {report['restart_count_before']} → {report['restart_count_after']}."]
    lines += ['', 'Сводка проверяет одинаковые workload SHA256, модель, imageID, ресурсы и все настройки, кроме max_num_batched_tokens. Тексты ответов и токен авторизации не сохраняются.', '']
    if args.plot:
        plot(reports,output_prefix)
        lines += [f'![График измерений]({output_prefix.name}-benchmark.png)','']
    Path(str(output_prefix)+'-benchmark.md').write_text('\n'.join(lines))
    print('PASS comparable reports; Markdown and CSV generated'+('; PNG rendered' if args.plot else ''))


if __name__=='__main__':
    main()
