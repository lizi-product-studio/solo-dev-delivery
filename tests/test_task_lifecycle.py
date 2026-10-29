"""Managed host lifecycle with real SQLite/file readback, no real model."""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

KIT=Path(__file__).resolve().parents[1]/'portable-agent-kit'
sys.path.insert(0,str(KIT))
from agent_runtime.intake import build_context,validate_plan,compile_contract
from agent_runtime.journal import RunJournal
from agent_runtime.task_lifecycle import (LifecycleError,seal_task,load_task,task_status,
    resume_arguments,make_readback_evidence,reconcile)
from control_plane.runtime import DeliveryRuntime
import agent_runtime.task_lifecycle as lifecycle


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


class TaskLifecycleTests(unittest.TestCase):
    def prepare(self,root,*,unknown=False,tool='write_file'):
        work=root/'workspace';work.mkdir();(work/'TASK.md').write_text('Write exact output',encoding='utf-8')
        control=root/'task';control.mkdir()
        context=build_context('Write exact output',work,['out.txt'],'host:test',max_steps=6)
        plan=validate_plan({'inputs':['TASK.md'],'outputs':['out.txt'],'steps':['write exact output'],'unknowns':[]},context)
        save(control/'request.json',context);save(control/'plan.json',plan)
        (control/'trusted-review.py').write_text('import unittest\nclass Actual(unittest.TestCase):\n def test_a(self):pass\n',encoding='utf-8')
        contract=compile_contract(context,plan,task_id='t',
            review_sha256=hashlib.sha256((control/'trusted-review.py').read_bytes()).hexdigest(),planner_steps=0)
        save(control/'contract.json',contract)
        argv=['--goal',context['goal'],'--workspace',str(work),'--memory',str(root/'memory.sqlite3'),
              '--skills',str(root/'skills.sqlite3'),'--project-id','workspace-test',
              '--db',str(control/'control.sqlite3'),'--contract',str(control/'contract.json'),
              '--receipt',str(control/'receipt.json'),'--task-id','t',
              '--review-script',str(control/'trusted-review.py'),'--auto-verify','--max-steps','6']
        save(control/'runtime-arguments.json',argv)
        save(control/'intake-receipt.json',{'task_id':'t','status':'RUNNING',
            'request_sha256':hashlib.sha256((control/'request.json').read_bytes()).hexdigest(),
            'plan_sha256':hashlib.sha256((control/'plan.json').read_bytes()).hexdigest()})
        task=seal_task(control,argv)
        if not unknown:return control,work,task
        cp=DeliveryRuntime(control/'control.sqlite3',evidence_root=work)
        state=cp.create_task(contract,{'fs.read':True,'fs.write':True,'shell.exec':False,'evidence.hash':True,'checkpoint.persist':True})
        lease=cp.claim('t','agent-runtime',ttl_seconds=3600)['lease_id']
        cp.apply('t',lease,'start',{'executor':'agent-runtime'})
        arguments=({'path':'out.txt','text':'exact 中文\n'} if tool=='write_file'
                   else {'path':'out.txt','value':{'number':42,'body':'中文\n"quoted"'}})
        data=(arguments['text'] if tool=='write_file' else json.dumps(arguments['value'],ensure_ascii=False,indent=2)+'\n')
        (work/'out.txt').write_bytes(data.encode('utf-8'))
        decision={'action':'tool','name':tool,'arguments':arguments}
        cp.apply('t',lease,'tool_result',{'tool_call_id':'t-step-1','status':'UNKNOWN'})
        RunJournal(control/'control.sqlite3').create('t',{'fixture':'binding'},
            {'phase':'ACTION_PENDING','next_step':1,'last_status':'NEEDS_RECONCILIATION',
             'events':[{'step':1,'action':'tool','decision':decision,'result':{'status':'UNKNOWN'}}],
             'pending':{'id':'t-step-1','decision':decision}})
        return control,work,task

    def test_seal_hashes_static_host_state_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);control,_,task=self.prepare(root)
            self.assertEqual(task['runtime_argv'],load_task(control)['runtime_argv'])
            self.assertEqual(task['manifest'],seal_task(control)['manifest'])
            # Runtime result updates do not mutate the immutable task envelope.
            save(control/'intake-receipt.json',{'task_id':'t','status':'PAUSED'})
            self.assertEqual('t',load_task(control)['task_id'])
            (control/'trusted-review.py').write_text('changed',encoding='utf-8')
            with self.assertRaisesRegex(LifecycleError,'STALE_MANAGED_TASK'):load_task(control)

    def test_status_is_read_only_and_does_not_create_missing_state(self):
        with tempfile.TemporaryDirectory() as temp:
            control,_,task=self.prepare(Path(temp))
            before={p.name:p.read_bytes() for p in control.iterdir()}
            self.assertEqual('NOT_STARTED',task_status(control)['status'])
            self.assertFalse(Path(task['state_db']).exists())
            self.assertEqual(before,{p.name:p.read_bytes() for p in control.iterdir()})
            with self.assertRaises(LifecycleError):resume_arguments(control)

    def test_pending_unknown_cannot_resume_and_wrong_readback_cannot_clear_it(self):
        with tempfile.TemporaryDirectory() as temp:
            control,work,_=self.prepare(Path(temp),unknown=True)
            status=task_status(control)
            self.assertEqual('ACTION_PENDING',status['phase']);self.assertFalse(status['can_resume'])
            with self.assertRaises(LifecycleError):resume_arguments(control)
            ref=make_readback_evidence(control,'t-step-1','independent-host')
            for changes in ({'tool_call_id':'other'},{'sha256':'0'*64},{'uri':'TASK.md'},
                            {'candidate':'other'},{'observed_outcome':'FAILED'}):
                with self.subTest(changes=changes),self.assertRaises((LifecycleError,ValueError)):
                    reconcile(control,'t-step-1',{**ref,**changes},'independent-host')
            (work/'out.txt').write_text('different',encoding='utf-8')
            with self.assertRaisesRegex(LifecycleError,'exact pending write'):reconcile(control,'t-step-1',ref,'independent-host')
            self.assertEqual(['t-step-1'],task_status(control)['unknown_tool_calls'])

    def test_exact_write_readback_atomically_pauses_both_records_and_can_resume(self):
        for tool in ('write_file','write_json'):
            with self.subTest(tool=tool),tempfile.TemporaryDirectory() as temp:
                control,work,task=self.prepare(Path(temp),unknown=True,tool=tool)
                ref=make_readback_evidence(control,'t-step-1','independent-host')
                result=reconcile(control,'t-step-1',ref,'independent-host')
                self.assertFalse(result['reused'])
                observed=task_status(control)
                self.assertEqual('PAUSED',observed['status']);self.assertEqual('PAUSED',observed['phase'])
                self.assertTrue(observed['can_resume']);self.assertIsNone(observed['lease'])
                self.assertEqual([],observed['unknown_tool_calls']);self.assertIsNone(observed['pending'])
                argv=resume_arguments(control,steps_per_run=1)
                self.assertIn('--resume',argv);self.assertEqual('6',argv[argv.index('--max-steps')+1])
                self.assertEqual('1',argv[argv.index('--steps-per-run')+1])
                with self.assertRaises(LifecycleError):resume_arguments(control,for_cancel=True)
                self.assertTrue(reconcile(control,'t-step-1',ref,'independent-host')['reused'])
                with self.assertRaisesRegex(LifecycleError,'CONFLICT'):
                    reconcile(control,'t-step-1',{**ref,'id':'other'},'independent-host')

    def test_transaction_rolls_back_control_and_journal_after_intermediate_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            control,_,_=self.prepare(Path(temp),unknown=True)
            ref=make_readback_evidence(control,'t-step-1','independent-host')
            before=task_status(control);journal=RunJournal(control/'control.sqlite3').load('t')
            original=lifecycle.apply_event
            def interrupted(state,request,**kwargs):
                if request['op']=='pause':raise RuntimeError('simulated crash before commit')
                return original(state,request,**kwargs)
            with patch.object(lifecycle,'apply_event',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'simulated crash'):
                reconcile(control,'t-step-1',ref,'independent-host')
            self.assertEqual(before,task_status(control))
            self.assertEqual(journal,RunJournal(control/'control.sqlite3').load('t'))
            cp=DeliveryRuntime(control/'control.sqlite3')
            self.assertEqual(3,len(cp.events('t')))

    def test_automatic_evidence_and_repeated_action_are_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            control,_,_=self.prepare(Path(temp),unknown=True)
            first=reconcile(control,'t-step-1',reviewer_id='independent-host')
            self.assertFalse(first['reused'])
            journal=RunJournal(control/'control.sqlite3').load('t')
            state=task_status(control)
            self.assertTrue(reconcile(control,'t-step-1',None,'independent-host')['reused'])
            self.assertEqual(journal,RunJournal(control/'control.sqlite3').load('t'))
            self.assertEqual(state,task_status(control))
            with self.assertRaisesRegex(LifecycleError,'CONFLICT'):
                reconcile(control,'t-step-1',reviewer_id='different-reviewer')
            ref=journal['snapshot']['reconciliations'][-1]['evidence']
            with self.assertRaisesRegex(LifecycleError,'CONFLICT'):
                reconcile(control,'t-step-1',{**ref,'id':'altered'},'independent-host')

    def test_unacknowledged_crash_expired_lease_and_unsupported_host_actions_stay_unknown(self):
        for variant in ('in-flight','expired','skill','unmanaged','update_json'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as temp:
                control,_,_=self.prepare(Path(temp),unknown=True)
                journal=RunJournal(control/'control.sqlite3');saved=journal.load('t');snapshot=saved['snapshot']
                if variant=='in-flight':snapshot.pop('last_status')
                elif variant=='expired':
                    with closing(sqlite3.connect(control/'control.sqlite3')) as db:
                        db.execute('UPDATE leases SET expires_at=0');db.commit()
                else:
                    snapshot['pending']['decision']['action']='skill' if variant=='skill' else ('unmanaged_host' if variant=='unmanaged' else 'tool')
                    if variant=='update_json':snapshot['pending']['decision']['name']='update_json'
                if variant!='expired':journal.save('t',saved['generation'],snapshot)
                with self.assertRaises(LifecycleError):
                    if variant=='expired':
                        ref=make_readback_evidence(control,'t-step-1','independent-host')
                        reconcile(control,'t-step-1',ref,'independent-host')
                    else:make_readback_evidence(control,'t-step-1','independent-host')
                self.assertEqual(['t-step-1'],task_status(control)['unknown_tool_calls'])

    def test_owner_false_pass_and_failed_outcome_are_not_independent_readback(self):
        with tempfile.TemporaryDirectory() as temp:
            control,_,task=self.prepare(Path(temp),unknown=True)
            with self.assertRaises(ValueError):make_readback_evidence(control,'t-step-1',task['contract']['owner'])
            ref=make_readback_evidence(control,'t-step-1','independent-host')
            with self.assertRaises(LifecycleError):reconcile(control,'t-step-1',ref,'independent-host',outcome='FAILED')
            with self.assertRaises(ValueError):reconcile(control,'t-step-1',{**ref,'verdict':'PASS','host_verified':True,'sha256':'0'*64},'independent-host')
            self.assertFalse(task_status(control)['can_resume'])

    def test_readback_changed_between_validation_and_commit_rolls_back_both_records(self):
        with tempfile.TemporaryDirectory() as temp:
            control,work,_=self.prepare(Path(temp),unknown=True)
            ref=make_readback_evidence(control,'t-step-1','independent-host')
            original=lifecycle.apply_event
            def changed_after_validation(state,request,**kwargs):
                result=original(state,request,**kwargs)
                if request['op']=='pause':(work/'out.txt').write_text('concurrent change',encoding='utf-8')
                return result
            with patch.object(lifecycle,'apply_event',side_effect=changed_after_validation),self.assertRaises(ValueError):
                reconcile(control,'t-step-1',ref,'independent-host')
            self.assertEqual('ACTION_PENDING',task_status(control)['phase'])
            self.assertEqual(['t-step-1'],task_status(control)['unknown_tool_calls'])

    def test_pending_controls_are_read_only_and_cancel_ack_does_not_expand_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            control,_,_=self.prepare(Path(temp),unknown=True)
            journal=RunJournal(control/'control.sqlite3')
            journal.request_control('t','pause','pause at safe boundary',request_id='p')
            journal.request_control('t','cancel','stop this task',request_id='c')
            observed=task_status(control)
            self.assertEqual(2,len(observed['control_requests']))
            self.assertEqual('cancel',observed['pending_control']['operation'])
            with self.assertRaises(LifecycleError):resume_arguments(control,for_cancel=True)
            ref=make_readback_evidence(control,'t-step-1','independent-host')
            reconcile(control,'t-step-1',ref,'independent-host')
            saved=journal.load('t');snapshot=saved['snapshot'];snapshot['rejections']=3
            journal.save('t',saved['generation'],snapshot)
            with self.assertRaises(LifecycleError):resume_arguments(control)
            argv=resume_arguments(control,for_cancel=True)
            self.assertEqual('6',argv[argv.index('--max-steps')+1])
            self.assertEqual(['REQUESTED','REQUESTED'],[row['status'] for row in task_status(control)['control_requests']])


if __name__=='__main__':unittest.main()
