# Solo 1.0 A/B experiment artifacts

This directory contains the frozen 10-pair A/B evaluation used to compare a plain Codex session (`baseline`) with a session that has the Solo 1.0 Skill installed (`solo-1.0`). The experiment uses 20 fresh sessions, 20 disposable workspaces, the same `gpt-6.1-sol` model, medium reasoning effort, CLI, timeout, approval policy, and sandbox configuration.

## What is frozen

- Ten small but adversarial Python repair tasks in `tasks/`.
- The executable acceptance contract in `judge.py` and its self-test in `judge_selftest.py`.
- `experiment-freeze.json`, which records task hashes, Skill/package hashes, model settings, and failure semantics.
- `results.jsonl`, one receipt per selected trial.
- `summary.json`, paired status and descriptive metrics.
- `validate_experiment.py`, a local integrity check.

A trial is quality-comparable only when the receipt is `completed` and `evaluation.task_success` is true. Capacity errors, timeouts, connection errors, and policy failures are censored; they are preserved and never counted as successful work. A `task_failed` receipt is a real completed session whose candidate did not satisfy the frozen contract. Zero observed intervention means the process stayed noninteractive; it is not a claim that a human could never intervene.

## Result of this run

All 20 selected receipts are real isolated sessions. One of the ten task pairs completed successfully in both arms. Nine pairs were censored by capacity/connection availability (one baseline repair also exposed a real task failure). The run therefore demonstrates the harness and observability contract, but it is not enough to claim that Solo 1.0 is statistically superior. The raw logs show exactly why each non-comparable sample was excluded.

The selected raw artifacts are outside the Git repository to avoid committing auth material or large session logs:

- `C:\Users\Administrator\Documents\Codex\ab-solo-1.0-2026-10-27-matrix`
- `C:\Users\Administrator\Documents\Codex\ab-solo-1.0-2026-10-27-matrix-repair`

Each trial directory contains `trial-config.json`, `prompt.txt`, `events.jsonl`, `stderr.log`, `final.txt`, `sessions/`, `evaluation.json`, `receipt.json`, and the isolated `workspace/`. The four original wrong-task-name harness failures remain in the first matrix root and are intentionally excluded from `results.jsonl`; corrected replacement runs are in the repair root.

## Re-run or validate

From the repository root:

```powershell
python evaluations/ab-solo-1.0-2026-10-26/judge_selftest.py
python evaluations/ab-solo-1.0-2026-10-26/validate_experiment.py --check-raw
```

To run a fresh matrix, use the runner and the corrected task list in `evaluations/ab-solo-1.0-2026-10-27-run_matrix.py`. Give it a new raw directory so prior evidence is never overwritten. Do not switch models or silently retry capacity failures if you want to preserve the frozen protocol.
