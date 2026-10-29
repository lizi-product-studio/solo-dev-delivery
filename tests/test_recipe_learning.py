import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable-agent-kit'))
from agent_runtime.recipes import compile_traces, execute_recipe
from agent_runtime.regression import regress_recipe
from agent_runtime.skills import SkillLibrary
from agent_runtime.tools import ToolRegistry


class RecipeLearningTests(unittest.TestCase):
    def trace(self,path,text):
        return [{'action':'tool','decision':{'name':'write_file','arguments':{'path':path,'text':text}},'result':{'written':True}},
                {'action':'tool','decision':{'name':'read_file','arguments':{'path':path}},'result':text}]

    def setup_recipe(self,root):
        lib=SkillLibrary(root/'skills.sqlite3')
        for tid,value in [('source-a','sensitive A'),('source-b','sensitive B')]:
            lib.record_experience(tid,'write and readback',self.trace(tid+'.txt',value),success=True)
        proposal=lib.propose_from_repeated_success('write and readback')
        return lib,proposal['name']

    def factory(self,root,case):
        workspace=root/case['id'];workspace.mkdir(exist_ok=True)
        tools=ToolRegistry()
        def target(path):
            result=(workspace/path).resolve();result.relative_to(workspace.resolve());return result
        def write_file(path,text):
            target(path).write_text(text,encoding='utf-8');return {'written':True}
        tools.register('write_file',write_file)
        tools.register('read_file',lambda path:target(path).read_text(encoding='utf-8'))
        return tools

    def cases(self):
        return [{'id':'holdout-'+str(i),'arguments':{'step1_path':p,'step1_text':value,'step2_path':p},
                 'expected':[{'tool':'write_file','result':{'written':True}},{'tool':'read_file','result':value}]}
                for i,p,value in [(1,'new.txt','unseen Chinese 内容'),(2,'second.txt','another unseen value')]]

    def test_compile_is_executable_without_copying_training_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root)
            spec=lib.get(name)['spec']
            self.assertNotIn('sensitive',json.dumps(spec))
            self.assertEqual('tool-recipe/1',spec['format'])
            receipt=regress_recipe(lib,name,self.cases(),lambda case:self.factory(root,case),root/'regression',reviewer_id='holdout-reviewer')
            lib.activate(name,receipt)
            case={'id':'third-task'};tools=self.factory(root,case)
            output=lib.execute(name,{'step1_path':'third.txt','step1_text':'third task data','step2_path':'third.txt'},tools)
            self.assertEqual('third task data',output[-1]['result'])
            self.assertEqual('third task data',(root/'third-task/third.txt').read_text(encoding='utf-8'))
            self.assertEqual(1,lib.get(name)['uses'])

    def test_failed_or_leaked_holdout_never_activates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root);cases=self.cases()
            cases[0]['id']='source-a'
            with self.assertRaises(ValueError):
                regress_recipe(lib,name,cases,lambda c:self.factory(root,c),root/'leaked',reviewer_id='reviewer')
            cases=self.cases();cases[0]['expected']=[]
            path=regress_recipe(lib,name,cases,lambda c:self.factory(root,c),root/'failed',reviewer_id='reviewer')
            with self.assertRaises(ValueError):lib.activate(name,path)
            self.assertEqual('CANDIDATE',lib.get(name)['status'])

    def test_disable_restore_and_stale_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root)
            path=regress_recipe(lib,name,self.cases(),lambda c:self.factory(root,c),root/'gates',reviewer_id='reviewer')
            lib.activate(name,path);lib.disable(name,reason='canary failure')
            self.assertEqual([],lib.list_active())
            lib.restore(name,reason='canary diagnosis resolved')
            self.assertEqual('ACTIVE',lib.get(name)['status'])
            (root/'gates/case-000.json').write_text('tampered',encoding='utf-8')
            with self.assertRaises(ValueError):lib.execute(name,self.cases()[0]['arguments'],self.factory(root,{'id':'tamper'}))

    def test_incompatible_or_failed_traces_do_not_compile(self):
        a=self.trace('a','b');b=self.trace('x','y')
        b[0]['result']={'status':'UNKNOWN'}
        self.assertIsNone(compile_traces([a,b]))
        self.assertIsNone(compile_traces([a,[]]))

    def test_invalid_later_step_is_rejected_before_first_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root);spec=lib.get(name)['spec']
            tools=self.factory(root,{'id':'preflight'})
            spec['steps'][1]['arguments']={'wrong_parameter':{'$input':'step2_path'}}
            with self.assertRaises(ValueError):
                execute_recipe(spec,self.cases()[0]['arguments'],tools)
            self.assertEqual([],list((root/'preflight').iterdir()))

    def test_rollback_restores_verified_version_and_versions_do_not_collide(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib,name=self.setup_recipe(root)
            path=regress_recipe(lib,name,self.cases(),lambda c:self.factory(root,c),root/'first-gate',reviewer_id='reviewer')
            lib.activate(name,path)
            first=lib.get(name)
            second=lib.register(name,'new candidate',{'tool':'missing'})
            self.assertEqual(2,second['version'])
            restored=lib.rollback(name,1,reason='candidate regression failed')
            self.assertEqual(first['spec_sha256'],restored['spec_sha256'])
            third=lib.register(name,'another fix',{'tool':'read_file'})
            self.assertEqual(3,third['version'])
            (root/'first-gate/case-000.json').write_text('bad evidence',encoding='utf-8')
            with self.assertRaises(ValueError):lib.rollback(name,1,reason='stale rollback target')


if __name__=='__main__':unittest.main()
