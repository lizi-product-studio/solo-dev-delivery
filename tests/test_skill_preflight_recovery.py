"""Known skill preflight rejection is repairable; possible effects stay unknown.

The public CLI test uses a labelled local model subprocess. File tools,
independent review, scope enforcement and persisted Runtime state are real.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable-agent-kit'))
from agent_runtime import AgentRuntime,MemoryStore,ToolRegistry
from agent_runtime.file_tools import register_file_tools
from agent_runtime.journal import RunJournal
from agent_runtime.recipes import execute_recipe,recipe_procedure,SkillPreflightError
from agent_runtime.skills import SkillLibrary
from agent_runtime.skill_management import review_skill,activate_reviewed
from agent_runtime.tools import ToolError


class SkillPreflightRecoveryTests(unittest.TestCase):
    def writer_spec(self):
        return {'format':'tool-recipe/1','parameters':['step1_path','step1_value'],
                'steps':[{'tool':'write_json','arguments':{'path':{'$input':'step1_path'},
                                                        'value':{'$input':'step1_value'}}}]}

    def tools(self,workspace,*,read_label=True):
        tools=ToolRegistry()
        def write_file(path,text):
            if path!='report.json':raise ToolError('write rejected before effects: outside the authorized report.json scope')
            (workspace/path).write_bytes(text.encode('utf-8'))
            return {'path':path,'written':True}
        register_file_tools(tools,workspace,write_file)
        if not read_label:
            # Identical name/function, but no trusted read-effect declaration.
            tools.register('read_json',lambda path:{'data':json.loads((workspace/path).read_text())})
        return tools

    def test_public_single_step_scope_error_repairs_without_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir()
            source=workspace/'input.json';source.write_text('{"value":9}',encoding='utf-8')
            source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
            library=SkillLibrary(root/'skills.sqlite3');spec=self.writer_spec()
            spec['training_note']='PRIVATE_TRAINING_VALUE_MUST_NOT_ENTER_MANIFEST'
            library.register('reviewed-writer','parameterized structured file writer',spec)
            with self.assertRaises(SkillPreflightError):
                library.execute('reviewed-writer',{'step1_path':'report.json','step1_value':{}},self.tools(workspace))
            suite={'format':'skill-artifact-suite/1','cases':[
                {'id':'holdout-'+str(total),'inputs':{},'scope':['report.json'],
                 'arguments':{'step1_path':'report.json','step1_value':{'result':total}},
                 'assertions':[{'type':'json_pointer_equals','path':'report.json','pointer':'/result','value':total}]}
                for total in (3,8)]}
            (root/'suite.json').write_text(json.dumps(suite),encoding='utf-8')
            reviewed=review_skill(library,'reviewed-writer',root/'suite.json',root/'regression',reviewer_id='holdout-reviewer')
            active=activate_reviewed(library,'reviewed-writer',reviewed)
            with self.assertRaises(SkillPreflightError):
                library.execute('reviewed-writer',{'step1_path':'report.json','step1_value':{}},
                    self.tools(workspace),expected_version=active['version']+1)
            self.assertFalse((workspace/'report.json').exists())
            fake=root/'model.py'
            fake.write_text('''import json,sys
sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
req=json.loads(sys.stdin.read().split('Request:\\n',1)[1])
if req['step']==1:
 d={'action':'tool','name':'read_json','arguments':{'path':'input.json'}}
elif req['step']==2:
 manifest=req['skills']['reviewed-writer']
 assert manifest['procedure']==[{'tool':'write_json','arguments':{'path':{'$input':'step1_path'},'value':{'$input':'step1_value'}}}]
 assert 'PRIVATE_TRAINING_VALUE' not in json.dumps(manifest)
 assert req['tools']['read_json']['effects']=='read'
 d={'action':'skill','name':'reviewed-writer','arguments':{'step1_path':'input.json','step1_value':9}}
elif req['step']==3:
 rejection=req['observations'][-1]['result']
 assert rejection['status']=='FAILED' and rejection['pre_effect_rejection'] is True
 d={'action':'skill','name':'reviewed-writer','arguments':{'step1_path':'report.json','step1_value':{'result':9}}}
else:d={'action':'finish','answer':'independently verified report'}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(d)}}))
print(json.dumps({'type':'turn.completed'}))
''',encoding='utf-8')
            review=root/'review.py'
            review.write_text('import json,unittest\nfrom pathlib import Path\nclass OriginalGoal(unittest.TestCase):\n'
                ' def test_original_value(self):\n'
                '  self.assertEqual({"result":json.loads(Path("input.json").read_text())["value"]},json.loads(Path("report.json").read_text()))\n',encoding='utf-8')
            contract={'owner':'agent-runtime','candidate':'safe-repair','authorization_ref':'local-fixture',
                'scope':['report.json'],'inputs':['input.json'],'excluded':['external'],
                'policy':{'workspace_write':True,'shell_required':False,'require_input_reads':True},
                'acceptance':[{'id':'result','expected':'original result9','environment':'local',
                    'assertion':{'type':'json_pointer_equals','path':'report.json','pointer':'/result','value':9}}]}
            (root/'contract.json').write_text(json.dumps(contract),encoding='utf-8')
            process=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'portable-agent-kit/scripts/agent_run.py'),
                '--goal','read original input and produce report.json','--workspace',str(workspace),
                '--memory',str(root/'memory.sqlite3'),'--skills',str(root/'skills.sqlite3'),
                '--contract',str(root/'contract.json'),'--db',str(root/'control.sqlite3'),
                '--review-script',str(review),'--auto-verify','--receipt',str(root/'receipt.json'),'--max-steps','4'],
                env={**os.environ,'CODEX_CLI_PATH':str(fake),'PYTHONUTF8':'1'},capture_output=True,encoding='utf-8',timeout=30)
            self.assertEqual(0,process.returncode,process.stderr)
            report=json.loads((root/'receipt.json').read_text(encoding='utf-8'))
            self.assertEqual('COMPLETED',report['status']);self.assertEqual(3,report['steps'])
            self.assertEqual('host-artifact-review',report['events'][-1]['source'])
            self.assertEqual('report.json',report['events'][2]['tool_observations'][0]['decision']['arguments']['path'])
            self.assertEqual('FAILED',report['events'][1]['result']['status'])
            self.assertTrue(report['events'][1]['result']['pre_effect_rejection'])
            self.assertEqual([],report['control_state']['unknown_tool_calls'])
            self.assertEqual([],report['control_state']['failed_tool_calls'])
            self.assertNotIn('reconcile',[row['operation'] for row in report['control_events']])
            self.assertEqual(source_hash,hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual({'result':9},json.loads((workspace/'report.json').read_text()))
            self.assertEqual(1,library.get('reviewed-writer')['uses'])
            self.assertEqual([],library.failure_history('reviewed-writer'))

    def read_write_spec(self):
        return {'format':'tool-recipe/1','parameters':['input','output','value'],
                'steps':[{'tool':'read_json','arguments':{'path':{'$input':'input'}}},
                         {'tool':'write_json','arguments':{'path':{'$input':'output'},'value':{'$input':'value'}}}]}

    def test_trusted_reads_before_rejection_are_repairable_but_unlabelled_steps_stay_unknown(self):
        for read_label in (True,False):
            with self.subTest(read_label=read_label),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);workspace=root/'workspace';workspace.mkdir()
                (workspace/'input.json').write_text('{"value":9}')
                tools=self.tools(workspace,read_label=read_label);spec=self.read_write_spec();calls=[]
                def model(request):
                    calls.append(request['step'])
                    if request['step']<3:
                        return {'action':'skill','name':'read-write','arguments':{
                            'input':'input.json','output':'input.json' if request['step']==1 else 'report.json','value':{'result':9}}}
                    return {'action':'finish','answer':'fixed'}
                journal=RunJournal(root/'journal.sqlite3')
                runtime=AgentRuntime(model,tools,MemoryStore(root/'memory.sqlite3'),
                    {'read-write':lambda arguments:execute_recipe(spec,arguments,tools)},max_steps=3)
                done=runtime.run('g',task_id='t',journal=journal,
                    verifier=lambda *_:(workspace/'report.json').is_file() and json.loads((workspace/'report.json').read_text())=={'result':9})
                self.assertEqual('COMPLETED' if read_label else 'NEEDS_RECONCILIATION',done.status)
                self.assertEqual([1,2,3] if read_label else [1],calls)
                if read_label:self.assertTrue(done.events[0]['result']['pre_effect_rejection'])
                else:
                    self.assertEqual('UNKNOWN',done.events[0]['result']['status'])
                    again=runtime.run('g',task_id='t',journal=journal,resume=True)
                    self.assertEqual('NEEDS_RECONCILIATION',again.status)
                    self.assertEqual([1],calls)

    def test_any_prior_write_keeps_a_later_tool_rejection_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);tools=self.tools(root);spec={
                'format':'tool-recipe/1','parameters':['first','second','value'],
                'steps':[{'tool':'write_json','arguments':{'path':{'$input':'first'},'value':{'$input':'value'}}},
                         {'tool':'write_json','arguments':{'path':{'$input':'second'},'value':{'$input':'value'}}}]}
            args={'first':'report.json','second':'forbidden.json','value':{'result':1}}
            journal=RunJournal(root/'journal.sqlite3');calls=[]
            def model(_):
                calls.append(1);return {'action':'skill','name':'two-writes','arguments':args}
            runtime=AgentRuntime(model,tools,MemoryStore(root/'memory.sqlite3'),
                {'two-writes':lambda arguments:execute_recipe(spec,arguments,tools)})
            result=runtime.run('write once then reject',task_id='t',journal=journal)
            self.assertEqual('NEEDS_RECONCILIATION',result.status)
            self.assertEqual({'result':1},json.loads((root/'report.json').read_text()))
            self.assertEqual('UNKNOWN',result.events[0]['result']['status'])
            self.assertNotIn('pre_effect_rejection',result.events[0]['result'])
            before=(root/'report.json').stat().st_mtime_ns
            self.assertEqual('NEEDS_RECONCILIATION',runtime.run('write once then reject',task_id='t',journal=journal,resume=True).status)
            self.assertEqual(before,(root/'report.json').stat().st_mtime_ns);self.assertEqual([1],calls)

    def test_whole_plan_validation_precedes_first_write_and_plain_io_stays_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);tools=self.tools(root);spec=self.writer_spec()
            spec['steps'].append({'tool':'missing','arguments':{}})
            with self.assertRaises(SkillPreflightError):execute_recipe(spec,{'step1_path':'report.json','step1_value':{}},tools)
            self.assertFalse((root/'report.json').exists())
            def writes_then_loses_return(value):
                (root/'effect.txt').write_text(str(value));raise OSError('lost result after effect')
            tools.register('ambiguous',writes_then_loses_return,effects='read')
            # Even a read declaration cannot turn an arbitrary IO exception
            # into a trusted ToolError/preflight rejection.
            ambiguous={'format':'tool-recipe/1','parameters':['value'],
                'steps':[{'tool':'ambiguous','arguments':{'value':{'$input':'value'}}}]}
            with self.assertRaises(OSError):execute_recipe(ambiguous,{'value':9},tools)
            self.assertEqual('9',(root/'effect.txt').read_text())

    def test_nested_preflight_subtype_does_not_erase_an_earlier_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);tools=self.tools(root)
            def nested_rejection():raise SkillPreflightError('nested pure rejection')
            tools.register('nested_rejection',nested_rejection,effects='read')
            spec=self.writer_spec();spec['steps'].append({'tool':'nested_rejection','arguments':{}})
            with self.assertRaises(ToolError) as caught:
                execute_recipe(spec,{'step1_path':'report.json','step1_value':{'result':9}},tools)
            self.assertNotIsInstance(caught.exception,SkillPreflightError)
            self.assertEqual({'result':9},json.loads((root/'report.json').read_text()))

    def test_stale_activation_is_pure_preflight_and_does_not_call_tool(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);library=SkillLibrary(root/'skills.sqlite3')
            row=library.register('single','single tool',{'tool':'write'})
            proof=root/'proof.txt';proof.write_text('host regression fixture',encoding='utf-8')
            receipt=root/'receipt.json'
            receipt.write_text(json.dumps({'format':'skill-regression/1','status':'PASS','skill':'single',
                'version':row['version'],'spec_sha256':row['spec_sha256'],'owner':row['owner'],
                'reviewer_id':'separate-test-reviewer','checks':[{'id':'fixture','status':'PASS','evidence_refs':[
                    {'uri':'proof.txt','sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}]}]}),encoding='utf-8')
            library.activate('single',receipt)
            receipt.write_text('changed since activation',encoding='utf-8')
            calls=[];tools=ToolRegistry();tools.register('write',lambda:calls.append('effect'))
            with self.assertRaises(SkillPreflightError):library.execute('single',{},tools)
            self.assertEqual([],calls);self.assertEqual(0,library.get('single')['uses'])

    def test_procedure_projection_and_effects_are_host_controlled(self):
        spec=self.writer_spec();spec['private_training_value']='secret'
        self.assertEqual(spec['steps'],recipe_procedure(spec))
        self.assertNotIn('secret',json.dumps(recipe_procedure(spec)))
        malformed=self.writer_spec();malformed['steps'][0]['arguments']['value']={'literal':'secret'}
        self.assertEqual([],recipe_procedure(malformed))
        tools=ToolRegistry();tools.register('look',lambda:1)
        self.assertEqual('unknown',tools.manifest()['look']['effects'])
        with self.assertRaises(ToolError):tools.register('bad',lambda:1,effects=True)
        with tempfile.TemporaryDirectory() as temp:
            manifest=self.tools(Path(temp)).manifest()
            for name in ('read_file','read_json'):self.assertEqual('read',manifest[name]['effects'])
            for name in ('write_file','write_json','update_json','write_text_from_json'):
                self.assertEqual('write',manifest[name]['effects'])


if __name__=='__main__':unittest.main()
