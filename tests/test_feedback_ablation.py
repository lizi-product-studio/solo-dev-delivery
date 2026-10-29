import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from verify_feedback_ablation import summarize,failure_class


class FeedbackAblationTests(unittest.TestCase):
    def row(self,arm,success,**extra):
        return {'id':'software','repetition':1,'arm':arm,'task_success':success,
                'infrastructure_failure':None,**extra}

    def test_observed_task_failures_remain_in_denominator(self):
        result=summarize([self.row('baseline',False),self.row('feedback',True)])
        self.assertEqual(1,result['valid_pairs'])
        self.assertEqual(1,result['feedback_wins'])
        self.assertEqual(0,result['baseline_successes'])

    def test_capacity_and_missing_arms_stay_incomplete(self):
        result=summarize([self.row('baseline',False,infrastructure_failure='capacity_or_quota'),self.row('feedback',True)])
        self.assertEqual('INCOMPLETE',result['status'])
        self.assertEqual(0,result['valid_pairs'])
        self.assertEqual(1,result['censored_pairs'])
        self.assertEqual('INCOMPLETE',summarize([self.row('baseline',True)])['status'])

    def test_duplicate_trials_cannot_silently_overwrite_failure(self):
        with self.assertRaises(ValueError):summarize([self.row('baseline',False),self.row('baseline',True)])

    def test_model_mismatch_and_missing_planned_pairs_are_incomplete(self):
        result=summarize([self.row('baseline',True,requested_model='a'),self.row('feedback',True,requested_model='b')])
        self.assertEqual('INCOMPLETE',result['status'])
        self.assertEqual(0,result['valid_pairs'])
        result=summarize([self.row('baseline',True),self.row('feedback',True)],expected_pairs=2)
        self.assertEqual('INCOMPLETE',result['status'])

    def test_task_answers_and_protocol_errors_are_not_censored(self):
        reports=[{'status':'COMPLETED','answer':'The network configuration report is complete.'},
                 {'status':'COMPLETED','answer':'Document forbidden actions and usage quota.'},
                 {'status':'MODEL_ERROR','answer':'decision model emitted a forbidden host item'},
                 {'status':'MODEL_ERROR','answer':'Codex returned invalid decision JSON'},
                 {'status':'MODEL_ERROR','answer':'invalid decision argument JSON'},
                 {'status':'MODEL_ERROR','answer':'Codex server at capacity'},
                 {'status':'MODEL_ERROR','answer':'Codex exit 1: [{"type":"error","message":"network timeout"}]'},
                 {'status':'MODEL_ERROR','answer':'Codex exit 1: not JSON network failure'},
                 {'error':'TimeoutExpired: process timed out'}]
        for report in reports:
            with self.subTest(report=report):self.assertIsNone(failure_class(report))
        failed={'status':'MODEL_ERROR','answer':'decision model emitted a forbidden host item'}
        result=summarize([self.row('baseline',False,infrastructure_failure=failure_class(failed)),
                          self.row('feedback',True)])
        self.assertEqual(1,result['valid_pairs'])
        self.assertEqual(1,result['feedback_wins'])

    def test_identifiable_adapter_failures_can_be_censored(self):
        for code,expected in [('capacity_error','capacity_or_quota'),(429,'capacity_or_quota'),
                              ('invalid_api_key','identity_or_permission'),(403,'identity_or_permission'),
                              ('transport_error','transport_or_timeout'),(503,'transport_or_timeout')]:
            message='Codex exit 1: '+json.dumps([{'type':'turn.failed','error':{'code':code}}])+' stderr'
            report={'status':'MODEL_ERROR','answer':message}
            with self.subTest(code=code):self.assertEqual(expected,failure_class(report))
            self.assertEqual(message,report['answer'])
        timeout='Codex timeout; result unknown, inspect persisted logs before retry'
        self.assertEqual('transport_or_timeout',failure_class({'status':'MODEL_ERROR','answer':timeout}))
        self.assertIsNone(failure_class({'status':'COMPLETED','answer':timeout}))

    def test_unrecognized_or_non_error_event_codes_do_not_remove_trials(self):
        for event in [{'type':'error','code':'unrecognized'},
                      {'type':'agent_message','code':'capacity_error'},
                      {'type':'error','message':'{"code":"capacity_error"}'}]:
            with self.subTest(event=event):
                self.assertIsNone(failure_class({'status':'MODEL_ERROR',
                                                'answer':'Codex exit 1: '+json.dumps([event])}))


if __name__=='__main__':unittest.main()
