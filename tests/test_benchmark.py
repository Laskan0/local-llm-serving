"""Проверки измерения задержек и завершённости потока SSE."""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('benchmark',Path(__file__).resolve().parents[1]/'scripts/benchmark.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def event(value):
    return b'data: '+json.dumps(value).encode()+b'\n'


class BenchmarkTests(unittest.TestCase):
    def test_ttft_waits_for_content_and_usage_survives_empty_choices(self):
        stream = [b': keepalive\n',event({'choices':[{'text':''}]}),
                  event({'choices':[{'text':'synthetic'}]}),
                  event({'choices':[{'text':'','finish_reason':'length'}]}),
                  event({'choices':[],'usage':{'prompt_tokens':128,'completion_tokens':64}}),
                  b'data: [DONE]\n']
        with patch.object(benchmark.time,'perf_counter',side_effect=[11,15]):
            result=benchmark.parse_stream(stream,10)
        self.assertEqual(result['ttft_s'],1)
        self.assertEqual(result['latency_s'],5)
        self.assertEqual(result['output_tokens'],64)
        self.assertTrue(result['stream_done'])
        self.assertNotIn('synthetic',json.dumps(result))

    def test_missing_done_and_usage_are_visible(self):
        result=benchmark.parse_stream([event({'choices':[{'text':'partial'}]})],0)
        self.assertFalse(result['stream_done'])
        self.assertIsNone(result['output_tokens'])

    def test_stream_error_is_not_misclassified_as_success(self):
        with self.assertRaises(ValueError):
            benchmark.parse_stream([event({'error':{'message':'private text'}})],0)

    def test_small_sample_p95_uses_maximum(self):
        self.assertEqual(benchmark.percentile(list(range(1,13)),95),12)
        self.assertIsNone(benchmark.percentile([],95))


if __name__=='__main__':
    unittest.main()
