import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import quality_contract as qc
import capacity_policy as cp


class QualityContractTests(unittest.TestCase):
    def test_ui_requires_viewports_content_and_evidence(self):
        row = {
            'format':'ui-quality/1','task':'inbox','target_users':'support agent',
            'viewports':[{'name':'desktop','width':1440,'height':900,'evidence':'desktop.png'},
                         {'name':'narrow','width':1280,'height':800,'evidence':'narrow.png'}],
            'paths':[{'id':'primary','functional':'PASS','visual':'PASS','accessibility':'PASS','evidence_refs':['click.json']}],
            'content_checks':{'real_content':True,'no_explanation_overload':True,'states_covered':['empty','error','success']},
            'explanatory_copy_ratio':0.2,
        }
        self.assertEqual('PASS', qc.validate_ui(row)['status'])
        row['paths'][0]['visual']='FAIL'
        self.assertEqual('FAIL', qc.validate_ui(row)['status'])

    def test_system_requires_capacity_controls(self):
        row={'format':'system-design/1','system':'orders',
             'traffic_profile':{'peak_rps':100,'burst_rps':300,'concurrency':500,'payload_kb':4},
             'slos':{'availability_target':99.9,'latency_p95_ms':300,'error_rate_target':0.01},
             'capacity_model':{'instance_capacity_rps':500,'instances':8,'headroom_ratio':1.5,'expected_concurrency':30},
             'controls':{key:True for key in ('idempotency','backpressure','bounded_retries','authz_audit','migration_rollback','observability','load_test','recovery_drill')},
             'evidence_refs':['load-test.json']}
        self.assertEqual('PASS', qc.validate_system(row)['status'])
        row['controls']['load_test']='unknown'
        self.assertEqual('INCOMPLETE', qc.validate_system(row)['status'])

    def test_host_and_autonomy_require_boundaries(self):
        host={'format':'host-adapter/1','host_id':'fixture','host_version':'1','platform':'windows',
              'capabilities':{'fs.read':'supported','shell.exec':'supported'},
              'required_capabilities':['fs.read','shell.exec'],
              'phases':['install','load','invoke','route','verify'],
              'observations':[{'phase:p':'x'}]}
        host['observations']=[{'phase':phase,'status':'PASS'} for phase in ['install','load','invoke','route','verify']]
        self.assertEqual('PASS',qc.validate_host(host)['status'])
        host['capabilities']['shell.exec']='unknown'
        self.assertEqual('INCOMPLETE',qc.validate_host(host)['status'])
        auto={'format':'autonomy-cycle/1','signals':[{'id':'bug','source':'issue','status':'PASS','observed_at':'2026-10-27'}],
              'authorization':{'scope':'local repo','allowed_actions':['edit'],'forbidden_actions':['publish']},
              'next_action':{'id':'fix','kind':'repair','reason':'known failure','estimated_cost':1,'rollback':'revert commit'},'budget':2,
              'stop_conditions':['budget','unknown state'],'recovery':{'on_error':'diagnose','unknown_state':'pause','external_result_unknown':'reconcile'}}
        self.assertEqual('PASS',qc.validate_autonomy(auto)['status'])

    def test_capacity_policy_does_not_silently_switch_model(self):
        result=cp.classify("Selected model is at capacity", attempt=2)
        self.assertEqual("RETRY_LATER", result["status"])
        self.assertFalse(result["switch_model"])
        self.assertTrue(result["continue_independent_work"])
        self.assertEqual("STOP_NEEDS_ACCOUNT_ACTION", cp.classify("usage limit reached")["status"])

    def test_compare_refuses_small_or_censored_sample(self):
        rows=[]
        for i in range(3):
            for arm in ('baseline','solo-1.0'):
                rows.append({'task_id':str(i),'arm':arm,'status':'completed','evaluation':{'task_success':True,'quality_score':100},'elapsed_seconds':2,'usage':{'usage':{'total_tokens':10}}})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'results.jsonl'; path.write_text('\n'.join(json.dumps(x) for x in rows),encoding='utf-8')
            result=qc.compare_results(path)
            self.assertEqual('PASS',result['status']); self.assertEqual(3,result['valid_completed_pairs'])
            rows[0]['status']='censored'
            path.write_text('\n'.join(json.dumps(x) for x in rows),encoding='utf-8')
            self.assertEqual('INCOMPLETE',qc.compare_results(path)['status'])


if __name__=='__main__': unittest.main()
