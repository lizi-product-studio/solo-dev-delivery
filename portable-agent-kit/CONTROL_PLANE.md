# Control Plane contract

The Skill remains the model-facing entry point. The control plane moves the
parts that must survive a weak model or a restarted process outside the model:

```text
Skill adapter
    -> delivery-contract/1
    -> SQLite runtime (state, event log, lease, idempotency)
    -> host tool execution
    -> independent-review/1 evidence verifier
    -> self-improvement/2 candidate gate
```

The model may propose an action, but it cannot complete a task by writing
`PASS` into a message. The runtime checks the current revision, contract hash,
candidate, lease and idempotency key. The state machine keeps unknown tool
effects unresolved until a later readback is independently verified.

Independent receipts use `acceptance-receipt/1` and bind one of four kinds:
`semantic`, `ui`, `business`, or `platform`. Each receipt must contain the
current task revision, contract hash, candidate, independent reviewer and
PASS checks. A receipt for a different revision or a blocked/unknown task is
rejected.

## Host contract

A host adapter should provide:

1. a capability snapshot with `supported`, `unsupported` or `unknown` values;
2. a stable owner identity and a process-stop/recovery observation;
3. a tool executor that returns `SUCCEEDED`, `FAILED` or `UNKNOWN` without
   rewriting history;
4. an evidence root or equivalent verifier for the target environment;
5. persistent wake/checkpoint storage if it wants to run across turns.

The runtime does not execute arbitrary shell commands, create accounts, grant
permissions, contact external services or start a scheduler. When a host cannot
provide an independent verifier, the task remains `BLOCKED` or `UNKNOWN` and
the Skill should report the exact missing capability.

## Minimal lifecycle

```text
create -> claim -> start -> propose/execute one action -> record result
       -> record evidence -> independent review -> finish
       -> checkpoint/pause/reconcile when interrupted or unknown
```

The learning layer is separate from delivery. It records hashed experience,
requires distinct runs for repeated-root-cause proposals, reads bound review /
regression / canary / rollback receipts, and only returns a release candidate.
Activation, publication and rollback readback remain host operations.
