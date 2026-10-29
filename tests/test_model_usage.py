import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from backend import app


class UsageTests(unittest.TestCase):
    @contextmanager
    def workspace(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'RUNTIME',Path(directory)),patch.object(app,'key',return_value='test'):
            yield

    def test_each_provider_response_is_recorded_including_tool_turns(self):
        responses=[{'choices':[{'message':{'role':'assistant','tool_calls':[{'id':'call1','function':{'name':'calculate','arguments':'{}'}}]}}],
                    'usage':{'prompt_tokens':120,'completion_tokens':20,'total_tokens':140,'prompt_tokens_details':{'cached_tokens':100}}},
                   {'choices':[{'message':{'role':'assistant','content':'Done'}}],
                    'usage':{'prompt_tokens':80,'completion_tokens':5,'total_tokens':85}}]
        class FakeResponse:
            def __init__(self,value):self.value=value
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps(self.value).encode()
        def provider(request,**kwargs):
            self.assertIn(b'secret-query',request.data)
            return FakeResponse(responses.pop(0))
        with self.workspace(),patch.object(app.urllib.request,'urlopen',side_effect=provider):
            answer=app.ask_model('Instruction','secret-query',tool_handlers={'calculate':dict(description='Calculate',parameters={},handler=lambda:{'result':1})},
                usage_run_id='run-1',trace_stage='funding_assessment')
            self.assertEqual(answer,'Done')
            report=app.usage()
            self.assertEqual(report['totals']['calls'],2)
            self.assertEqual(report['totals']['input_tokens'],200)
            self.assertEqual(report['totals']['output_tokens'],25)
            self.assertEqual(report['totals']['cached_input_tokens'],100)
            self.assertEqual({row['stage'] for row in report['calls']},{'funding_assessment'})
            self.assertTrue(all(row['run_id']=='run-1' for row in report['calls']))
            self.assertNotIn('secret-query',json.dumps(report))

    def test_missing_usage_is_unknown_and_failure_is_not_a_zero_cost_call(self):
        class FakeResponse:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b'{"choices":[{"message":{"content":"OK"}}]}'
        with self.workspace():
            with patch.object(app.urllib.request,'urlopen',return_value=FakeResponse()):
                app.ask_model('Instruction','Context',usage_run_id='run-2',usage_stage='chat')
            with patch.object(app.urllib.request,'urlopen',side_effect=TimeoutError('private')):
                with self.assertRaises(TimeoutError):
                    app.ask_model('Instruction','Context',usage_run_id='run-2',usage_stage='chat')
            report=app.usage()
            self.assertEqual(report['totals']['unreported_calls'],1)
            self.assertEqual(report['totals']['failed_calls'],1)
            self.assertIsNone(report['totals']['total_tokens'])
            self.assertIsNone(report['calls'][0]['total_tokens'])
            self.assertNotIn('private',json.dumps(report))
