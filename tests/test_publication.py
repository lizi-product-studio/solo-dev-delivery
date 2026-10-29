"""Generation-owned publication and real public CLI publisher races.

The model is a labelled local subprocess double. No external model is used.
"""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];KIT=ROOT/'portable-agent-kit'
sys.path.insert(0,str(KIT))
from agent_runtime.journal import RunJournal
from agent_runtime.publication import publish_report,write_immutable_report,immutable_receipt_path,PublicationError
import agent_runtime.publication as publication
from control_plane.runtime import DeliveryRuntime

spec=importlib.util.spec_from_file_location('publication_cli_fixture',ROOT/'tests/test_task_management_cli.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
CLI=KIT/'scripts/agent_task.py'


class PublicationTests(unittest.TestCase):
    def report(self,receipt,identity,generation,status):
        return {'task_id':'t','status':status,'steps':generation+1,'answer':status,'events':[],
                'invocation_id':identity,'checkpoint_generation':generation,
                'invocation_receipt':str(immutable_receipt_path(receipt,identity))}

    def test_old_generation_and_wrong_invocation_cannot_replace_either_shared_view(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);receipt=root/'receipt.json';database=root/'state.sqlite3';journal=RunJournal(database)
            old=uuid.uuid4().hex;new=uuid.uuid4().hex
            snapshot={'phase':'PAUSED','last_status':'PAUSED','invocation_id':old,'next_step':2,'pending':None,'events':[]}
            journal.create('t',{'fixture':True},snapshot)
            first=self.report(receipt,old,0,'PAUSED');write_immutable_report(receipt,first)
            publish_report(receipt,first,state_db=database,intake={'status':'PAUSED'})
            snapshot.update(phase='FINISHED',last_status='COMPLETED',invocation_id=new,next_step=3)
            journal.save('t',0,snapshot)
            latest=self.report(receipt,new,1,'COMPLETED');write_immutable_report(receipt,latest)
            publish_report(receipt,latest,state_db=database,intake={'status':'COMPLETED'})
            before=(receipt.read_bytes(),(root/'intake-receipt.json').read_bytes())
            self.assertFalse(publish_report(receipt,first,state_db=database,intake={'status':'PAUSED'})['published'])
            forged={**latest,'invocation_id':old}
            self.assertFalse(publish_report(receipt,forged,state_db=database,intake={'status':'PAUSED'})['published'])
            self.assertEqual(before,(receipt.read_bytes(),(root/'intake-receipt.json').read_bytes()))
            self.assertEqual(first,json.loads(immutable_receipt_path(receipt,old).read_text(encoding='utf-8')))

    def split_state(self,root,status='PAUSED'):
        receipt=root/'receipt.json';state_db=root/'state.sqlite3';control_db=root/'control.sqlite3'
        runtime=DeliveryRuntime(control_db)
        contract={'task_id':'t','goal':'g','owner':'host','candidate':'v','authorization_ref':'test',
                  'scope':['out'],'excluded':['external'],'policy':{'workspace_write':False},
                  'acceptance':[{'id':'A','expected':'verified output','environment':'local'}]}
        runtime.create_task(contract,{'evidence.hash':True,'checkpoint.persist':True})
        lease=runtime.claim('t','agent-runtime')['lease_id']
        runtime.apply('t',lease,'start',{'executor':'agent-runtime'})
        runtime.apply('t',lease,'checkpoint',{'id':'paused','resume_token':'token'})
        runtime.apply('t',lease,'pause',{'executor_stopped':True})
        identity=uuid.uuid4().hex
        RunJournal(state_db).create('t',{'fixture':True},{'phase':'PAUSED','last_status':status,
            'invocation_id':identity,'next_step':2,'pending':None,'events':[]})
        report={**self.report(receipt,identity,0,status),'control_state':runtime.get('t')}
        write_immutable_report(receipt,report)
        return receipt,state_db,control_db,lease,report

    def test_split_control_and_journal_publish_normal_and_model_error_results(self):
        for status in ('PAUSED','MODEL_ERROR'):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as temp:
                receipt,state_db,control_db,lease,report=self.split_state(Path(temp),status)
                result=publish_report(receipt,report,state_db=state_db,control_db=control_db,lease_id=lease,
                                      intake={'status':status})
                self.assertTrue(result['published'])
                self.assertEqual(status,json.loads(receipt.read_text(encoding='utf-8'))['status'])
                with closing(sqlite3.connect(state_db)) as db:
                    self.assertEqual(0,db.execute('SELECT generation FROM agent_result_publications').fetchone()[0])
                with closing(sqlite3.connect(control_db)) as db:
                    self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_result_publications'").fetchone())

    def test_split_publication_locks_both_databases_and_rejects_wrong_control_or_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);receipt,state_db,control_db,lease,report=self.split_state(root)
            empty=root/'unrelated.sqlite3'
            with closing(sqlite3.connect(empty)):pass
            self.assertFalse(publish_report(receipt,report,state_db=state_db,control_db=empty,lease_id=lease)['published'])
            self.assertFalse(publish_report(receipt,report,state_db=state_db,control_db=control_db,lease_id='other-lease')['published'])
            self.assertFalse(receipt.exists())
            seen=[];original=publication._atomic
            def locked_publish(path,data):
                if not seen:
                    for database,sql in ((state_db,'UPDATE agent_checkpoints SET generation=generation+1'),
                                         (control_db,'UPDATE leases SET expires_at=expires_at+1')):
                        with closing(sqlite3.connect(database,timeout=0.01)) as other:
                            with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                                other.execute(sql)
                        seen.append(str(database))
                original(path,data)
            with patch.object(publication,'_atomic',side_effect=locked_publish):
                self.assertTrue(publish_report(receipt,report,state_db=state_db,control_db=control_db,lease_id=lease)['published'])
            self.assertEqual(2,len(seen))

    def test_delayed_old_public_process_does_not_overwrite_newer_complete_result(self):
        for operation in ('start','resume'):
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);args,env=fixture.ManagedTaskCliTests().prepare(root)
                if operation=='resume':
                    first=subprocess.run([sys.executable,'-X','utf8',str(CLI),*args,'--steps-per-run','1'],
                                         env=env,capture_output=True,encoding='utf-8',timeout=20)
                    self.assertEqual('PAUSED',json.loads(first.stdout)['status'],first.stderr)
                inject=root/'delayed-publisher';inject.mkdir();ready=root/'old-released';release=root/'release-old'
                code='''from pathlib import Path
import sys,time,os
sys.path.insert(0,__KIT__)
from control_plane.runtime import DeliveryRuntime
original=DeliveryRuntime.release
def delayed(self,*args,**kwargs):
 result=original(self,*args,**kwargs)
 if os.environ.get('DELAY_RELEASE')=='1':
  Path(__READY__).write_text('old lease released',encoding='utf-8')
  until=time.monotonic()+20
  while not Path(__CONTINUE__).exists() and time.monotonic()<until:time.sleep(0.02)
 return result
DeliveryRuntime.release=delayed
'''.replace('__KIT__',repr(str(KIT))).replace('__READY__',repr(str(ready))).replace('__CONTINUE__',repr(str(release)))
                (inject/'sitecustomize.py').write_text(code,encoding='utf-8')
                oldargs=([*args,'--steps-per-run','1'] if operation=='start'
                         else ['resume','--control-root',str(root/'control'),'--steps-per-run','1'])
                oldenv={**env,'PYTHONPATH':str(inject),'DELAY_RELEASE':'1'}
                older=subprocess.Popen([sys.executable,'-X','utf8',str(CLI),*oldargs],env=oldenv,
                                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf-8')
                try:
                    limit=time.monotonic()+12
                    while not ready.exists():
                        if older.poll() is not None:self.fail('old worker exited before release boundary: '+older.communicate()[1])
                        if time.monotonic()>limit:self.fail('old publisher did not reach actual release boundary')
                        time.sleep(0.02)
                    newer=subprocess.run([sys.executable,'-X','utf8',str(CLI),'resume','--control-root',str(root/'control')],
                                         env=env,capture_output=True,encoding='utf-8',timeout=20)
                    self.assertEqual('COMPLETED',json.loads(newer.stdout)['status'],newer.stderr)
                    release.write_text('continue old publisher',encoding='utf-8')
                    oldout,olderr=older.communicate(timeout=12)
                    self.assertEqual('PAUSED',json.loads(oldout)['status'],olderr)
                    self.assertEqual('COMPLETED',json.loads((root/'control/receipt.json').read_text(encoding='utf-8'))['status'])
                    self.assertEqual('COMPLETED',json.loads((root/'control/intake-receipt.json').read_text(encoding='utf-8'))['status'])
                    with closing(sqlite3.connect(root/'control/control.sqlite3')) as db:
                        self.assertEqual('COMPLETED',db.execute('SELECT status FROM tasks').fetchone()[0])
                    self.assertEqual(2 if operation=='start' else 3,len(list((root/'control/runs').glob('*/receipt.json'))))
                finally:
                    if older.poll() is None:older.kill();older.communicate()

    def test_raw_multiline_chinese_goal_is_preserved_while_contract_is_normalized(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args,env=fixture.ManagedTaskCliTests().prepare(root)
            goal='  请读取原始数字并求和。\n将结果写入 report.json，不能修改输入。\n'
            args[args.index('--goal')+1]=goal
            process=subprocess.run([sys.executable,'-X','utf8',str(CLI),*args],env=env,
                                   capture_output=True,encoding='utf-8',timeout=25)
            self.assertEqual('COMPLETED',json.loads(process.stdout)['status'],process.stderr)
            request=json.loads((root/'control/request.json').read_text(encoding='utf-8'))
            contract=json.loads((root/'control/contract.json').read_text(encoding='utf-8'))
            self.assertEqual(goal,request['goal']);self.assertEqual(goal.strip(),contract['goal'])

    def test_seal_failure_is_prelaunch_rejection_without_a_model_or_unknown_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args,env=fixture.ManagedTaskCliTests().prepare(root)
            inject=root/'seal-failure';inject.mkdir()
            (inject/'sitecustomize.py').write_text('import sys\nsys.path.insert(0,'+repr(str(KIT))+')\n'
                'import agent_runtime.task_lifecycle as lifecycle\n'
                'def fail(*args,**kwargs):raise ValueError("simulated sealing failure")\n'
                'lifecycle.seal_task=fail\n',encoding='utf-8')
            process=subprocess.run([sys.executable,'-X','utf8',str(CLI),*args],env={**env,'PYTHONPATH':str(inject)},
                                   capture_output=True,encoding='utf-8',timeout=15)
            report=json.loads(process.stdout)
            self.assertEqual('PLAN_REJECTED',report['status'],process.stderr)
            self.assertFalse((root/'model-calls.txt').exists())
            self.assertFalse((root/'control/control.sqlite3').exists())
            self.assertEqual('PLAN_REJECTED',json.loads((root/'control/intake-receipt.json').read_text(encoding='utf-8'))['status'])


if __name__=='__main__':unittest.main()
