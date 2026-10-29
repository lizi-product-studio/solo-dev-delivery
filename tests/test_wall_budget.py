"""Focused persisted-deadline checks; local model double, real SQLite/files."""
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable-agent-kit'))
from agent_runtime import AgentRuntime,MemoryStore,ToolRegistry
from agent_runtime.journal import RunJournal
from control_plane.runtime import DeliveryRuntime
from agent_runtime.models import _request_timeout


class WallBudgetTests(unittest.TestCase):
    def test_late_model_cannot_write_or_reset_deadline_on_resume_and_cancel_still_works(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);control=DeliveryRuntime(root/'state.sqlite3')
            control.create_task({'task_id':'wall','goal':'g','owner':'runtime','candidate':'v',
                'authorization_ref':'local-test','scope':['out'],'excluded':['external'],
                'policy':{'workspace_write':False},'budget':{'max_seconds':10},
                'acceptance':[{'id':'A','expected':'artifact','environment':'local'}]},
                {'evidence.hash':True,'checkpoint.persist':True})
            lease=control.claim('wall','runtime')['lease_id'];journal=RunJournal(root/'state.sqlite3')
            clock=[time.time()];calls=[];tools=ToolRegistry()
            tools.register('write',lambda:(root/'out').write_text('unexpected'))
            def model(request):
                calls.append(request);clock[0]+=11
                return {'action':'tool','name':'write','arguments':{}}
            runtime=AgentRuntime(model,tools,MemoryStore(root/'memory.sqlite3'))
            with patch('agent_runtime.runtime.time.time',side_effect=lambda:clock[0]):
                first=runtime.run('g',task_id='wall',control=control,lease_id=lease,journal=journal)
                deadline=journal.load('wall')['snapshot']['budget_clock']
                again=runtime.run('g',task_id='wall',control=control,lease_id=lease,journal=journal,resume=True)
                self.assertEqual('BUDGET_EXHAUSTED',first.status)
                self.assertEqual('BUDGET_EXHAUSTED',again.status)
                self.assertEqual(deadline,journal.load('wall')['snapshot']['budget_clock'])
                self.assertEqual(1,len(calls));self.assertFalse((root/'out').exists())
                journal.request_control('wall','cancel','cancel expired task')
                cancelled=runtime.run('g',task_id='wall',control=control,lease_id=lease,journal=journal,resume=True)
                self.assertEqual('CANCELLED',cancelled.status);self.assertEqual(1,len(calls))

    def test_adapter_timeout_uses_remaining_budget(self):
        self.assertEqual(2,_request_timeout({'budget':{'remaining_seconds':2}},180))
        self.assertEqual(180,_request_timeout({'budget':{'remaining_seconds':None}},180))


if __name__=='__main__':unittest.main()
