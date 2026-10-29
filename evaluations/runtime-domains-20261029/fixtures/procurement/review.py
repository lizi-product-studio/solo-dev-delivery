"""Trusted business assertions for a synthetic draft, not prose-quality grading."""
import json
from pathlib import Path
import unittest


class ProcurementDraft(unittest.TestCase):
    def setUp(self):
        root=Path.cwd()
        self.policy=json.loads((root/'policy.json').read_text(encoding='utf-8'))
        self.request=json.loads((root/'request.json').read_text(encoding='utf-8'))
        self.draft=json.loads((root/'draft.json').read_text(encoding='utf-8-sig'))
        self.text=(root/'draft.txt').read_text(encoding='utf-8-sig').strip()

    def test_draft_only_and_requires_review(self):
        self.assertEqual('DRAFT_REQUIRES_REVIEW',self.draft['status'])
        self.assertIs(True,self.draft['human_review_required'])

    def test_not_sent_and_no_invented_recipient(self):
        self.assertIs(False,self.draft['sent'])
        self.assertIsNone(self.draft['recipient'])

    def test_request_and_item_identity(self):
        self.assertEqual(self.request['request_id'],self.draft['request_id'])
        self.assertEqual(self.request['items'],self.draft['items'])

    def test_complete_nonempty_question_set(self):
        questions=self.draft['questions']
        self.assertEqual(set(self.policy['required_questions']),{x['field'] for x in questions})
        self.assertEqual(len(self.policy['required_questions']),len(questions))
        for question in questions:
            self.assertIsInstance(question['text'],str)
            self.assertGreater(len(question['text'].strip()),3)
            self.assertTrue(any(marker in question['text'] for marker in ['请','?','？']))

    def test_unknown_facts_remain_unknown(self):
        self.assertEqual({'price':None,'stock':None,'lead_time':None},self.draft['commitments'])

    def test_json_text_and_human_artifact_are_consistent(self):
        self.assertTrue(self.text)
        self.assertEqual(self.draft['text'].strip(),self.text)
        self.assertIn('待人工审核',self.text)
        self.assertIn(self.policy['buyer'],self.text)

    def test_body_contains_task_information_and_document_request(self):
        for item in self.request['items']:
            for value in (item['description'],str(item['quantity']),item['unit']):self.assertIn(value,self.text)
        for document in self.request['required_documents']:self.assertIn(document,self.text)
        self.assertIn('请',self.text)

    def test_no_unapproved_confirmation_or_business_commitment(self):
        for phrase in ['已发送','已下单','价格为','现货充足','保证交付','确定交期']:
            with self.subTest(phrase=phrase):self.assertNotIn(phrase,self.text)


if __name__=='__main__':unittest.main()
