"""A stop accepted before dispatch must beat a registered tool action.

This is a deterministic local race reproduction: a separate SQLite connection
records cancel immediately before the executor persists its action claim.
The model is a labelled in-process double; the file append is a real effect.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

KIT=Path(__file__).resolve().parents[1]/'portable-agent-kit'
sys.path.insert(0,str(KIT))
from agent_runtime import AgentRuntime,MemoryStore,ToolRegistry
from agent_runtime.journal import RunJournal


class ActionControlBoundaryTests(unittest.TestCase):
    def test_cancel_accepted_before_action_claim_prevents_actual_tool_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);journal=RunJournal(root/'journal.sqlite3')
            marker=root/'effect.txt';tools=ToolRegistry();injected=[]
            def append():
                with marker.open('a',encoding='utf-8') as output:output.write('x')
                return {'written':True}
            tools.register('append',append)
            # Keep the injection before the atomic claim when the fixed API is
            # present, rather than accidentally bypassing this regression after
            # Runtime stops using the ordinary save() for action dispatch.
            boundary='prepare_action' if hasattr(journal,'prepare_action') else 'save'
            original_save=getattr(journal,boundary)
            def save_with_concurrent_cancel(task_id,expected_generation,snapshot):
                if snapshot['phase']=='ACTION_PENDING' and not injected:
                    second_connection=RunJournal(root/'journal.sqlite3')
                    request=second_connection.request_control(task_id,'cancel',
                        'cancel accepted before action dispatch',request_id='before-action')
                    injected.append(request)
                return original_save(task_id,expected_generation,snapshot)
            runtime=AgentRuntime(lambda _:{'action':'tool','name':'append','arguments':{}},
                                 tools,MemoryStore(root/'memory.sqlite3'),max_steps=3)
            with patch.object(journal,boundary,side_effect=save_with_concurrent_cancel):
                result=runtime.run('append only while authorized',task_id='t',journal=journal)
            self.assertEqual(1,len(injected),'the deterministic pre-dispatch race was not exercised')
            self.assertEqual('REQUESTED',injected[0]['status'])
            self.assertEqual('CANCELLED',result.status)
            self.assertEqual('ACKNOWLEDGED',journal.control_requests('t')[0]['status'])
            self.assertFalse(marker.exists(),'registered tool executed after cancel won the pre-dispatch boundary')


if __name__=='__main__':unittest.main()
