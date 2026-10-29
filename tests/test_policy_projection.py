import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable-agent-kit'))
from agent_runtime.file_tools import register_file_tools
from agent_runtime.tools import ToolRegistry,ToolError


class PolicyProjectionTests(unittest.TestCase):
    def test_actual_projection_preserves_values_and_rejects_missing_or_unapproved_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'policy.json').write_text('{"required":["a","b"]}')
            draft={'status':'DRAFT','questions':[{'field':'a','text':'A?'},{'field':'b','text':'B?'},{'field':'extra','text':'X?'}]}
            target=root/'draft.json';target.write_text(json.dumps(draft))
            def write(path,text):
                if path!='draft.json':raise ToolError('read-only input')
                (root/path).write_bytes(text.encode());return {'written':True}
            tools=ToolRegistry();register_file_tools(tools,root,write,policy_inputs=['policy.json'])
            args={'path':'draft.json','pointer':'/questions','allowed_path':'policy.json','allowed_pointer':'/required','field':'field'}
            tools.call('project_json_fields',args)
            fixed=json.loads(target.read_text());self.assertEqual('DRAFT',fixed['status'])
            self.assertEqual(draft['questions'][:2],fixed['questions'])
            with self.assertRaises(ToolError):tools.call('project_json_fields',{**args,'allowed_path':'draft.json'})
            target.write_text('{"questions":[{"field":"a","text":"A?"}]}');before=target.read_bytes()
            with self.assertRaises(ToolError):tools.call('project_json_fields',args)
            self.assertEqual(before,target.read_bytes())


if __name__=='__main__':unittest.main()
