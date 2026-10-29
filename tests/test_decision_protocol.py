import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable-agent-kit'))
from agent_runtime.models import CodexDecisionModel,ModelAdapterError,ModelActionUnknown
from agent_runtime import AgentRuntime,MemoryStore,ToolRegistry
from agent_runtime.journal import RunJournal,JournalError
from control_plane.runtime import DeliveryRuntime


class DecisionProtocolTests(unittest.TestCase):
    def call(self,source,**kwargs):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fake=root/'model.py';fake.write_text(source,encoding='utf-8')
            return CodexDecisionModel(root,executable=str(fake),log_dir=root/'logs',**kwargs)({
                'task_id':'t','goal':'g','step':1,'tools':{},'skills':[],'memory':[],'observations':[]})

    def source(self,value,extra=''):
        return ('import json,sys\nfrom pathlib import Path\n'
                'schema=Path(sys.argv[sys.argv.index("--output-schema")+1])\n'
                'assert json.loads(schema.read_text())["additionalProperties"] is False\n'
                'assert "skills.include_instructions=false" in sys.argv\n'
                'assert "shell_tool" in sys.argv\n'
                +extra+'\n'
                +'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":'+repr(json.dumps(value))+'}}))\n'
                +'print(json.dumps({"type":"turn.completed"}))\n')

    def test_schema_bound_decision_normalizes_argument_string(self):
        value={'action':'tool','name':'read_file','arguments_json':'{"path":"TASK.md"}','answer':None}
        result=self.call(self.source(value))
        self.assertEqual({'path':'TASK.md'},result['arguments'])
        self.assertNotIn('arguments_json',result)

    def test_invalid_inner_json_or_host_file_changes_are_rejected(self):
        value={'action':'tool','name':'read_file','arguments_json':'[]','answer':None}
        with self.assertRaises(ModelAdapterError):self.call(self.source(value))
        value={'action':'finish','name':None,'arguments_json':'{}','answer':'done'}
        extra='print(json.dumps({"type":"item.completed","item":{"type":"file_change","status":"completed"}}))'
        with self.assertRaisesRegex(ModelAdapterError,'forbidden host item'):
            self.call(self.source(value,extra))

    def test_isolation_requires_pinned_model_and_is_per_process(self):
        value={'action':'finish','name':None,'arguments_json':'{}','answer':'done'}
        with self.assertRaises(ValueError):self.call(self.source(value),isolate_user_config=True)
        self.assertEqual('finish',self.call(self.source(value,'assert "--ignore-user-config" in sys.argv'),
                         model='chosen-model',isolate_user_config=True)['action'])

    def test_unmanaged_host_effect_is_checkpointed_and_not_replayed(self):
        # A local subprocess double simulates a native MCP action. No external
        # service is contacted. The actual append proves rejection is too late
        # to treat this as a harmless, retryable model protocol error.
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'work';workspace.mkdir()
            effect=root/'host-effect.txt';fake=root/'host.py'
            extra=('effect=Path('+repr(str(effect))+')\n'
                   'with effect.open("a",encoding="utf-8") as out: out.write("x")\n'
                   'print(json.dumps({"type":"item.started","item":{"id":"outside-1","type":"mcp_tool_call"}}))\n')
            fake.write_text(self.source({'action':'finish','name':None,'arguments_json':'{}','answer':'done'},extra),encoding='utf-8')
            model=CodexDecisionModel(workspace,executable=fake,log_dir=root/'logs')
            journal=RunJournal(root/'journal.sqlite3');memory=MemoryStore(root/'memory.sqlite3')
            runtime=AgentRuntime(model,ToolRegistry(),memory,max_steps=3)
            result=runtime.run('g',task_id='host-unknown',journal=journal)
            self.assertEqual('NEEDS_RECONCILIATION',result.status)
            snapshot=journal.load('host-unknown')['snapshot']
            self.assertEqual('ACTION_PENDING',snapshot['phase'])
            self.assertEqual('mcp_tool_call',snapshot['events'][-1]['result']['details']['item']['type'])
            self.assertEqual('outside-1',snapshot['pending']['host_evidence']['item']['id'])
            self.assertEqual('x',effect.read_text(encoding='utf-8'))
            resumed=runtime.run('g',task_id='host-unknown',journal=journal,resume=True)
            self.assertEqual('NEEDS_RECONCILIATION',resumed.status)
            self.assertEqual('x',effect.read_text(encoding='utf-8'))
            with self.assertRaises(JournalError):
                journal.reconcile('host-unknown',action_id=snapshot['pending']['id'],
                    observation={'status':'SUCCEEDED'},verify_readback=lambda *_:False)
            self.assertFalse(any(row['kind']=='success' for row in memory.search('g')))

    def test_unknown_host_effect_blocks_control_plane(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);control=DeliveryRuntime(root/'control.sqlite3')
            contract={'task_id':'host-control','goal':'g','owner':'runtime','candidate':'v',
                'authorization_ref':'local-test','scope':['output'],'excluded':['external'],
                'policy':{'workspace_write':False},
                'acceptance':[{'id':'A','expected':'verified output','environment':'local'}]}
            control.create_task(contract,{'evidence.hash':True,'checkpoint.persist':True})
            lease=control.claim('host-control','runtime')
            def model(_):raise ModelActionUnknown('unmanaged host effect',details={'item':{'id':'native-1'}})
            result=AgentRuntime(model,ToolRegistry(),MemoryStore(root/'memory.sqlite3')).run('g',
                task_id='host-control',control=control,lease_id=lease['lease_id'],journal=RunJournal(root/'journal.sqlite3'))
            self.assertEqual('NEEDS_RECONCILIATION',result.status)
            self.assertEqual('UNKNOWN',control.get('host-control')['status'])

    def test_incomplete_or_timed_out_decision_host_requires_readback(self):
        for source,kwargs in [('import time\ntime.sleep(2)',{'timeout':0.1}),
                              ('print("malformed event")',{}),
                              ('import sys\nsys.exit(3)',{}),
                              ('import json\nprint(json.dumps({"type":"error","message":"worker disconnected"}))',{}),
                              ('import json,sys\nprint(json.dumps({"type":"error","message":"worker disconnected"}))\nsys.exit(3)',{})]:
            with self.subTest(source=source),self.assertRaises(ModelActionUnknown):
                self.call(source,**kwargs)


if __name__=='__main__':unittest.main()
