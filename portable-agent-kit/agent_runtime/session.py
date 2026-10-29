"""Host adapter that binds Agent Runtime actions to the control plane."""
from __future__ import annotations

from pathlib import Path


class RuntimeSession:
    def __init__(self, runtime, task_id, lease_id, *, evidence_root=None, reviewer_id=None):
        self.runtime=runtime; self.task_id=task_id; self.lease_id=lease_id
        self.evidence_root=evidence_root; self.reviewer_id=reviewer_id

    def start(self, executor="agent-runtime"):
        return self.runtime.apply(self.task_id, self.lease_id, "start", {"executor":executor})

    def tool_result(self, tool_call_id, status, **payload):
        return self.runtime.apply(self.task_id, self.lease_id, "tool_result",
                                  {"tool_call_id":tool_call_id,"status":status,**payload})

    def evidence(self, reference):
        return self.runtime.apply(self.task_id, self.lease_id, "evidence", reference,
                                  evidence_root=self.evidence_root, reviewer_id=self.reviewer_id)

    def pause(self, *, checkpoint_id, resume_token, cursor=None, executor_stopped=True):
        self.runtime.apply(self.task_id, self.lease_id, "checkpoint",
                           {"id":checkpoint_id,"resume_token":resume_token,"cursor":cursor})
        return self.runtime.apply(self.task_id, self.lease_id, "pause",
                                  {"executor_stopped":executor_stopped})

    def resume(self, resume_token, executor="agent-runtime"):
        return self.runtime.apply(self.task_id, self.lease_id, "resume",
                                  {"resume_token":resume_token,"executor":executor,
                                   "executor_verified":True})

    def cancel(self, reason):
        return self.runtime.cancel(self.task_id, self.lease_id,
                                   executor_stopped=True, reason=reason)

    def reconcile(self, tool_call_id, evidence, outcome):
        return self.runtime.reconcile_external(self.task_id, self.lease_id,
                                               tool_call_id=tool_call_id,
                                               evidence=evidence, outcome=outcome,
                                               evidence_root=self.evidence_root,
                                               reviewer_id=self.reviewer_id)

    def finish(self):
        return self.runtime.apply(self.task_id, self.lease_id, "finish",
                                  {"executor_stopped":True},
                                  evidence_root=self.evidence_root,
                                  reviewer_id=self.reviewer_id)

    def snapshot(self):
        return self.runtime.get(self.task_id)
