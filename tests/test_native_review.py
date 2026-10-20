"""Native receipt/gate integration with synthetic host exports, no model calls."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_resource_engine as fixtures


class NativeReviewTests(unittest.TestCase):
    setUp = fixtures.ResourceTests.setUp
    git = fixtures.ResourceTests.git
    start = fixtures.ResourceTests.start
    pass_checks = fixtures.ResourceTests.pass_checks
    report = fixtures.ResourceTests.report

    def prepare(self, passed=True):
        self.c['risk_signals']=['backend_frontend']
        self.start()
        if passed:self.pass_checks()
        packet=self.q.reviews.packet(self.t['id'])
        dispatch={'agent_name':'synthetic-native-reviewer','source':'UNIT_TEST_NOT_HOST_ATTESTATION'}
        completion={k:packet[k] for k in ('task_id','candidate','revision','contract_hash')}
        completion.update(reviewer_id=dispatch['agent_name'],status='completed',result={
            'status':'PASS','summary':'Synthetic result for gate testing only.','failure_kind':'none',
            'observations':[{'check_id':cid,'observed':'Actual fixture unittest result inspected.'}
                            for cid in packet['checks']],
            'findings':[],'resolved_findings':[],'uncovered':[]})
        self.files={}
        for key,value in [('packet',packet),('dispatch_evidence',dispatch),('completion_evidence',completion)]:
            p=self.root/(key+'.json');p.write_text(json.dumps(value));self.files[key]=str(p)
        self.completion=completion
        return {**self.files,'host_verified':True}

    def update_completion(self):
        Path(self.files['completion_evidence']).write_text(json.dumps(self.completion))

    def record(self,data):
        return self.q.resources.native_review(self.t['id'],'impl',data)

    def test_native_review_delivers_without_cli_and_keeps_evidence(self):
        data=self.prepare()
        fixtures.engine.shutil.which.reset_mock()
        with patch.object(fixtures.engine.Resources,'run',side_effect=AssertionError('Nested CLI dispatch')):
            result=self.record(data)
        fixtures.engine.shutil.which.assert_not_called()
        self.assertEqual('SUCCESS',result['status'])
        review=self.q.reviews.load(self.t['id'])['review']
        self.assertEqual('codex-native:synthetic-native-reviewer',review['actor'])
        self.q.submit(self.t['id'],'impl',self.report())
        self.assertEqual('DELIVERED',self.q.finish(self.t['id'],'impl')['status'])
        row=self.q.resources.rows(self.t['id'])[0]['receipt']
        self.assertEqual('codex_native',row['backend'])
        self.assertIsNone(row['result']['usage'])
        self.assertEqual('NOT_MEASURED',row['result']['elapsed_measurement'])

    def test_duplicate_native_receipt_does_not_duplicate_review(self):
        data=self.prepare();first=self.record(data);again=self.record(data)
        self.assertTrue(again['reused']);self.assertEqual(first['run_id'],again['run_id'])
        self.assertEqual(1,len(self.q.resources.rows(self.t['id'])))

    def test_owner_self_review_and_dispatch_mismatch_are_rejected(self):
        data=self.prepare()
        with self.assertRaises(ValueError):self.q.resources.native_review(self.t['id'],'another-owner',data)
        self.completion['reviewer_id']='impl';self.update_completion()
        with self.assertRaisesRegex(ValueError,'INDEPENDENT'):self.record(data)
        self.completion['reviewer_id']='invented';self.update_completion()
        with self.assertRaisesRegex(ValueError,'IDENTITY_MISMATCH'):self.record(data)

    def test_missing_host_confirmation_and_incomplete_agent_are_rejected(self):
        data=self.prepare()
        with self.assertRaisesRegex(ValueError,'HOST_VERIFICATION'):self.record({**data,'host_verified':False})
        self.completion['status']='running';self.update_completion()
        with self.assertRaisesRegex(ValueError,'NOT_COMPLETED'):self.record(data)

    def test_candidate_revision_contract_and_scope_drift_are_rejected(self):
        data=self.prepare();original=copy.deepcopy(self.completion)
        for key,value in [('candidate','0'*40),('revision',-1),('contract_hash','changed'),('task_id','other-task')]:
            with self.subTest(key=key):
                self.completion=copy.deepcopy(original);self.completion[key]=value;self.update_completion()
                with self.assertRaisesRegex(ValueError,'STALE'):self.record(data)
        self.completion=original;self.update_completion()
        p=Path(self.files['packet']);packet=json.loads(p.read_text());packet['checks']={};p.write_text(json.dumps(packet))
        with self.assertRaisesRegex(ValueError,'SCOPE_MISMATCH'):self.record(data)

    def test_unrun_or_tampered_tests_cannot_be_imported_as_pass(self):
        data=self.prepare(passed=False)
        with self.assertRaisesRegex(ValueError,'CHECKS_NOT_PASSED'):self.record(data)

    def test_missing_duplicate_and_uncovered_observations_block_pass(self):
        data=self.prepare();original=copy.deepcopy(self.completion)
        self.completion['result']['observations'].pop();self.update_completion()
        with self.assertRaisesRegex(ValueError,'COVERAGE_MISSING'):self.record(data)
        self.completion=copy.deepcopy(original)
        self.completion['result']['observations']*=2;self.update_completion()
        with self.assertRaisesRegex(ValueError,'重复'):self.record(data)
        self.completion=original;self.completion['result']['uncovered']=['Unverified journey'];self.update_completion()
        with self.assertRaisesRegex(ValueError,'矛盾'):self.record(data)

    def test_host_export_and_packet_tampering_prevent_delivery(self):
        data=self.prepare();self.record(data)
        for key in ('packet','dispatch_evidence','completion_evidence'):
            with self.subTest(key=key):
                p=Path(self.files[key]);old=p.read_bytes();p.write_bytes(old+b'\n')
                with self.assertRaises(ValueError):self.q.submit(self.t['id'],'impl',self.report())
                p.write_bytes(old)

    def test_findings_expand_checks_and_do_not_allow_delivery(self):
        data=self.prepare();r=self.completion['result'];r['status']='FINDINGS'
        r['findings']=[{'severity':'P2','family':'ui_flow','components':['main'],
                        'description':'Synthetic missing recovery action','reproduction':'Open then fail request'}]
        self.update_completion();result=self.record(data)
        self.assertEqual('FINDINGS',result['status'])
        review=self.q.reviews.load(self.t['id'])
        self.assertTrue(any(cid.startswith('REG:') for cid in review['checks']))
        self.assertTrue(any(f['status']=='OPEN' for f in review['findings'].values()))
        with self.assertRaises(ValueError):self.q.submit(self.t['id'],'impl',self.report())

    def test_blocked_review_is_preserved_without_a_pass(self):
        data=self.prepare();r=self.completion['result'];r.update(status='BLOCKED',failure_kind='environment')
        self.update_completion();result=self.record(data)
        self.assertEqual('BLOCKED',result['status'])
        self.assertIsNone(self.q.reviews.load(self.t['id'])['review'])

    def test_active_legacy_worker_keeps_its_lock(self):
        data=self.prepare()
        with self.q.transaction():
            self.q.db.execute('INSERT INTO resource_runs VALUES (?,?,?,?,?,?)',
                ('synthetic-active',self.t['id'],'work','fixture','RUNNING',json.dumps({'unit':{'role':'implement'}})))
        with self.assertRaisesRegex(ValueError,'仍在运行'):self.record(data)

    def test_changed_candidate_after_native_review_cannot_deliver(self):
        data=self.prepare();self.record(data)
        (self.repo/'left/value.txt').write_text('New candidate after review\n')
        self.git('add','left/value.txt');self.git('commit','-qm','later change')
        with self.assertRaises(ValueError):self.q.submit(self.t['id'],'impl',self.report())


if __name__=='__main__':unittest.main()
