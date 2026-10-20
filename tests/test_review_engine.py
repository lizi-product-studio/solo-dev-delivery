import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from review_helpers import assess,bind_all,review_data

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('delivery_reviews',ROOT/'scripts/delivery.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.repo=self.root/'repo';self.repo.mkdir()
        self.git=lambda *a:subprocess.run(['git','-C',str(self.repo),*a],capture_output=True,text=True,check=True)
        self.git('init');self.git('config','user.name','Review fixture');self.git('config','user.email','fixture@example.invalid')
        (self.repo/'app.txt').write_text('original')
        (self.repo/'test_probe.py').write_text("import unittest\nfrom pathlib import Path\nclass Probe(unittest.TestCase):\n def test_file(self): self.assertEqual(Path('app.txt').read_text(), 'original')\n")
        (self.repo/'.gitignore').write_text('__pycache__/\n')
        self.git('add','.');self.git('commit','-m','baseline')
        self.q=d.Queue(self.root/'runtime');self.proof=self.root/'review.txt'
        self.proof.write_text('Synthetic fixture assessment/review; not a real independent agent receipt.')
        brief=self.root/'brief.md';brief.write_text('Review engine synthetic fixture')
        self.t=self.q.ingest(brief)
        self.c={'goal':'local fixture','repository':str(self.repo),'owner':'fixture','approval_ref':'SYNTHETIC_ONLY',
            'autonomy':'local_until_delivery','rollback':'revert fixture commit','scope':['app.txt','test_probe.py'],
            'excluded':['production'],'acceptance':[{'id':'A','expected':'fixture stays original','environment':'isolated'}],
            'risk_signals':['copy_change'],'estimated_business_files':1,
            'source_resolutions':[{'id':'manual-brief-review','decision':'fixture only','evidence':'this isolated test'}],
            'open_business_decisions':[],'repository_idle_confirmed':True,'ux_required':False,
            'review_components':[{'id':'main','paths':['app.txt','test_probe.py','.gitignore'],'depends_on':[]}]}

    def tearDown(self):self.q.db.close();self.temp.cleanup()

    def approve(self):
        self.t=self.q.approve(self.t['id'],self.c);return self.t

    def start(self):
        self.approve();assess(self.q,self.t,'impl',self.proof);self.q.claim(self.t['id'],'impl')

    def passes(self):
        bind_all(self.q,self.t,'impl');return self.q.reviews.run(self.t['id'],'impl')

    def review(self,actor=None):
        p=self.q.reviews.plan(self.t['id']);actor=actor or ('qa' if p['independent_required'] else 'impl')
        return self.q.reviews.record(self.t['id'],actor,review_data(self.q,self.t,self.proof,d.frozen_commit(self.repo)))

    def finding(self,fid='F1',severity='P2',family='object_identity',components=None):
        return self.q.reviews.finding(self.t['id'],'qa',{'id':fid,'severity':severity,'family':family,
            'components':components or ['main'],'description':'actual fixture issue','reproduction':'perform ordered counterexample',
            'evidence':str(self.proof)})

    def change(self,path,text):
        p=self.repo/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        self.git('add',path);self.git('commit','-m','fixture delta')

    def test_packet_carries_current_ux_lessons_without_new_checks(self):
        self.c['ux_required']=True
        self.start()
        before=self.q.reviews.plan(self.t['id'])
        packet=self.q.reviews.packet(self.t['id'])
        brief=packet['review_brief']
        self.assertEqual(self.c['goal'],brief['goal'])
        self.assertEqual(self.c['scope'],brief['scope'])
        self.assertEqual(['M01','M03'],[m['id'] for m in brief['focus']])
        self.assertEqual(before,self.q.reviews.plan(self.t['id']))

    def test_approval_automatically_requires_review_before_claim(self):
        self.approve();p=self.q.reviews.plan(self.t['id'])
        self.assertEqual(p['next_action'],'ASSESS_REVIEW_PLAN')
        with self.assertRaisesRegex(ValueError,'PREFLIGHT'):self.q.claim(self.t['id'],'impl')
        self.assertTrue(self.q.scan()['review_actions'])

    def test_payment_plan_has_money_check_without_user_reminder(self):
        self.c['risk_signals']=['payment'];self.approve()
        p=self.q.reviews.plan(self.t['id']);self.assertIn('RISK:money',p['checks']);self.assertTrue(p['independent_required'])

    def test_path_risk_cannot_be_hidden_by_copy_change_signal(self):
        self.c['review_components'][0]['paths']=['.','refund.py'];self.approve()
        self.assertIn('RISK:money',self.q.reviews.plan(self.t['id'])['checks'])

    def test_new_risky_file_reopens_preflight_and_invalidates_review(self):
        self.c['review_components'][0]['paths']=['.'];self.start();self.passes();self.review()
        self.change('refund.py','FLAG=True\n')
        p=self.q.reviews.refresh(self.t['id'])
        self.assertEqual('ASSESS_REVIEW_PLAN',p['next_action']);self.assertIn('RISK:money',p['checks'])
        with self.assertRaises(ValueError):self.q.reviews.enforce(self.t['id'])

    def test_missing_tests_cannot_be_replaced_by_pass_report(self):
        self.start()
        with self.assertRaisesRegex(ValueError,'必需检查'):self.review()
        with self.assertRaisesRegex(ValueError,'QUALITY_GATE'):self.q.check_report(self.q.task(self.t['id']),{'open_p0_p1':[]})

    def test_actual_runner_receipt_and_unchanged_checks_reused(self):
        self.start();self.passes();s=self.q.reviews.load(self.t['id'])
        self.assertEqual(1,s['runs'][-1]['tests']);self.assertEqual('PASS',s['runs'][-1]['status'])
        count=len(s['runs']);self.q.reviews.run(self.t['id'],'impl')
        self.assertEqual(count,len(self.q.reviews.load(self.t['id'])['runs']))
        self.assertTrue(self.review()['delivery_allowed'])

    def test_blocked_task_never_reports_deliverable(self):
        self.start();self.passes();self.review()
        self.q.block(self.t['id'],'impl','external prerequisite')
        self.assertFalse(self.q.reviews.plan(self.t['id'])['delivery_allowed'])

    def test_one_command_serves_multiple_obligations_without_duplicate_processes(self):
        self.c['risk_signals']=['backend_frontend'];self.start();self.passes()
        s=self.q.reviews.load(self.t['id']);self.assertGreater(len(s['runs']),1)
        self.assertEqual(1,len({r['id'] for r in s['runs']}))

    def test_failed_actual_test_blocks_and_retry_budget_stops_loop(self):
        self.start();self.change('app.txt','broken');bind_all(self.q,self.t,'impl')
        p=self.q.reviews.run(self.t['id'],'impl');self.assertFalse(p['delivery_allowed'])
        self.assertEqual('FAIL',self.q.reviews.load(self.t['id'])['runs'][-1]['status'])
        self.q.reviews.run(self.t['id'],'impl')
        with self.assertRaisesRegex(ValueError,'RETRY_EXHAUSTED'):self.q.reviews.run(self.t['id'],'impl')
        self.change('app.txt','original');self.q.reviews.run(self.t['id'],'impl')
        self.assertTrue(self.review()['delivery_allowed'])

    def test_empty_or_skipped_unittest_is_not_pass(self):
        self.start();self.change('test_probe.py','import unittest\n')
        bind_all(self.q,self.t,'impl');self.q.reviews.run(self.t['id'],'impl')
        self.assertEqual('INCOMPLETE',self.q.reviews.load(self.t['id'])['runs'][-1]['status'])
        self.change('test_probe.py',"import unittest\nclass P(unittest.TestCase):\n @unittest.skip('missing env')\n def test_a(self): self.fail()\n")
        self.q.reviews.run(self.t['id'],'impl')
        self.assertEqual('INCOMPLETE',self.q.reviews.load(self.t['id'])['runs'][-1]['status'])

    def test_timeout_is_recorded_and_process_terminated(self):
        self.start();self.change('test_probe.py','import unittest,time\nclass P(unittest.TestCase):\n def test_a(self): time.sleep(30)\n')
        p=self.q.reviews.plan(self.t['id']);self.q.reviews.bind(self.t['id'],'impl',{'revision':p['revision'],'local_only':True,'checks':{'AC:A':{'argv':[sys.executable,'-m','unittest','test_probe.py'],'adapter':'unittest','environment':'isolated','rationale':'timeout fixture','timeout':1}}})
        self.q.reviews.run(self.t['id'],'impl')
        self.assertEqual('TIMEOUT',self.q.reviews.load(self.t['id'])['runs'][-1]['status'])
        self.assertIsNone(self.q.reviews.load(self.t['id'])['active_run'])

    def test_test_that_changes_source_cannot_pass(self):
        self.start();self.change('test_probe.py',"import unittest\nfrom pathlib import Path\nclass P(unittest.TestCase):\n def test_a(self): Path('app.txt').write_text('mutated');self.assertTrue(True)\n")
        self.passes();self.assertEqual('STALE',self.q.reviews.load(self.t['id'])['runs'][-1]['status'])

    def test_scope_expansion_blocks_execution(self):
        self.start();self.change('other.py','x=1\n');bind_all(self.q,self.t,'impl')
        self.assertIn('OUTSIDE_REVIEW_SCOPE',self.q.reviews.plan(self.t['id'])['blockers'])
        with self.assertRaisesRegex(ValueError,'超出'):self.q.reviews.run(self.t['id'],'impl')

    def test_commands_are_not_read_from_brief_or_shell(self):
        self.start();p=self.q.reviews.plan(self.t['id'])
        with self.assertRaisesRegex(ValueError,'shell'):self.q.reviews.bind(self.t['id'],'impl',{'revision':p['revision'],'local_only':True,'checks':{'AC:A':{'argv':['sh','-c','touch SENTINEL'],'adapter':'process','environment':'isolated','rationale':'no','timeout':1}}})
        self.assertFalse((self.repo/'SENTINEL').exists())

    def test_missing_or_altered_assessment_is_not_accepted(self):
        self.approve();assess(self.q,self.t,'impl',self.proof);self.proof.write_text('changed')
        with self.assertRaises(ValueError):self.q.claim(self.t['id'],'impl')

    def test_finding_creates_regression_and_root_cause_sweep(self):
        self.start();self.passes();self.review();p=self.finding()
        self.assertIn('REG:F1',p['checks']);self.assertIn('SWEEP:F1:main',p['checks'])
        self.assertIn('OPEN_FINDING:F1',p['blockers']);self.assertTrue(p['independent_required'])
        self.assertIsNone(self.q.reviews.load(self.t['id'])['review'])

    def test_duplicate_finding_is_idempotent(self):
        self.start();a=self.finding();b=self.finding();self.assertEqual(a['revision'],b['revision'])

    def test_critical_and_repeated_findings_expand_related_not_unrelated(self):
        self.c['review_components']=[{'id':'main','paths':['.'],'depends_on':['shared']},
            {'id':'shared','paths':['shared'],'depends_on':['consumer']},
            {'id':'consumer','paths':['consumer'],'depends_on':[]},
            {'id':'unrelated','paths':['copy'],'depends_on':[]}]
        self.start();p=self.finding('F1','P2')
        self.assertEqual(['main','shared'],p['findings']['F1']['expanded_components'])
        p=self.finding('F2','P2')
        self.assertEqual(['consumer','main','shared'],p['findings']['F2']['expanded_components'])
        p=self.finding('F3','P1',family='data_integrity')
        self.assertNotIn('unrelated',p['findings']['F3']['expanded_components'])

    def test_pass_record_cannot_suppress_new_finding_with_empty_list(self):
        self.start();self.finding();self.passes()
        r=review_data(self.q,self.t,self.proof,d.frozen_commit(self.repo));r['resolved_findings']=[]
        with self.assertRaisesRegex(ValueError,'未关闭缺陷'):self.q.reviews.record(self.t['id'],'qa',r)
        self.assertEqual('OPEN',self.q.reviews.load(self.t['id'])['findings']['F1']['status'])

    def test_self_review_rejected_after_finding_even_on_quick_task(self):
        self.start();self.finding();self.passes()
        with self.assertRaisesRegex(ValueError,'实现者'):self.review('impl')
        self.assertTrue(self.review('qa')['delivery_allowed'])

    def test_stale_review_and_missing_host_receipt_rejected(self):
        self.c['risk_signals']=['backend_frontend'];self.start();self.passes()
        r=review_data(self.q,self.t,self.proof,d.frozen_commit(self.repo));r['revision']-=1
        with self.assertRaisesRegex(ValueError,'过期'):self.q.reviews.record(self.t['id'],'qa',r)
        r=review_data(self.q,self.t,self.proof,d.frozen_commit(self.repo));r['independence_evidence']=str(self.root/'missing')
        with self.assertRaises(ValueError):self.q.reviews.record(self.t['id'],'qa',r)

    def test_new_code_invalidates_old_pass_and_old_defect_closure(self):
        self.start();self.finding();self.passes();self.review('qa')
        self.change('app.txt','regressed')
        with self.assertRaises(ValueError):self.q.reviews.enforce(self.t['id'])

    def test_result_log_tamper_blocks_delivery(self):
        self.start();self.passes();self.review()
        s=self.q.reviews.load(self.t['id']);Path(s['runs'][0]['evidence']['path']).write_text('forged')
        with self.assertRaises(ValueError):self.q.reviews.enforce(self.t['id'])

    def test_restart_preserves_pending_checks_and_findings(self):
        self.start();self.finding();self.q.db.close();self.q=d.Queue(self.root/'runtime')
        p=self.q.reviews.plan(self.t['id']);self.assertIn('OPEN_FINDING:F1',p['blockers']);self.assertIn('REG:F1',p['checks'])

    def test_unknown_legacy_state_fails_closed_without_changing_completed_tasks(self):
        self.start();self.q.db.execute('delete from review_state where task_id=?',(self.t['id'],));self.q.db.commit()
        with self.assertRaisesRegex(ValueError,'MIGRATION_REQUIRED'):self.q.reviews.enforce(self.t['id'])
        p=self.q.reviews.adopt(self.t['id'],'impl',d.frozen_commit(self.repo));self.assertEqual('ASSESS_REVIEW_PLAN',p['next_action'])

    def test_late_finding_withdraws_delivery_and_requires_followup(self):
        self.start();self.passes();self.review()
        self.q.db.execute("update tasks set status='DELIVERED' where id=?",(self.t['id'],));self.q.db.commit()
        p=self.finding();self.assertEqual('QUALITY_HOLD',p['task_status']);self.assertEqual('CREATE_REPAIR_TASK',p['next_action'])
        with self.assertRaises(ValueError):self.q.reviews.enforce(self.t['id'])

    def test_partial_reviewer_coverage_cannot_pass(self):
        self.start();self.passes();r=review_data(self.q,self.t,self.proof,d.frozen_commit(self.repo));r['observations']={}
        with self.assertRaisesRegex(ValueError,'逐项'):self.q.reviews.record(self.t['id'],'impl',r)

    def test_live_run_cannot_be_reconciled_or_duplicated(self):
        self.start()
        with self.q.transaction():
            s=self.q.reviews.load(self.t['id']);s['active_run']={'id':'fixture','pid':os.getpid()};self.q.reviews.save(self.t['id'],s)
        with self.assertRaisesRegex(ValueError,'存活'):self.q.reviews.reconcile(self.t['id'],'impl',str(self.proof))
        with self.assertRaisesRegex(ValueError,'运行'):self.q.reviews.run(self.t['id'],'impl')

if __name__=='__main__':unittest.main(verbosity=2)
