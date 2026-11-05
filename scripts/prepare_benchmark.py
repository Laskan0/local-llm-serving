"""Подготовка воспроизводимых входов точной длины токенизатором модели из PVC."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PREPARE = r'''
import json, random
from pathlib import Path
from transformers import AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained('/models/qwen', local_files_only=True)
header = tokenizer.encode('<|im_start|>user\n', add_special_tokens=False)
tail = tokenizer.encode('\nContinue this synthetic report with a detailed numbered list.<|im_end|>\n<|im_start|>assistant\n', add_special_tokens=False)
words = ['river', 'forest', 'stone', 'cloud', 'orange', 'silver', 'station', 'garden', 'window', 'planet', 'paper', 'bridge', 'summer', 'valley', 'circle', 'table']
groups = {}
for length in (128, 2048):
    prompts = []
    for index in range(100):
        rng = random.Random(20261104 + length * 100 + index)
        # Уникальная часть запроса расположена до первого повторно используемого блока KV-кэша.
        text = 'Record ' + ''.join(rng.choice('abcdef0123456789') for _ in range(32)) + '\n'
        text += ' '.join(rng.choice(words) for _ in range(length * 2))
        content = tokenizer.encode(text, add_special_tokens=False)
        ids = header + content[:length-len(header)-len(tail)] + tail
        assert len(ids) == length
        prompts.append(ids)
    assert len({tuple(p[:16]) for p in prompts}) == len(prompts)
    groups[str(length)] = prompts
print(json.dumps({'seed':20261104, 'model':json.loads(Path('/models/qwen/.ready.json').read_text()),
 'format':'Qwen chat framing passed as token IDs to /v1/completions',
 'synthetic_only':True, 'prompts':groups}))
'''


def main():
    """Подготовить входы длиной 128 и 2048 токенов и вывести SHA256.

    :raises SystemExit: При ошибке токенизатора или отличии существующего файла.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='results/05-workload.json')
    args = parser.parse_args()
    command = ['bash', str(ROOT/'scripts/kubectl.sh'), '-n', 'kaspersky-llm',
               'exec', '-i', 'deployment/vllm', '-c', 'server', '--', 'python3', '-']
    result = subprocess.run(command, input=PREPARE, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise SystemExit('Tokenizer preparation failed; no runtime payload displayed')
    data = json.loads(result.stdout)
    output = ROOT/args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, separators=(',', ':')) + '\n'
    if output.exists() and output.read_text() != encoded:
        raise SystemExit('Existing workload differs; choose another --output to preserve previous measurements')
    output.write_text(encoded)
    print('Synthetic workload saved:', args.output)
    print('SHA256:', hashlib.sha256(output.read_bytes()).hexdigest())
    print('100 unique prompts per length; exactly 128 and 2048 input tokens')


if __name__ == '__main__':
    main()
