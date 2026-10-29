import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from review_helpers import assess,bind_all,review_data

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('delivery',ROOT/'scripts/delivery.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.repo=self.root/'repo';self.repo.mkdir()
        def git(*args):
            return subprocess.run(['git','-C',str(self.repo),*args],check=True,capture_output=True,text=True)
        self.git=git
        git('init');git('config','user.name','Isolated fixture');git('config','user.email','fixture@example.invalid')
        (self.repo/'app.txt').write_text('original')
        (self.repo/'test_probe.py').write_text("import unittest\nfrom pathlib import Path\nclass Probe(unittest.TestCase):\n def test_file(self): self.assertEqual(Path('app.txt').read_text(), 'original')\n")
        (self.repo/'.gitignore').write_text('__pycache__/\n')
        git('add','.');git('commit','-m','fixture baseline')
        self.q=d.Queue(self.root/'runtime')
        self.brief=self.root/'brief.md';self.brief.write_text('只修改当前示例文案，无业务数据。', encoding='utf-8')
        self.t=self.q.ingest(self.brief)
        self.contract={'goal':'修改示例文案','repository':str(self.repo),'owner':'fixture',
            'approval_ref':'SYNTHETIC_TEST_ONLY','autonomy':'local_until_delivery',
            'rollback':'git revert任务提交','scope':['app.txt'],'excluded':['上线和数据操作'],
            'acceptance':[{'id':'AC1','expected':'文案正确','environment':'local_unit'}],
            'risk_signals':['copy_change'],'estimated_business_files':1,
            'source_resolutions':[{'id':'manual-brief-review','decision':'示例已读','evidence':'SYNTHETIC_TEST_ONLY'}],
            'open_business_decisions':[],'repository_idle_confirmed':True,'ux_required':False}
        self.log=self.root/'test.log';self.log.write_text('SYNTHETIC CHECK EVIDENCE — fixture only\n')

    def tearDown(self):
        self.q.db.close();self.temp.cleanup()

    def running(self, contract=None):
        t=self.q.approve(self.t['id'],contract or self.contract)
        assess(self.q,t,'session-main',self.log)
        self.q.claim(t['id'],'session-main')
        return t

    def report(self,t):
        if t['contract']['route']['task_mode']=='QUICK_PATCH' and not t['contract']['ux_required']:
            bind_all(self.q,t,'session-main')
            self.q.reviews.run(t['id'],'session-main')
            self.q.reviews.record(t['id'],'session-main',review_data(self.q,t,self.log,d.frozen_commit(self.repo)))
        return {'contract_hash':t['contract_hash'],'commit':d.frozen_commit(self.repo),
            'environment':'local','open_p0_p1':[],
            'checks':[{'id':'AC1','status':'PASS','environment':'local_unit','observed':'fixture text','evidence':str(self.log)}],
            'artifacts':[str(self.log)]}

    def report_file(self,r):
        p=self.root/'report.json';p.write_text(json.dumps(r));return p

    def test_import_never_authorizes_and_deduplicates(self):
        self.assertEqual('DRAFT',self.t['status'])
        other=self.root/'copy.md';other.write_bytes(self.brief.read_bytes())
        self.assertEqual(self.t['id'],self.q.ingest(other)['id'])
        with self.assertRaises(ValueError):self.q.claim(self.t['id'],'x')

    def test_human_unknown_blocks(self):
        self.contract['open_business_decisions']=['退款经营规则']
        with self.assertRaises(ValueError):self.q.approve(self.t['id'],self.contract)

    def test_unreviewed_brief_blocks(self):
        self.contract['source_resolutions']=[]
        with self.assertRaises(ValueError):self.q.approve(self.t['id'],self.contract)

    def test_running_owner_cannot_be_reclaimed(self):
        self.running()
        with self.assertRaises(ValueError):self.q.claim(self.t['id'],'session-main')
        with self.assertRaises(ValueError):self.q.block(self.t['id'],'someone-else','reason')

    def test_one_writer_even_across_git_worktrees(self):
        self.running()
        wt=self.root/'worktree';self.git('worktree','add','--detach',str(wt),'HEAD')
        p=self.root/'other.md';p.write_text('second task')
        other=self.q.ingest(p)
        c=copy.deepcopy(self.contract);c['repository']=str(wt)
        approved=self.q.approve(other['id'],c)
        assess(self.q,approved,'session-other',self.log)
        with self.assertRaises(sqlite3.IntegrityError):self.q.claim(other['id'],'session-other')
        self.assertEqual('READY',self.q.task(other['id'])['status'])

    def test_payment_cannot_route_as_quick(self):
        self.contract['risk_signals']=['payment']
        t=self.running()
        self.assertEqual('GOVERNED_DELIVERY',t['contract']['route']['task_mode'])
        with self.assertRaises(ValueError):self.q.submit(t['id'],'session-main',self.report_file(self.report(t)))

    def test_independent_review_cannot_be_self(self):
        self.contract['risk_signals']=['backend_frontend']
        t=self.running();r=self.report(t)
        r['review']={'reviewer':'session-main','result':'PASS','evidence':str(self.log)}
        with self.assertRaises(ValueError):self.q.submit(t['id'],'session-main',self.report_file(r))

    def test_skipped_is_not_pass(self):
        t=self.running();r=self.report(t);r['checks'][0]['status']='SKIP'
        with self.assertRaises(ValueError):self.q.submit(t['id'],'session-main',self.report_file(r))

    def test_environment_cannot_be_substituted(self):
        t=self.running();r=self.report(t);r['checks'][0]['environment']='mock'
        with self.assertRaises(ValueError):self.q.submit(t['id'],'session-main',self.report_file(r))

    def test_ui_needs_real_interaction(self):
        self.contract['ux_required']=True;t=self.running();r=self.report(t)
        r['ux']={'kind':'build_only','evidence':str(self.log)}
        with self.assertRaises(ValueError):self.q.submit(t['id'],'session-main',self.report_file(r))

    def test_missing_evidence_blocks(self):
        t=self.running();r=self.report(t);r['checks'][0]['evidence']=str(self.root/'absent')
        with self.assertRaises(ValueError):self.q.submit(t['id'],'session-main',self.report_file(r))

    def test_changed_requirements_blocks(self):
        t=self.running();r=self.report(t);r['contract_hash']='wrong'
        with self.assertRaises(ValueError):self.q.submit(t['id'],'session-main',self.report_file(r))

    def test_dirty_code_blocks(self):
        t=self.running();r=self.report(t);(self.repo/'app.txt').write_text('changed')
        with self.assertRaises(ValueError):self.q.submit(t['id'],'session-main',self.report_file(r))

    def test_committed_code_drift_blocks_finish(self):
        t=self.running();r=self.report(t);self.q.submit(t['id'],'session-main',self.report_file(r))
        (self.repo/'app.txt').write_text('changed');self.git('add','.');self.git('commit','-m','new version')
        with self.assertRaises(ValueError):self.q.finish(t['id'],'session-main')

    def test_evidence_tamper_blocks_finish(self):
        t=self.running();self.q.submit(t['id'],'session-main',self.report_file(self.report(t)))
        self.log.write_text('different')
        with self.assertRaises(ValueError):self.q.finish(t['id'],'session-main')

    def test_persistent_resume_and_local_delivery(self):
        t=self.running();self.q.checkpoint(t['id'],'session-main',self.log)
        self.q.block(t['id'],'session-main','fixture dependency unavailable')
        self.q.db.close();self.q=d.Queue(self.root/'runtime')
        self.assertEqual('BLOCKED',self.q.task(t['id'])['status'])
        self.q.resume(t['id'],'session-main',self.log)
        self.q.submit(t['id'],'session-main',self.report_file(self.report(t)))
        self.assertEqual('DELIVERED',self.q.finish(t['id'],'session-main')['status'])

    def test_cancel_releases_repo_without_deleting_code(self):
        self.running();self.q.cancel(self.t['id'],'test writer has stopped; synthetic cancel')
        self.assertEqual('CANCELLED',self.q.task(self.t['id'])['status'])
        self.assertTrue((self.repo/'app.txt').exists())

    def test_scan_is_quiet_when_unchanged(self):
        self.assertTrue(self.q.scan()['changed']);self.assertFalse(self.q.scan()['changed'])

    def test_learning_is_deduplicated_candidate_not_permission(self):
        p=self.root/'lesson.json';p.write_text(json.dumps({
            'observation':'fixture observation','candidate_rule':'fixture proposal',
            'applies_when':'fixture','does_not_apply_when':'real production',
            'source_ref':'SYNTHETIC_TEST_ONLY','next_validation':'another case','evidence_file':str(self.log)}))
        one=self.q.learn(self.t['id'],p);two=self.q.learn(self.t['id'],p)
        self.assertEqual(one['id'],two['id']);self.assertEqual('CANDIDATE',one['status'])
        self.assertFalse(one['changes_permissions'])

    def test_real_kickoff_export_retains_pending_decisions(self):
        raw={'app':'project-kickoff','version':1,'project':{'id':'synthetic','mode':'project','data':{
            'types':['admin'],'depth':'demo','goal':'示例','flow':'维护商品',
            'entities':'商品','acceptance':'能保存','scope':'unsure'}}}
        p=self.root/'kickoff.json';p.write_text(json.dumps(raw))
        t=self.q.ingest(p)
        self.assertTrue(t['source']['handoff']['open'])
        with self.assertRaises(ValueError):self.q.approve(t['id'],self.contract)

    def test_idea_is_not_development_task(self):
        raw={'app':'project-kickoff','version':1,'project':{'mode':'idea','data':{}}}
        p=self.root/'idea.json';p.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):self.q.ingest(p)

if __name__=='__main__':unittest.main(verbosity=2)
