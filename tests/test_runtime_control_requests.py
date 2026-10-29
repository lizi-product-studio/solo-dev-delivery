"""Cooperative stop requests use real SQLite connections and live executors.

Models/tools here are labelled local doubles. Their threads actually overlap
with host requests, and the tool test really appends before it returns.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

KIT=Path(__file__).resolve().parents[1]/'portable-agent-kit'
sys.path.insert(0,str(KIT))
from agent_runtime import AgentRuntime,MemoryStore,ToolRegistry
from agent_runtime.journal import RunJournal,JournalError
from control_plane.runtime import DeliveryRuntime


class RuntimeControlRequestTests(unittest.TestCase):
    def control(self,root,*,max_steps=20,allow_write=False):
        control=DeliveryRuntime(root/'control.sqlite3')
        control.create_task({'task_id':'t','goal':'g','owner':'runtime','candidate':'test',
            'authorization_ref':'local-test','scope':['counter.txt'],'excluded':['external'],
            'policy':{'workspace_write':allow_write,'shell_required':False},'budget':{'max_steps':max_steps,'max_retries':1},
            'acceptance':[{'id':'A','expected':'readback','environment':'local'}]},
            {'evidence.hash':True,'checkpoint.persist':True,'fs.read':True,'fs.write':True})
        lease=control.claim('t','runtime')['lease_id']
        return control,lease

    def threaded(self,fn):
        outcome={}
        def work():
            try:outcome['result']=fn()
            except BaseException as exc:outcome['error']=exc
        thread=threading.Thread(target=work,daemon=True);thread.start()
        return thread,outcome

    def joined(self,thread,outcome):
        thread.join(5)
        self.assertFalse(thread.is_alive(),'executor did not acknowledge the safe boundary')
        if 'error' in outcome:raise outcome['error']
        return outcome['result']

    def test_second_connection_stops_after_model_without_executing_its_decision(self):
        for operation in ('pause','cancel'):
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);control,lease=self.control(root)
                journal=RunJournal(root/'state.sqlite3');entered=threading.Event();release=threading.Event()
                writes=[];tools=ToolRegistry();tools.register('write',lambda:writes.append('effect'))
                model_calls=[]
                def slow_model(request):
                    model_calls.append(request['step']);entered.set()
                    if not release.wait(5):raise RuntimeError('test release missing')
                    return {'action':'tool','name':'write','arguments':{}}
                runtime=AgentRuntime(slow_model,tools,MemoryStore(root/'memory.sqlite3'),max_steps=3)
                thread,outcome=self.threaded(lambda:runtime.run('g',task_id='t',control=control,
                    lease_id=lease,journal=journal))
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual('MODEL_PENDING',journal.load('t')['snapshot']['phase'])
                    requested=RunJournal(root/'state.sqlite3').request_control('t',operation,'host stop',request_id='stop-1')
                    self.assertEqual('REQUESTED',requested['status'])
                finally:release.set()
                result=self.joined(thread,outcome)
                expected='PAUSED' if operation=='pause' else 'CANCELLED'
                self.assertEqual(expected,result.status)
                self.assertEqual(expected,control.get('t')['status'])
                self.assertEqual([],writes);self.assertEqual([1],model_calls)
                self.assertEqual(2,journal.load('t')['snapshot']['next_step'])
                self.assertEqual('ACKNOWLEDGED',journal.control_requests('t')[0]['status'])
                self.assertEqual('model_discarded',result.events[-1]['action'])
                if operation=='cancel':
                    restarted=AgentRuntime(lambda _:self.fail('cancelled run called model'),tools,
                        MemoryStore(root/'memory.sqlite3'),max_steps=3)
                    again=restarted.run('g',task_id='t',journal=journal,control=control,lease_id=lease,resume=True)
                    self.assertEqual('CANCELLED',again.status)
                else:
                    calls=[]
                    resumed=AgentRuntime(lambda req:calls.append(req['step']) or {'action':'finish','answer':'ok'},
                        tools,MemoryStore(root/'memory.sqlite3'),max_steps=3)
                    # The previous pause request is acknowledged, not sticky.
                    after=resumed.run('g',task_id='t',journal=journal,control=control,lease_id=lease,resume=True)
                    self.assertNotEqual('PAUSED',after.status)
                    self.assertEqual([2,3],calls)
                    self.assertEqual([],writes)

    def test_request_during_real_tool_waits_for_result_and_never_starts_next_action(self):
        for operation in ('pause','cancel'):
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);journal=RunJournal(root/'state.sqlite3')
                entered=threading.Event();release=threading.Event();calls=[]
                tools=ToolRegistry()
                def append():
                    with (root/'counter.txt').open('a') as stream:stream.write('x')
                    entered.set()
                    if not release.wait(5):raise RuntimeError('test release missing')
                    return {'written':True}
                tools.register('append',append)
                def model(req):
                    calls.append(req['step']);return {'action':'tool','name':'append','arguments':{}}
                runtime=AgentRuntime(model,tools,MemoryStore(root/'memory.sqlite3'),max_steps=3)
                thread,outcome=self.threaded(lambda:runtime.run('g',task_id='t',journal=journal))
                try:
                    self.assertTrue(entered.wait(5))
                    journal.request_control('t',operation,'stop after current tool')
                    pending=journal.load('t')['snapshot']
                    self.assertEqual('ACTION_PENDING',pending['phase'])
                    again=AgentRuntime(lambda _:self.fail('unknown checkpoint re-executed model'),tools,
                        MemoryStore(root/'memory.sqlite3'),max_steps=3).run('g',task_id='t',journal=journal,resume=True)
                    self.assertEqual('NEEDS_RECONCILIATION',again.status)
                    self.assertEqual('REQUESTED',journal.control_requests('t')[0]['status'])
                finally:release.set()
                result=self.joined(thread,outcome)
                self.assertEqual('PAUSED' if operation=='pause' else 'CANCELLED',result.status)
                self.assertEqual([1],calls);self.assertEqual('x',(root/'counter.txt').read_text())
                self.assertIsNone(journal.load('t')['snapshot']['pending'])
                self.assertTrue(result.events[-1]['result']['written'])

    def test_unknown_side_effect_wins_over_a_cancel_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);control,lease=self.control(root);journal=RunJournal(root/'state.sqlite3')
            tools=ToolRegistry()
            def append():
                with (root/'counter.txt').open('a') as stream:stream.write('x')
                RunJournal(root/'state.sqlite3').request_control('t','cancel','stop requested during effect')
                raise TimeoutError('return lost after actual append')
            tools.register('append',append)
            runtime=AgentRuntime(lambda _:{'action':'tool','name':'append','arguments':{}},tools,
                                 MemoryStore(root/'memory.sqlite3'))
            result=runtime.run('g',task_id='t',journal=journal,control=control,lease_id=lease)
            self.assertEqual('NEEDS_RECONCILIATION',result.status)
            self.assertEqual('UNKNOWN',control.get('t')['status'])
            again=runtime.run('g',task_id='t',journal=journal,control=control,lease_id=lease,resume=True)
            self.assertEqual('NEEDS_RECONCILIATION',again.status)
            self.assertEqual('REQUESTED',journal.control_requests('t')[0]['status'])
            self.assertEqual('ACTION_PENDING',journal.load('t')['snapshot']['phase'])
            self.assertEqual('x',(root/'counter.txt').read_text())

    def test_paused_or_exhausted_runs_can_ack_cancel_without_spending_more_budget(self):
        for mode in ('step-budget','review-budget','paused'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);control,lease=self.control(root,max_steps=2 if mode=='step-budget' else 20)
                journal=RunJournal(root/'state.sqlite3');tools=ToolRegistry();calls=[]
                tools.register('read',lambda:{'value':1})
                steps=1 if mode=='step-budget' else 4
                def model(req):
                    calls.append(req['step'])
                    return {'action':'finish','answer':'wrong'} if mode=='review-budget' else {'action':'tool','name':'read','arguments':{}}
                runtime=AgentRuntime(model,tools,MemoryStore(root/'memory.sqlite3'),max_steps=steps)
                first=runtime.run('g',task_id='t',journal=journal,control=control,lease_id=lease,
                                  verifier=lambda *_:False,steps_per_run=1 if mode=='paused' else None)
                self.assertIn(first.status,{'BUDGET_EXHAUSTED','CONTROL_BLOCKED','PAUSED'})
                before=list(calls);journal.request_control('t','cancel','cancel exhausted work')
                again=runtime.run('g',task_id='t',journal=journal,control=control,lease_id=lease,
                                  verifier=lambda *_:False,resume=True)
                self.assertEqual('CANCELLED',again.status);self.assertEqual(before,calls)
                self.assertEqual('CANCELLED',control.get('t')['status'])

    def test_known_model_error_releases_executor_then_resumes_with_remaining_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);control,lease=self.control(root);journal=RunJournal(root/'state.sqlite3')
            tools=ToolRegistry();tools.register('read',lambda:{'value':1})
            def broken_model(_):raise RuntimeError('labelled local model transport double failed')
            first=AgentRuntime(broken_model,tools,MemoryStore(root/'memory.sqlite3'),max_steps=2).run('g',
                task_id='t',journal=journal,control=control,lease_id=lease)
            self.assertEqual('MODEL_ERROR',first.status)
            self.assertEqual('PAUSED',control.get('t')['status'])
            snapshot=journal.load('t')['snapshot']
            self.assertEqual('PAUSED',snapshot['phase']);self.assertEqual('MODEL_ERROR',snapshot['last_status'])
            control.release('t',lease)
            next_lease=control.claim('t','runtime')['lease_id'];calls=[]
            resumed=AgentRuntime(lambda req:calls.append(req['step']) or {'action':'tool','name':'read','arguments':{}},
                tools,MemoryStore(root/'memory.sqlite3'),max_steps=2).run('g',task_id='t',journal=journal,
                    control=control,lease_id=next_lease,resume=True)
            self.assertEqual([2],calls);self.assertEqual('BUDGET_EXHAUSTED',resumed.status)
            self.assertEqual('PAUSED',control.get('t')['status'])
            before=len(control.events('t'))
            again=AgentRuntime(lambda _:self.fail('exhausted resume called model'),tools,
                MemoryStore(root/'memory.sqlite3'),max_steps=2).run('g',task_id='t',journal=journal,
                    control=control,lease_id=next_lease,resume=True)
            self.assertEqual('BUDGET_EXHAUSTED',again.status)
            self.assertEqual(before,len(control.events('t')),'exhausted resume must not spend a CP resume event')
            control.release('t',next_lease)

    def test_lease_loss_is_not_rewritten_as_a_proven_pause(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);control,lease=self.control(root);journal=RunJournal(root/'state.sqlite3')
            before=[];tools=ToolRegistry();tools.register('read',lambda:before.append('tool'))
            with patch.object(control,'heartbeat',side_effect=ValueError('lost lease')):
                result=AgentRuntime(lambda _:{'action':'tool','name':'read','arguments':{}},tools,
                    MemoryStore(root/'memory.sqlite3')).run('g',task_id='t',journal=journal,control=control,lease_id=lease)
            self.assertEqual('LEASE_LOST',result.status);self.assertEqual([],before)
            self.assertEqual('RUNNING',control.get('t')['status'])
            self.assertNotEqual('PAUSED',journal.load('t')['snapshot']['phase'])

    def test_control_budget_is_checked_before_an_actual_file_write(self):
        for budget,expected in ((1,''),(2,'x')):
            with self.subTest(budget=budget),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);control,lease=self.control(root,max_steps=budget,allow_write=True)
                journal=RunJournal(root/'journal.sqlite3');target=root/'counter.txt';tools=ToolRegistry()
                def append():
                    with target.open('a',encoding='utf-8') as stream:stream.write('x')
                    return {'written':True}
                tools.register('append',append)
                model=lambda _:{'action':'tool','name':'append','arguments':{}}
                runtime=AgentRuntime(model,tools,MemoryStore(root/'memory.sqlite3'),max_steps=4)
                first=runtime.run('g',task_id='t',journal=journal,control=control,lease_id=lease)
                self.assertEqual('BUDGET_EXHAUSTED',first.status)
                self.assertTrue(first.events[-1]['result']['pre_effect_rejection'])
                self.assertEqual(expected,target.read_text() if target.exists() else '')
                self.assertEqual(budget,control.get('t')['control_plane']['steps_used'])
                self.assertEqual([],control.get('t')['unknown_tool_calls'])
                self.assertIsNone(journal.load('t')['snapshot']['pending'])
                # A fresh execution cannot reset the CP budget or append once
                # more merely because some model-decision budget remains.
                control.release('t',lease);next_lease=control.claim('t','runtime')['lease_id']
                again=runtime.run('g',task_id='t',journal=RunJournal(root/'journal.sqlite3'),
                                  control=control,lease_id=next_lease,resume=True)
                self.assertEqual('BUDGET_EXHAUSTED',again.status)
                self.assertEqual(expected,target.read_text() if target.exists() else '')
                self.assertEqual(budget,control.get('t')['control_plane']['steps_used'])

    def test_chunked_resume_finishes_on_last_permitted_model_and_tool_step(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);control=DeliveryRuntime(root/'control.sqlite3',evidence_root=root)
            control.create_task({'task_id':'chunked','goal':'sum original inputs','owner':'runtime','candidate':'test',
                'authorization_ref':'local-file-test','scope':['output.json'],'inputs':['a.txt','b.txt'],
                'excluded':['external'],'policy':{'workspace_write':True,'shell_required':False},
                'budget':{'max_steps':4,'max_retries':1},
                'acceptance':[{'id':'sum','expected':'sum=42','environment':'local',
                    'assertion':{'type':'json_pointer_equals','path':'output.json','pointer':'/sum','value':42}}]},
                {'evidence.hash':True,'checkpoint.persist':True,'fs.read':True,'fs.write':True})
            (root/'a.txt').write_text('20');(root/'b.txt').write_text('22')
            tools=ToolRegistry();calls=[];writes=[]
            tools.register('read',lambda path:(root/path).read_text())
            def write(value):
                writes.append(value);(root/'output.json').write_text(json.dumps({'sum':value}),encoding='utf-8')
                return {'written':True}
            tools.register('write',write)
            def model(request):
                calls.append(request['step'])
                if request['step']<3:return {'action':'tool','name':'read','arguments':{'path':'a.txt' if request['step']==1 else 'b.txt'}}
                total=sum(int(row['result']) for row in request['observations'])
                return {'action':'tool','name':'write','arguments':{'value':total}}
            lease=control.claim('chunked','runtime')['lease_id']
            def verifier(*_):
                target=root/'output.json'
                if json.loads(target.read_text())!={'sum':42}:return False
                state=control.get('chunked')
                control.apply('chunked',lease,'evidence',{
                    'id':'sum-proof','kind':'local-readback','uri':'output.json',
                    'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'claim':'sum=42','verdict':'PASS',
                    'check_id':'sum','environment':'local',
                    **{k:state[k] for k in ('task_id','revision','contract_hash','candidate')}},
                    evidence_root=root,reviewer_id='trusted-file-test')
                return True
            verifier.ready=lambda _: (root/'output.json').is_file()
            runtime=AgentRuntime(model,tools,MemoryStore(root/'memory.sqlite3'),max_steps=3)
            for chunk in range(3):
                result=runtime.run('sum original inputs',task_id='chunked',journal=RunJournal(root/'journal.sqlite3'),
                    control=control,lease_id=lease,resume=chunk>0,steps_per_run=1,
                    verifier=verifier,auto_verify=True,evidence_root=root,reviewer_id='trusted-file-test')
                if chunk<2:
                    self.assertEqual('PAUSED',result.status)
                    control.release('chunked',lease);lease=control.claim('chunked','runtime')['lease_id']
            self.assertEqual('COMPLETED',result.status,(result.answer,control.get('chunked')['blockers'],result.events[-1]));self.assertEqual([1,2,3],calls);self.assertEqual([42],writes)
            self.assertEqual({'sum':42},json.loads((root/'output.json').read_text()))
            self.assertEqual(4,control.get('chunked')['control_plane']['steps_used'])
            self.assertEqual(3,result.steps)
            with self.assertRaisesRegex(ValueError,'terminal'):
                runtime.run('sum original inputs',task_id='chunked',journal=RunJournal(root/'journal.sqlite3'),
                    control=control,lease_id=lease,resume=True,verifier=verifier,auto_verify=True,
                    evidence_root=root,reviewer_id='trusted-file-test')
            self.assertEqual([42],writes)

    def test_request_identity_priority_and_ack_cas(self):
        with tempfile.TemporaryDirectory() as temp:
            journal=RunJournal(Path(temp)/'journal.sqlite3')
            saved=journal.create('t',{}, {'phase':'READY','pending':None,'next_step':1,'events':[]})
            first=journal.request_control('t','pause','pause now',request_id='p')
            self.assertEqual(first,journal.request_control('t','pause','pause now',request_id='p'))
            with self.assertRaisesRegex(JournalError,'CONFLICT'):
                journal.request_control('t','cancel','pause now',request_id='p')
            journal.request_control('t','cancel','cancel now',request_id='c')
            self.assertEqual('c',journal.pending_control('t')['request_id'])
            stopped={'phase':'CANCELLED','last_status':'CANCELLED','pending':None,'next_step':1,'events':[]}
            with self.assertRaisesRegex(JournalError,'CONCURRENT'):
                journal.acknowledge_control('t','c',saved['generation']+1,stopped)
            self.assertEqual('REQUESTED',journal.pending_control('t')['status'])
            finished=journal.acknowledge_control('t','c',saved['generation'],stopped)
            self.assertIsNone(journal.pending_control('t'))
            self.assertEqual(['SUPERSEDED','ACKNOWLEDGED'],[r['status'] for r in journal.control_requests('t')])
            self.assertEqual(finished,journal.acknowledge_control('t','c',0,stopped))
            with self.assertRaisesRegex(JournalError,'terminal'):
                journal.request_control('t','pause','too late')
            with self.assertRaisesRegex(JournalError,'does not exist'):
                journal.request_control('other','cancel','wrong task')
            for operation,reason,request_id in [('resume','reason','x'),('pause','','x'),('pause','reason','')]:
                with self.subTest(operation=operation,reason=reason,request_id=request_id),self.assertRaises(JournalError):
                    journal.request_control('t',operation,reason,request_id=request_id)

    def test_finalization_cutoff_cannot_accept_and_ignore_a_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            journal=RunJournal(Path(temp)/'journal.sqlite3')
            snapshot={'phase':'FINISH_PENDING','next_step':2,'pending':None,'events':[]}
            pending=journal.create('pending',{},deepcopy(snapshot))
            journal.request_control('pending','cancel','cancel before commit')
            with self.assertRaisesRegex(JournalError,'CONTROL_REQUEST_PENDING'):
                journal.prepare_finish('pending',pending['generation'],snapshot)
            self.assertFalse(journal.load('pending')['snapshot'].get('controls_closed',False))
            current=journal.create('committing',{},deepcopy(snapshot))
            sealed=journal.prepare_finish('committing',current['generation'],snapshot)
            self.assertTrue(sealed['snapshot']['controls_closed'])
            with self.assertRaisesRegex(JournalError,'FINALIZING'):
                journal.request_control('committing','cancel','too late to accept')
            self.assertEqual([],journal.control_requests('committing'))

    def test_sqlite_finalization_request_race_has_one_winner(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);journal=RunJournal(root/'journal.sqlite3')
            for index in range(8):
                task='race-'+str(index)
                snapshot={'phase':'FINISH_PENDING','next_step':1,'pending':None,'events':[]}
                saved=journal.create(task,{},snapshot);barrier=threading.Barrier(3);outcomes={}
                requester=RunJournal(root/'journal.sqlite3');finisher=RunJournal(root/'journal.sqlite3')
                def request():
                    barrier.wait()
                    try:outcomes['request']=requester.request_control(task,'cancel','race stop')['status']
                    except JournalError as exc:outcomes['request']=str(exc)
                def seal():
                    barrier.wait()
                    try:
                        finisher.prepare_finish(task,saved['generation'],snapshot);outcomes['finish']='SEALED'
                    except JournalError as exc:outcomes['finish']=str(exc)
                threads=[threading.Thread(target=request),threading.Thread(target=seal)]
                for thread in threads:thread.start()
                barrier.wait()
                for thread in threads:thread.join(5);self.assertFalse(thread.is_alive())
                with self.subTest(index=index,outcomes=outcomes):
                    if outcomes['request']=='REQUESTED':
                        self.assertEqual('CONTROL_REQUEST_PENDING',outcomes['finish'])
                        self.assertFalse(journal.load(task)['snapshot'].get('controls_closed',False))
                    else:
                        self.assertIn('FINALIZING',outcomes['request']);self.assertEqual('SEALED',outcomes['finish'])
                        self.assertIsNone(journal.pending_control(task))

    def test_pause_commit_before_journal_ack_can_be_recovered_without_resuming_executor(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);control,lease=self.control(root);journal=RunJournal(root/'state.sqlite3')
            calls=[];tools=ToolRegistry();tools.register('read',lambda:{'value':1})
            def model(req):
                calls.append(req['step'])
                journal.request_control('t','pause','pause at model return',request_id='pause-once')
                return {'action':'tool','name':'read','arguments':{}}
            runtime=AgentRuntime(model,tools,MemoryStore(root/'memory.sqlite3'),max_steps=3)
            with patch.object(journal,'acknowledge_control',side_effect=RuntimeError('simulated crash after CP pause')):
                with self.assertRaisesRegex(RuntimeError,'simulated crash'):
                    runtime.run('g',task_id='t',journal=journal,control=control,lease_id=lease)
            self.assertEqual('PAUSED',control.get('t')['status'])
            self.assertEqual('READY',journal.load('t')['snapshot']['phase'])
            before=len(control.events('t'))
            recovered=AgentRuntime(lambda _:self.fail('recovery called model before acknowledging pause'),tools,
                MemoryStore(root/'memory.sqlite3'),max_steps=3).run('g',task_id='t',journal=RunJournal(root/'state.sqlite3'),
                    control=control,lease_id=lease,resume=True)
            self.assertEqual('PAUSED',recovered.status);self.assertEqual([1],calls)
            self.assertEqual(before,len(control.events('t')),'already committed pause must not issue resume or another pause')
            self.assertEqual('ACKNOWLEDGED',journal.control_requests('t')[0]['status'])

    def test_request_during_verifier_prevents_success_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);journal=RunJournal(root/'state.sqlite3');memory=MemoryStore(root/'memory.sqlite3')
            entered=threading.Event();release=threading.Event()
            def verifier(*_):
                entered.set()
                if not release.wait(5):raise RuntimeError('test release missing')
                return True
            runtime=AgentRuntime(lambda _:{'action':'finish','answer':'done'},ToolRegistry(),memory)
            thread,outcome=self.threaded(lambda:runtime.run('g',task_id='t',journal=journal,verifier=verifier))
            try:
                self.assertTrue(entered.wait(5))
                journal.request_control('t','cancel','cancel before final acceptance')
            finally:release.set()
            self.assertEqual('CANCELLED',self.joined(thread,outcome).status)
            self.assertEqual([],memory.search('g'))
            self.assertEqual('ACKNOWLEDGED',journal.control_requests('t')[0]['status'])

    def test_control_plane_cancellation_supersedes_an_earlier_pause_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);control,lease=self.control(root);journal=RunJournal(root/'state.sqlite3')
            effects=[];tools=ToolRegistry();tools.register('write',lambda:effects.append('effect'))
            def model(_):
                journal.request_control('t','pause','earlier pause')
                control.cancel('t',lease,executor_stopped=True,reason='later trusted host cancellation')
                return {'action':'tool','name':'write','arguments':{}}
            result=AgentRuntime(model,tools,MemoryStore(root/'memory.sqlite3')).run('g',
                task_id='t',journal=journal,control=control,lease_id=lease)
            self.assertEqual('CANCELLED',result.status);self.assertEqual([],effects)
            self.assertEqual('SUPERSEDED',journal.control_requests('t')[0]['status'])
            self.assertIsNone(journal.pending_control('t'))


if __name__=='__main__':unittest.main()
