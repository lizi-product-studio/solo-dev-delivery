import sys
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable-agent-kit"))
from agent_runtime import AgentRuntime, MemoryStore, ToolRegistry, SkillLibrary
from control_plane.runtime import DeliveryRuntime
from control_plane.runtime import ControlPlaneError
from unittest.mock import patch


class AgentRuntimeTests(unittest.TestCase):
    def activate_skill(self, lib, name, root):
        row=lib.get(name)
        evidence=Path(root)/'regression.txt';evidence.write_text('recipe regression passed',encoding='utf-8')
        receipt=Path(root)/'regression.json'
        receipt.write_text(json.dumps({'format':'skill-regression/1','status':'PASS','skill':name,
            'version':row['version'],'spec_sha256':row['spec_sha256'],'owner':row['owner'],
            'reviewer_id':'independent-test-reviewer','checks':[{'id':'recipe-regression','status':'PASS',
            'evidence_refs':[{'uri':'regression.txt','sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]}]}),encoding='utf-8')
        return lib.activate(name,receipt)

    def test_real_local_artifact_task_runs_model_tool_memory_loop(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); memory=MemoryStore(root/'memory.sqlite3'); tools=ToolRegistry()
            target=root/'result.txt'
            tools.register('write_file', lambda path, text: Path(path).write_text(text, encoding='utf-8') or {'path':path,'written':True}, description='write a local UTF-8 file')
            calls=[]
            def model(req):
                calls.append(req)
                if not req['memory']:
                    return {'action':'tool','name':'write_file','arguments':{'path':str(target),'text':'runtime completed\n'}}
                return {'action':'finish','answer':'local artifact written and read back'}
            result=AgentRuntime(model, tools, memory, max_steps=3).run('write a result file',
                verifier=lambda goal,answer,events: target.exists() and target.read_text(encoding='utf-8')=='runtime completed\n')
            self.assertEqual('COMPLETED', result.status)
            self.assertEqual('runtime completed\n', target.read_text(encoding='utf-8'))
            self.assertEqual(2, result.steps)
            self.assertTrue(memory.search('write_file'))
            self.assertEqual(['tool','finish'], [x['action'] for x in result.events])

    def test_runtime_rejects_unknown_tool_and_exposes_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            def model(_): return {'action':'tool','name':'missing','arguments':{}}
            result=AgentRuntime(model, ToolRegistry(), MemoryStore(Path(temp)/'m.sqlite3'), max_steps=2).run('x')
            self.assertEqual('BUDGET_EXHAUSTED', result.status)

    def test_finish_without_verifier_never_creates_success_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            memory=MemoryStore(Path(temp)/'m.sqlite3')
            result=AgentRuntime(lambda _: {'action':'finish','answer':'done'},ToolRegistry(),memory).run('mandatory output')
            self.assertEqual('UNVERIFIED',result.status)
            self.assertEqual([],memory.search('mandatory'))

    def test_current_observation_survives_unrelated_chinese_goal(self):
        with tempfile.TemporaryDirectory() as temp:
            tools=ToolRegistry();tools.register('inspect',lambda: {'value':1742})
            def model(req):
                if req['step']==1:return {'action':'tool','name':'inspect','arguments':{}}
                self.assertEqual({'value':1742},req['observations'][0]['result'])
                return {'action':'finish','answer':'1742'}
            AgentRuntime(model,tools,MemoryStore(Path(temp)/'m.sqlite3')).run('核对采购数量')

    def test_active_skill_executes_real_tool_and_increments_uses(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib=SkillLibrary(root/'s.sqlite3');target=root/'out.txt'
            tools=ToolRegistry();tools.register('write',lambda text: target.write_text(text,encoding='utf-8'))
            lib.register('writer','local writer',{'tool':'write'});self.activate_skill(lib,'writer',root)
            lib.execute('writer',{'text':'actual artifact'},tools)
            self.assertEqual('actual artifact',target.read_text(encoding='utf-8'))
            self.assertEqual(1,lib.list_active()[0]['uses'])

    def test_skill_library_promotes_and_persists_across_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            lib=SkillLibrary(Path(temp)/'skills.sqlite3')
            lib.register('local-write', 'write a local artifact', {'tool':'write_file'})
            self.assertEqual([], lib.list_active())
            self.activate_skill(lib,'local-write',temp)
            self.assertEqual('local-write', lib.list_active()[0]['name'])

    def test_repeated_success_generates_candidate_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            lib=SkillLibrary(Path(temp)/'skills.sqlite3')
            trace=[{'action':'tool','decision':{'name':'write_file','arguments':{'path':'old','text':'old'}},'result':{'written':True}}]
            lib.record_experience('t1','repeatable task',trace,success=True)
            self.assertIsNone(lib.propose_from_repeated_success('repeatable task'))
            lib.record_experience('t2','repeatable task',trace,success=True)
            proposal=lib.propose_from_repeated_success('repeatable task')
            self.assertEqual('CANDIDATE', proposal['status'])
            with self.assertRaises(ValueError): lib.activate(proposal['name'], {'status':'PASS','skill':'other'})
            self.activate_skill(lib,proposal['name'],temp)
            self.assertEqual(proposal['name'], lib.list_active()[0]['name'])

    def test_runtime_is_bound_to_control_plane_and_cannot_finish_without_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); evidence=root/'evidence'; evidence.mkdir()
            control=DeliveryRuntime(root/'control.sqlite3', evidence_root=evidence)
            contract={"task_id":"bound","goal":"write artifact","owner":"runtime","candidate":"c",
                      "authorization_ref":"local","scope":["result.txt"],"excluded":["external"],
                      "policy":{"workspace_write":False},"acceptance":[{"id":"A","expected":"file","environment":"local","assertion":{"type":"file_exists"}}]}
            state=control.create_task(contract,{"evidence.hash":True,"checkpoint.persist":True,"fs.read":True})
            lease=control.claim(state["task_id"],"runtime")
            tools=ToolRegistry(); target=evidence/'result.txt'
            tools.register('write_file',lambda **kw: target.write_text(kw['text'],encoding='utf-8') or {'written':True})
            def model(req):
                if req['step']==1: return {'action':'tool','name':'write_file','arguments':{'text':'ok'}}
                return {'action':'finish','answer':'done'}
            result=AgentRuntime(model,tools,MemoryStore(root/'m.sqlite3'),max_steps=2).run('write artifact',task_id=state['task_id'],control=control,lease_id=lease['lease_id'])
            self.assertEqual('CONTROL_BLOCKED',result.status)

    def test_lease_loss_after_model_response_prevents_tool_side_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);control=DeliveryRuntime(root/'c.sqlite3')
            contract={'task_id':'lease-loss','owner':'runtime','goal':'g','candidate':'v',
                'authorization_ref':'local','scope':['out'],'excluded':['external'],
                'policy':{'workspace_write':False},'acceptance':[{'id':'A','expected':'e','environment':'local'}]}
            control.create_task(contract,{'evidence.hash':True,'checkpoint.persist':True})
            lease=control.claim('lease-loss','runtime');calls=[];tools=ToolRegistry()
            tools.register('write',lambda:calls.append('write'))
            model=lambda req:{'action':'tool','name':'write','arguments':{}}
            with patch.object(control,'heartbeat',side_effect=ControlPlaneError('LEASE_EXPIRED')):
                result=AgentRuntime(model,tools,MemoryStore(root/'m.sqlite3')).run('g',
                    task_id='lease-loss',control=control,lease_id=lease['lease_id'])
            self.assertEqual('LEASE_LOST',result.status)
            self.assertEqual([],calls)

    def test_unchanged_repeated_read_requests_replan_with_remaining_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);control=DeliveryRuntime(root/'c.sqlite3')
            contract={'task_id':'read-loop','owner':'runtime','goal':'read TASK then data','candidate':'v',
                'authorization_ref':'local','scope':['out'],'inputs':['TASK.md','data.json'],
                'excluded':['external'],'policy':{'workspace_write':False},
                'acceptance':[{'id':'A','expected':'e','environment':'local'}]}
            control.create_task(contract,{'evidence.hash':True,'checkpoint.persist':True,'fs.read':True})
            lease=control.claim('read-loop','runtime');tools=ToolRegistry();requests=[]
            tools.register('read_file',lambda path:'Read TASK.md then data.json',effects='read')
            def model(request):
                requests.append(request)
                return {'action':'tool','name':'read_file','arguments':{'path':'TASK.md'}}
            AgentRuntime(model,tools,MemoryStore(root/'m.sqlite3'),max_steps=3).run(
                contract['goal'],task_id='read-loop',control=control,lease_id=lease['lease_id'])
            self.assertNotIn('runtime_feedback',requests[1])
            feedback=requests[2]['runtime_feedback']
            self.assertEqual('REPLAN_REQUIRED',feedback['decision'])
            self.assertEqual(['data.json'],feedback['remaining_required_inputs'])
            self.assertEqual(['TASK.md'],feedback['already_read'])


if __name__ == '__main__': unittest.main()
