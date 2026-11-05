"""Проверка подготовленных файлов Git без вывода обнаруженных секретов."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def git(*args):
    """Выполнить команду Git в корне проекта.

    :param args: Аргументы команды без имени git.
    :return: Стандартный вывод команды.
    :rtype: bytes
    :raises subprocess.CalledProcessError: Если команда завершилась с ошибкой.
    """
    return subprocess.check_output(['git',*args],cwd=ROOT,stderr=subprocess.DEVNULL)


def main():
    """Проверить подготовленный снимок Git и сохранить отчёт.

    :raises SystemExit: При отсутствии снимка или обнаружении запрещённых данных.
    """
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='.cache/reports/repository.json')
    args=parser.parse_args()
    try:paths=git('ls-files','-z').decode().split('\0')
    except subprocess.SubprocessError:raise SystemExit('Run inside an initialized repository with staged delivery files') from None
    paths=sorted(p for p in paths if p)
    if not paths:raise SystemExit('No staged delivery files to audit')
    known=[]
    for name in ('k8s-api.env','smoke.env'):
        path=ROOT/'.cache'/name
        if path.is_file():
            value=path.read_text().strip().split('=',1)[-1].encode()
            if value:known.append(value)
    if os.environ.get('HF_TOKEN'):known.append(os.environ['HF_TOKEN'].encode())
    problems=[];manifest=[];total=0
    for name in paths:
        p=Path(name)
        if (p.parts[0] in ('.cache','models','secrets','.venv') or
            '__pycache__' in p.parts or p.suffix in ('.docx','.pyc','.key') or
            (p.name.startswith('.env') and p.name!='.env.example')):
            problems.append({'path':name,'category':'private_or_generated_path'})
        data=git('show',':'+name)
        total+=len(data)
        if any(value in data for value in known):problems.append({'path':name,'category':'known_credential'})
        if re.search(rb'hf_[A-Za-z0-9]{20,}',data):problems.append({'path':name,'category':'hf_token_pattern'})
        if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=\r\n]{40,}-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',data):
            problems.append({'path':name,'category':'private_key'})
        manifest.append({'path':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
    encoded=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    report={'scope':'Staged Git blobs; known local credentials, HF token pattern, PEM private-key blocks and excluded paths',
            'file_count':len(paths),'total_bytes':total,'manifest_sha256':hashlib.sha256(encoded).hexdigest(),
            'manifest':manifest,'history_commit_count':int(git('rev-list','--all','--count').strip() or b'0'),
            'findings':problems,'all_passed':not problems}
    output=ROOT/args.output;output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(f"{'PASS' if not problems else 'FAIL'} staged repository audit: {len(paths)} files; {len(problems)} findings")
    if problems:raise SystemExit('Inspect metadata report; matched values are not displayed')


if __name__=='__main__':main()
