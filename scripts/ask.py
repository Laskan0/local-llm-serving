"""Терминальный клиент локального vLLM с потоковым выводом и историей диалога."""

import argparse
import http.client
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ClientError(Exception):
    """Ошибка клиента, предназначенная для вывода без traceback."""
    pass


def read_token(path):
    """Прочитать непустой VLLM_API_KEY из локального файла.

    :param path: Путь к файлу переменных окружения.
    :type path: pathlib.Path
    :return: Токен доступа к API.
    :rtype: str
    :raises ClientError: Если файл недоступен или токен отсутствует.
    """
    try:
        for line in path.read_text().splitlines():
            name, separator, value = line.partition('=')
            if separator and name.strip() == 'VLLM_API_KEY' and value.strip():
                return value.strip()
    except OSError:
        pass
    raise ClientError('Не найден API-токен. Сначала выполните bash scripts/deploy_k8s.sh.')


def ask(messages, token, args, output):
    """Отправить запрос и вывести текстовые фрагменты по мере получения.

    :param messages: История сообщений в формате API.
    :param token: Токен доступа к серверу.
    :param args: Адрес, таймаут и лимит длины ответа.
    :param output: Текстовый поток для вывода.
    :return: Полный полученный текст.
    :rtype: str
    :raises ClientError: При ошибке HTTP, соединения или незавершённом ответе.
    """
    request = urllib.request.Request(
        args.base_url.rstrip('/') + '/v1/chat/completions',
        data=json.dumps({'model': 'qwen', 'messages': messages,
                         'max_tokens': args.max_tokens, 'temperature': 0,
                         'stream': True}).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    pieces = []
    done = False
    finish = None
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            for line in response:
                if not line.startswith(b'data:'):
                    continue
                data = line[5:].strip()
                if data == b'[DONE]':
                    done = True
                    continue  # Сервер завершает ответ и запись аудита до закрытия соединения.
                item = json.loads(data)
                if item.get('error'):
                    raise ClientError('Сервер сообщил об ошибке во время ответа. Попробуйте /reset.')
                for choice in item.get('choices', []):
                    text = choice.get('delta', {}).get('content')
                    if text:
                        if not isinstance(text, str):
                            raise ValueError('Invalid content')
                        output.write(text)
                        output.flush()
                        pieces.append(text)
                    if choice.get('finish_reason'):
                        finish = choice['finish_reason']
    except urllib.error.HTTPError as error:
        # Тело ошибки не выводится: оно может содержать текст запроса или секреты.
        hints = {
            401: 'API-токен не подошёл. Проверьте Secret и файл токена.',
            403: 'Сервер отказал в доступе. Проверьте API-токен.',
            400: 'Запрос отклонён. Возможно, история превысила контекст; используйте /reset.',
            404: 'API не найден. Проверьте адрес сервера и port-forward.',
        }
        raise ClientError(hints.get(error.code, f'Сервер вернул HTTP {error.code}.')) from None
    except (TimeoutError, OSError, urllib.error.URLError, http.client.HTTPException):
        raise ClientError('Нет соединения или истекло время ожидания. Проверьте сервер и port-forward на порту 18000.') from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ClientError('Не удалось прочитать поток ответа сервера.') from None
    if not done or not finish or not ''.join(pieces).strip():
        raise ClientError('Ответ пуст или оборвался. Незавершённый обмен не сохранён в истории.')
    output.write('\n')
    output.flush()
    if finish == 'length':
        print('Ответ достиг лимита длины; увеличить его можно через --max-tokens.', file=sys.stderr)
    return ''.join(pieces)


def conversation(token, args):
    """Обработать вопросы и команды диалога.

    :param token: Токен доступа к серверу.
    :param args: Параметры клиента.
    :return: Нулевой код при штатном завершении.
    :rtype: int
    """
    messages = []
    print('Введите вопрос. /reset — очистить историю, /exit — выйти. История хранится только в памяти.')
    while True:
        try:
            prompt = input('\nВы: ').strip()
        except EOFError:
            print()
            return 0
        if prompt == '/exit':
            return 0
        if prompt == '/reset':
            messages.clear()
            print('История очищена.')
            continue
        if not prompt:
            continue
        candidate = messages + [{'role': 'user', 'content': prompt}]
        print('Модель: ', end='', flush=True)
        try:
            answer = ask(candidate, token, args, sys.stdout)
        except ClientError as error:
            print(f'\nОшибка: {error}', file=sys.stderr)
            continue
        messages = candidate + [{'role': 'assistant', 'content': answer}]


def main():
    """Разобрать аргументы и запустить одиночный запрос или диалог.

    :return: Код завершения клиента.
    :rtype: int
    """
    parser = argparse.ArgumentParser(description='Задать вопрос локальной модели. Без вопроса запускается диалог.')
    parser.add_argument('question', nargs='?', help='Вопрос в кавычках')
    parser.add_argument('--base-url', default='http://127.0.0.1:18000')
    parser.add_argument('--token-file', type=Path, default=ROOT / '.cache/k8s-api.env')
    parser.add_argument('--max-tokens', type=int, default=128, help='Максимум токенов ответа (по умолчанию 128)')
    parser.add_argument('--timeout', type=float, default=240, help='Ожидание сетевых данных, секунды')
    args = parser.parse_args()
    if args.max_tokens < 1 or args.timeout <= 0:
        parser.error('--max-tokens и --timeout должны быть больше нуля')
    try:
        token = read_token(args.token_file)
        if args.question is None:
            return conversation(token, args)
        if not args.question.strip():
            raise ClientError('Введите непустой вопрос.')
        ask([{'role': 'user', 'content': args.question}], token, args, sys.stdout)
        return 0
    except ClientError as error:
        print(f'\nОшибка: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nДиалог завершён.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
