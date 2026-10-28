import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from backend import app


class ChatTests(unittest.TestCase):
    def invoke(self, replies):
        analysis=dict(id='chat-test',suggestions=[dict(id='insight')],context={},rules={})
        model=Mock(side_effect=replies)
        db=Mock()
        with patch.object(app,'run',return_value=analysis),patch.object(app,'history',return_value=[]),patch.object(app,'build_evidence',return_value={'cash':{'display':'USD 1,000'}}),patch.object(app,'funding_plan_tool',return_value={}),patch.object(app,'ask_model',model),patch.object(app,'database',return_value=nullcontext(db)):
            result=app.chat(app.Chat(run_id='chat-test',suggestion_id='insight',message='how do i fix ?'))
        return result,model,db

    def test_valid_answer_needs_no_retry(self):
        result,model,_=self.invoke(['Confirm funding before committing cash.'])
        self.assertEqual(result['mode'],'Live AI')
        self.assertEqual(model.call_count,1)

    def test_numerical_wording_is_returned_unchanged_without_warning_or_retry(self):
        for text in ('Convert USD 999.','Review USD 1.05 million by November 17, 2026.','1. Confirm receipts. 2. Review the 111% ratio.'):
            with self.subTest(text=text):
                result,model,db=self.invoke([text])
                self.assertEqual(result['mode'],'Live AI')
                self.assertEqual(model.call_count,1)
                self.assertEqual(result['answer'],text)
                self.assertEqual(db.executemany.call_args.args[1][1][-1],text)

    def test_service_failure_does_not_leak_provider_details(self):
        result,model,_=self.invoke([RuntimeError('private provider body')])
        self.assertEqual(result['mode'],'Analyst unavailable')
        self.assertNotIn('private',result['answer'])
        self.assertEqual(model.call_count,1)
