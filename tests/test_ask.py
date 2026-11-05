"""Проверки потокового клиента, обработки ошибок и истории диалога."""

import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ask', Path(__file__).resolve().parents[1] / 'scripts/ask.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def event(value):
    return b'data: ' + json.dumps(value).encode() + b'\n'


class Response:
    def __init__(self, lines):
        self.lines = lines
        self.exhausted = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __iter__(self):
        yield from self.lines
        self.exhausted = True


class AskTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(base_url='http://127.0.0.1:18000', max_tokens=32, timeout=10)

    def test_stream_prints_text_and_reads_to_eof(self):
        response = Response([
            b': keepalive\n', event({'choices': [{'delta': {'role': 'assistant'}}]}),
            event({'choices': [{'delta': {'content': 'При'}}]}),
            event({'choices': [{'delta': {'content': 'вет'}, 'finish_reason': 'stop'}]}),
            b'data: [DONE]\n', b'\n'])
        output = io.StringIO()
        with patch.object(client.urllib.request, 'urlopen', return_value=response) as call:
            self.assertEqual(client.ask([{'role': 'user', 'content': 'test'}], 'test-key', self.args, output), 'Привет')
        self.assertEqual(output.getvalue(), 'Привет\n')
        self.assertTrue(response.exhausted)
        body = json.loads(call.call_args.args[0].data)
        self.assertTrue(body['stream'])
        self.assertEqual(body['model'], 'qwen')

    def test_incomplete_empty_and_error_streams_are_failures(self):
        cases = [
            [event({'choices': [{'delta': {'content': 'partial'}}]})],
            [event({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}), b'data: [DONE]\n'],
            [event({'error': {'message': 'private-payload'}})],
            [b'data: invalid-json\n'],
        ]
        for lines in cases:
            with self.subTest(lines=lines), patch.object(client.urllib.request, 'urlopen', return_value=Response(lines)):
                with self.assertRaises(client.ClientError) as caught:
                    client.ask([], 'key', self.args, io.StringIO())
                self.assertNotIn('private-payload', str(caught.exception))

    def test_auth_and_connection_errors_have_safe_messages(self):
        for error in [urllib.error.HTTPError('local', 401, 'private-key', {}, io.BytesIO(b'private-body')),
                      urllib.error.URLError('private-key')]:
            with patch.object(client.urllib.request, 'urlopen', side_effect=error):
                with self.assertRaises(client.ClientError) as caught:
                    client.ask([], 'private-key', self.args, io.StringIO())
                self.assertNotIn('private', str(caught.exception))

    def test_token_file_requires_named_nonempty_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'token.env'
            for value in ['OTHER=wrong', 'VLLM_API_KEY=']:
                path.write_text(value)
                with self.assertRaises(client.ClientError):
                    client.read_token(path)
            path.write_text('OTHER=ignored\nVLLM_API_KEY=test-key\n')
            self.assertEqual(client.read_token(path), 'test-key')

    def test_history_reset_and_failed_turn_not_committed(self):
        with patch('builtins.input', side_effect=['one', 'failed', 'two', '/reset', 'three', '/exit']), \
             patch.object(client, 'ask', side_effect=['first', client.ClientError('failed'), 'second', 'third']) as ask, \
             patch.object(client.sys, 'stdout', io.StringIO()), patch.object(client.sys, 'stderr', io.StringIO()):
            self.assertEqual(client.conversation('key', self.args), 0)
        self.assertEqual(ask.call_args_list[2].args[0], [
            {'role': 'user', 'content': 'one'}, {'role': 'assistant', 'content': 'first'},
            {'role': 'user', 'content': 'two'}])
        self.assertEqual(ask.call_args_list[3].args[0], [{'role': 'user', 'content': 'three'}])


if __name__ == '__main__':
    unittest.main()
