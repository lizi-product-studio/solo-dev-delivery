"""Managed learning lifecycle with actual files and labelled host fixtures.

No real model or business service is contacted. Training and suite oracles are
provided by the test host; the learned recipe invokes real registered tools.
"""
import hashlib
import json
import os
from dataclasses import asdict
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable-agent-kit'))
from agent_runtime.skills import SkillLibrary
from agent_runtime.file_tools import register_file_tools
from agent_runtime.tools import ToolRegistry
from agent_runtime.skill_management import review_skill,activate_reviewed,FORMAT
import agent_runtime.skill_management as skill_management
from agent_runtime import AgentRuntime,MemoryStore
from agent_runtime.journal import RunJournal
from control_plane.runtime import DeliveryRuntime


class ManagedSkillTests(unittest.TestCase):
    def setup_recipe(self,root):
        lib=SkillLibrary(root/'skills.sqlite3')
        for tid,value in [('training-a',1),('training-b',2)]:
            work=root/tid;work.mkdir()
            def write_file(path,text):
                (work/path).write_bytes(text.encode('utf-8'))
                return {'path':path,'written':True}
            tools=ToolRegistry();register_file_tools(tools,work,write_file)
            arguments={'path':'training.json','value':{'total':value}}
            actual=tools.call('write_json',arguments)
            self.assertEqual(value,json.loads((work/'training.json').read_text(encoding='utf-8'))['total'])
            lib.record_experience(tid,'write a verified total',[{'action':'tool',
                'decision':{'name':'write_json','arguments':arguments},'result':actual}],success=True)
        proposal=lib.propose_from_repeated_success('write a verified total')
        return lib,proposal['name']

    def suite(self,root,*,wrong=False):
        cases=[]
        for index,total in enumerate((42,7)):
            cases.append({'id':'holdout-'+str(index),'inputs':{'policy.json':json.dumps({'required_total':total})},
                'scope':['report.json'],'arguments':{'step1_path':'report.json','step1_value':{'total':total}},
                'assertions':[{'type':'json_pointer_equals','path':'report.json','pointer':'/total','value':99 if wrong else total}]})
        path=root/'suite.json';path.write_text(json.dumps({'format':FORMAT,'cases':cases}),encoding='utf-8')
        return path

    def activate(self,root,lib,name,folder='review'):
        receipt=review_skill(lib,name,self.suite(root),root/folder,reviewer_id='independent-host-reviewer')
        row=activate_reviewed(lib,name,receipt)
        return receipt,row

    def cli(self,root,*argv):
        process=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'portable-agent-kit/scripts/skill_manage.py'),
                                '--library',str(root/'skills.sqlite3'),*map(str,argv)],
                               capture_output=True,encoding='utf-8',timeout=20)
        return process,json.loads(process.stdout)

    def failure_receipt(self,root,name,row,task='failure-a',*,outcome='UNKNOWN'):
        report={'status':'NEEDS_RECONCILIATION' if outcome=='UNKNOWN' else 'CONTROL_BLOCKED','task_id':task,
            'control_state':{'task_id':task,'revision':1,'contract_hash':'a'*64,'candidate':'v1',
                'unknown_tool_calls':[task+'-step-1'] if outcome=='UNKNOWN' else [],
                'failed_tool_calls':[task+'-step-1'] if outcome=='FAILED' else [],
                'contract':{'goal':'use tested skill'}},
            'events':[{'step':1,'action':'skill','decision':{'name':name},
                       'skill_binding':{'name':name,'version':row['version'],'spec_sha256':row['spec_sha256']},
                       'result':{'status':outcome,'error':'labelled host fixture: actual return lost'}}],
            'control_events':[{'operation':'tool_result','request':{'task_id':task,'revision':1,'contract_hash':'a'*64,'candidate':'v1',
                'payload':{'tool_call_id':task+'-step-1','status':outcome}}}]}
        path=root/(task+'.json');path.write_text(json.dumps(report),encoding='utf-8')
        return path,hashlib.sha256(path.read_bytes()).hexdigest(),report

    def test_public_cli_reviews_readbacks_mutations_then_activates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root);suite=self.suite(root)
            process,result=self.cli(root,'review','--name',name,'--suite',suite,'--output',root/'review','--reviewer','independent-host')
            self.assertEqual(0,process.returncode,process.stderr);self.assertEqual('PASS',result['status'])
            self.assertEqual('CANDIDATE',lib.get(name)['status'])
            case=json.loads((root/'review/case-000.json').read_text(encoding='utf-8'))
            self.assertEqual('PASS',case['acceptance']['status'])
            self.assertTrue(case['acceptance']['mutations'][0]['rejected'])
            self.assertEqual(42,json.loads((root/'review/workspace-000/report.json').read_text())['total'])
            process,result=self.cli(root,'activate','--name',name,'--receipt',result['receipt'])
            self.assertEqual(0,process.returncode,process.stderr);self.assertEqual('ACTIVE',result['status'])
            process,result=self.cli(root,'disable','--name',name,'--reason','observed canary failure')
            self.assertEqual('DISABLED',result['status'])
            process,result=self.cli(root,'restore','--name',name,'--reason','original independent checks remain current')
            self.assertEqual('ACTIVE',result['status'])

    def test_wrong_artifact_does_not_pass_on_written_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root)
            path=review_skill(lib,name,self.suite(root,wrong=True),root/'review',reviewer_id='independent-host')
            receipt=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual('FAIL',receipt['status'])
            case=json.loads((root/'review/case-000.json').read_text(encoding='utf-8'))
            self.assertTrue(case['actual'][0]['result']['written'])
            with self.assertRaises(ValueError):activate_reviewed(lib,name,path)
            self.assertEqual('CANDIDATE',lib.get(name)['status'])

    def test_weak_or_training_reused_cases_are_rejected_before_execution(self):
        for variant in ('exists-only','training-id','uncovered-output','duplicate-id','renamed-content'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);lib,name=self.setup_recipe(root);path=self.suite(root)
                value=json.loads(path.read_text())
                if variant=='exists-only':value['cases'][0]['assertions']=[{'type':'file_exists','path':'report.json'}]
                elif variant=='training-id':value['cases'][0]['id']='training-a'
                elif variant=='uncovered-output':value['cases'][0]['scope'].append('other.txt')
                elif variant=='duplicate-id':value['cases'][1]['id']=value['cases'][0]['id']
                else:value['cases'][1]={**deepcopy(value['cases'][0]),'id':'a-renamed-copy'}
                path.write_text(json.dumps(value),encoding='utf-8')
                with self.assertRaises(ValueError):review_skill(lib,name,path,root/'review',reviewer_id='independent-host')
                self.assertFalse((root/'review').exists())

    def test_json_content_mutations_keep_valid_structure_and_detect_null_and_numeric_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root);path=self.suite(root)
            value=json.loads(path.read_text())
            for case in value['cases']:
                total=case['arguments']['step1_value']['total']
                case['arguments']['step1_value']={'nested':{'count':total,'other':'untouched'},
                    'nullable':None,'unchanged':['stable',{'x':3}]}
                case['assertions']=[
                    {'type':'json_pointer_equals','path':'report.json','pointer':'/nested/count','value':total},
                    {'type':'json_pointer_equals','path':'report.json','pointer':'/nullable','value':None}]
            path.write_text(json.dumps(value),encoding='utf-8')
            receipt=review_skill(lib,name,path,root/'review',reviewer_id='independent-host')
            self.assertEqual('PASS',json.loads(receipt.read_text())['status'])
            result=json.loads((root/'review/case-000.json').read_text())
            mutations=result['acceptance']['mutations'];self.assertEqual(2,len(mutations))
            for mutation in mutations:
                self.assertTrue(mutation['valid_json']);self.assertTrue(mutation['rejected'])
                self.assertIn('JSON value mismatch',mutation['rejection_error'])
                mutant=json.loads((root/'review'/mutation['evidence_ref']['uri']).read_text(encoding='utf-8'))
                self.assertEqual(['stable',{'x':3}],mutant['unchanged'])
                self.assertEqual('untouched',mutant['nested']['other'])
                if mutation['pointer']=='/nested/count':
                    self.assertEqual(43,mutant['nested']['count']);self.assertIsNone(mutant['nullable'])
                else:
                    self.assertIs(mutant['nullable'],False);self.assertEqual(42,mutant['nested']['count'])

    def test_text_and_hash_mutants_are_valid_utf8_and_change_actual_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib=SkillLibrary(root/'skills.sqlite3')
            lib.register('render','a host-provided candidate',{'format':'tool-recipe/1','parameters':['path','text'],
                'steps':[{'tool':'write_file','arguments':{'path':{'$input':'path'},'text':{'$input':'text'}}}]})
            cases=[]
            for index in range(2):
                text='任务%d：请提供资质报告；其余说明保持。'%index
                cases.append({'id':'text-holdout-'+str(index),'inputs':{},'scope':['note.txt'],
                    'arguments':{'path':'note.txt','text':text},'assertions':[
                        {'type':'text_contains','path':'note.txt','text':'资质报告'},
                        {'type':'file_sha256','path':'note.txt','sha256':hashlib.sha256(text.encode('utf-8')).hexdigest()}]})
            suite=root/'suite.json';suite.write_text(json.dumps({'format':FORMAT,'cases':cases}),encoding='utf-8')
            receipt=review_skill(lib,'render',suite,root/'review',reviewer_id='independent-host')
            self.assertEqual('PASS',json.loads(receipt.read_text())['status'])
            result=json.loads((root/'review/case-000.json').read_text(encoding='utf-8'))
            self.assertEqual({'required-text-removed','utf8-bytes-changed'},
                             {row['kind'] for row in result['acceptance']['mutations']})
            for mutation in result['acceptance']['mutations']:
                text=(root/'review'/mutation['evidence_ref']['uri']).read_text(encoding='utf-8')
                self.assertTrue(mutation['valid_utf8']);self.assertTrue(mutation['rejected'])
                if mutation['kind']=='required-text-removed':
                    self.assertNotIn('资质报告',text);self.assertIn('其余说明保持',text)
                    self.assertIn('text was not found',mutation['rejection_error'])
                else:
                    self.assertEqual(cases[0]['arguments']['text']+'\n',text)
                    self.assertIn('file hash mismatch',mutation['rejection_error'])

    def test_syntax_only_oracle_does_not_pass_valid_wrong_value_mutations(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root)
            def weak_syntax_check(workspace,assertions,*args):
                for assertion in assertions:json.loads((workspace/assertion['path']).read_text(encoding='utf-8'))
                return [{'status':'PASS','check_kind':'syntax-only test double'}]
            with patch.object(skill_management,'_check_assertions',side_effect=weak_syntax_check):
                path=review_skill(lib,name,self.suite(root),root/'review',reviewer_id='independent-host')
            self.assertEqual('FAIL',json.loads(path.read_text())['status'])
            result=json.loads((root/'review/case-000.json').read_text())
            self.assertTrue(result['acceptance']['mutations'][0]['valid_json'])
            self.assertFalse(result['acceptance']['mutations'][0]['rejected'])
            with self.assertRaises(ValueError):activate_reviewed(lib,name,path)

    def test_existing_output_update_recipe_uses_distinct_initial_documents_and_real_mutations(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib=SkillLibrary(root/'skills.sqlite3')
            lib.register('repair-total','edit an existing authorized document',{
                'format':'tool-recipe/1','parameters':['path','operations'],'steps':[
                    {'tool':'update_json','arguments':{'path':{'$input':'path'},'operations':{'$input':'operations'}}}]})
            cases=[]
            # Apart from IDs, only the initial editable state differs. Those
            # states must participate in the independent-case fingerprint.
            for index,start in enumerate((0,-9)):
                cases.append({'id':'edit-holdout-'+str(index),'inputs':{'policy.json':'{"total":42}'},
                    'scope':['report.json'],'initial_outputs':{'report.json':json.dumps({'total':start,'untouched':{'name':'保留'}})},
                    'arguments':{'path':'report.json','operations':[{'op':'replace','path':'/total','value':42}]},
                    'assertions':[{'type':'json_pointer_equals','path':'report.json','pointer':'/total','value':42},
                        {'type':'json_pointer_equals','path':'report.json','pointer':'/untouched/name','value':'保留'}]})
            suite=root/'suite.json';suite.write_text(json.dumps({'format':FORMAT,'cases':cases}),encoding='utf-8')
            path=review_skill(lib,'repair-total',suite,root/'review',reviewer_id='independent-host')
            self.assertEqual('PASS',json.loads(path.read_text())['status'])
            fingerprints=[]
            for index,case in enumerate(cases):
                work=root/'review'/('workspace-%03d'%index)
                self.assertEqual({'total':42,'untouched':{'name':'保留'}},json.loads((work/'report.json').read_text(encoding='utf-8')))
                self.assertEqual(case['inputs']['policy.json'],(work/'policy.json').read_text(encoding='utf-8'))
                evidence=json.loads((root/'review'/('case-%03d.json'%index)).read_text(encoding='utf-8'))['acceptance']
                fingerprints.append(evidence['case_fingerprint'])
                initial=evidence['initial_output_hashes'][0]
                self.assertEqual(hashlib.sha256(case['initial_outputs']['report.json'].encode('utf-8')).hexdigest(),initial['sha256'])
                self.assertTrue(all(m['valid_json'] and m['rejected'] for m in evidence['mutations']))
            self.assertEqual(2,len(set(fingerprints)))
            self.assertEqual('ACTIVE',activate_reviewed(lib,'repair-total',path)['status'])

    def test_initial_output_outside_scope_or_over_readonly_input_is_rejected_before_execution(self):
        variants=[{'outside.json':'{}'},{'../outside.json':'{}'},{'policy.json':'{}'},
                  {'report.json':'{}','./report.json':'{}'},['not-a-map'],{'report.json':'x'*65537}]
        for initial in variants:
            with self.subTest(initial_type=type(initial).__name__),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);lib,name=self.setup_recipe(root);path=self.suite(root)
                value=json.loads(path.read_text());value['cases'][0]['initial_outputs']=initial
                path.write_text(json.dumps(value),encoding='utf-8')
                with self.assertRaises(ValueError):review_skill(lib,name,path,root/'review',reviewer_id='independent-host')
                self.assertFalse((root/'review').exists())

    def test_editable_initial_state_does_not_grant_write_access_to_readonly_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib=SkillLibrary(root/'skills.sqlite3')
            lib.register('bad-editor','tries an unauthorized input mutation',{'format':'tool-recipe/1',
                'parameters':['path','operations'],'steps':[{'tool':'update_json',
                    'arguments':{'path':{'$input':'path'},'operations':{'$input':'operations'}}}]})
            cases=[]
            for index,start in enumerate((0,-9)):
                cases.append({'id':'readonly-holdout-'+str(index),'inputs':{'policy.json':'{"total":42}'},
                    'scope':['report.json'],'initial_outputs':{'report.json':json.dumps({'total':start})},
                    'arguments':{'path':'policy.json','operations':[{'op':'replace','path':'/total','value':999}]},
                    'assertions':[{'type':'json_pointer_equals','path':'report.json','pointer':'/total','value':42}]})
            suite=root/'suite.json';suite.write_text(json.dumps({'format':FORMAT,'cases':cases}),encoding='utf-8')
            path=review_skill(lib,'bad-editor',suite,root/'review',reviewer_id='independent-host')
            self.assertEqual('FAIL',json.loads(path.read_text())['status'])
            for index in range(2):
                work=root/'review'/('workspace-%03d'%index)
                self.assertEqual({'total':42},json.loads((work/'policy.json').read_text()))
            with self.assertRaises(ValueError):activate_reviewed(lib,'bad-editor',path)

    def test_changed_review_artifact_or_suite_blocks_use_and_activation(self):
        for target in ('suite.json','workspace-000/report.json'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);lib,name=self.setup_recipe(root)
                receipt=review_skill(lib,name,self.suite(root),root/'review',reviewer_id='independent-host')
                (root/'review'/target).write_text('changed bytes',encoding='utf-8')
                with self.assertRaises(ValueError):activate_reviewed(lib,name,receipt)
                self.assertEqual('CANDIDATE',lib.get(name)['status'])

    def test_failed_mutation_cannot_activate_even_with_pass_strings(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root)
            path=review_skill(lib,name,self.suite(root),root/'review',reviewer_id='independent-host')
            receipt=json.loads(path.read_text());case_path=root/'review/case-000.json'
            case=json.loads(case_path.read_text());case['acceptance']['mutations'][0]['rejected']=False
            case_path.write_text(json.dumps(case),encoding='utf-8')
            receipt['checks'][0]['evidence_refs'][0]['sha256']=hashlib.sha256(case_path.read_bytes()).hexdigest()
            path.write_text(json.dumps(receipt),encoding='utf-8')
            with self.assertRaises(ValueError):activate_reviewed(lib,name,path)

    def test_failure_archive_requires_bound_actual_skill_and_control_event(self):
        for variant in ('answer-only','missing-binding','wrong-control','wrong-hash','stale-control-version','resolved-failure'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);lib,name=self.setup_recipe(root);_,row=self.activate(root,lib,name)
                path,digest,report=self.failure_receipt(root,name,row)
                if variant=='answer-only':report['events']=[];report['answer']='the skill failed'
                elif variant=='missing-binding':del report['events'][0]['skill_binding']
                elif variant=='wrong-control':report['control_events'][0]['request']['payload']['tool_call_id']='different-action'
                elif variant=='stale-control-version':report['control_events'][0]['request']['revision']=0
                elif variant=='resolved-failure':report['control_state']['unknown_tool_calls']=[]
                path.write_text(json.dumps(report),encoding='utf-8')
                digest='0'*64 if variant=='wrong-hash' else hashlib.sha256(path.read_bytes()).hexdigest()
                with self.assertRaises(ValueError):lib.record_failure(name,path,digest,quarantine=True)
                self.assertEqual('ACTIVE',lib.get(name)['status']);self.assertEqual([],lib.failure_history(name))

    def test_failure_is_deduplicated_and_explicit_current_version_can_be_quarantined(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root);_,row=self.activate(root,lib,name)
            path,digest,_=self.failure_receipt(root,name,row)
            first=lib.record_failure(name,path,digest)
            self.assertFalse(first['quarantined']);self.assertEqual('ACTIVE',lib.get(name)['status'])
            self.assertTrue(lib.record_failure(name,path,digest)['reused'])
            self.assertEqual(1,len(lib.failure_history(name)))
            path,digest,_=self.failure_receipt(root,name,row,task='failure-b',outcome='FAILED')
            process,result=self.cli(root,'record-failure','--name',name,'--run-receipt',path,'--sha256',digest,'--quarantine')
            self.assertEqual(0,process.returncode,process.stderr);self.assertTrue(result['quarantined'])
            self.assertEqual('DISABLED',lib.get(name)['status'])

    def test_delayed_old_version_failure_does_not_disable_new_active_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root);_,old=self.activate(root,lib,name)
            path,digest,_=self.failure_receipt(root,name,old)
            changed={**old['spec'],'trigger':'a revised goal trigger'}
            lib.register(name,'revised candidate',changed)
            _,new=self.activate(root,lib,name,folder='new-review')
            self.assertGreater(new['version'],old['version'])
            result=lib.record_failure(name,path,digest,quarantine=True)
            self.assertFalse(result['quarantined']);self.assertEqual('ACTIVE',lib.get(name)['status'])
            lib.rollback(name,old['version'],reason='new canary failed')
            self.assertEqual(old['spec_sha256'],lib.get(name)['spec_sha256'])

    def test_invocation_version_change_rejected_before_first_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root);_,old=self.activate(root,lib,name)
            lib.register(name,'new candidate',{**old['spec'],'trigger':'different'})
            calls=[];tools=ToolRegistry();tools.register('write_json',lambda **kw:calls.append(kw))
            with self.assertRaisesRegex(ValueError,'version/spec changed'):
                lib.execute(name,{'step1_path':'out.json','step1_value':{'total':1}},tools,
                            expected_version=old['version'],expected_spec_sha256=old['spec_sha256'])
            self.assertEqual([],calls)

    def test_actual_runtime_unknown_skill_receipt_is_archived_without_clearing_pending_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root);_,row=self.activate(root,lib,name)
            workspace=root/'task';workspace.mkdir();tools=ToolRegistry()
            def lost_return(path,text):
                (workspace/path).write_bytes(text.encode('utf-8'))
                raise TimeoutError('real local write happened; return was lost')
            register_file_tools(tools,workspace,lost_return)
            control=DeliveryRuntime(root/'control.sqlite3',evidence_root=workspace)
            contract={'task_id':'actual-runtime-failure','owner':'agent-runtime','candidate':'v1',
                'goal':'write a checked total','authorization_ref':'temporary-local-test','scope':['out.json'],
                'excluded':['external'],'policy':{'workspace_write':True,'shell_required':False},
                'acceptance':[{'id':'A','expected':'total=42','environment':'local',
                    'assertion':{'type':'json_pointer_equals','path':'out.json','pointer':'/total','value':42}}]}
            control.create_task(contract,{'fs.read':True,'fs.write':True,'evidence.hash':True,'checkpoint.persist':True})
            lease=control.claim(contract['task_id'],'test-host')
            model=lambda request:{'action':'skill','name':name,
                'arguments':{'step1_path':'out.json','step1_value':{'total':42}}}
            skills={name:lambda arguments:lib.execute(name,arguments,tools,
                expected_version=row['version'],expected_spec_sha256=row['spec_sha256'])}
            journal=RunJournal(root/'journal.sqlite3')
            runtime=AgentRuntime(model,tools,MemoryStore(root/'memory.sqlite3'),skills,skill_library=lib)
            result=runtime.run(contract['goal'],task_id=contract['task_id'],control=control,
                lease_id=lease['lease_id'],journal=journal)
            self.assertEqual('NEEDS_RECONCILIATION',result.status)
            self.assertEqual(42,json.loads((workspace/'out.json').read_text())['total'])
            report={**asdict(result),'control_state':control.get(result.task_id),'control_events':control.events(result.task_id)}
            path=root/'runtime-receipt.json';path.write_text(json.dumps(report),encoding='utf-8')
            archived=lib.record_failure(name,path,hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual('UNKNOWN',archived['outcome']);self.assertFalse(archived['quarantined'])
            self.assertEqual('ACTIVE',lib.get(name)['status'])
            self.assertEqual('ACTION_PENDING',journal.load(result.task_id)['snapshot']['phase'])
            self.assertEqual('UNKNOWN',control.get(result.task_id)['status'])


if __name__=='__main__':unittest.main()
