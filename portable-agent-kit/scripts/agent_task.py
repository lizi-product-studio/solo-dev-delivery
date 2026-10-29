#!/usr/bin/env python3
"""Receive a goal, validate a bounded plan, then run the existing Agent Runtime.

The caller is the trusted host/Skill and supplies write authority and reviewer.
This is an explicit CLI entry, not a desktop event subscription or background job.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_runtime.intake import IntakeError, build_context, compile_contract, planner_request, validate_plan
from agent_runtime.models import CodexDecisionModel
from agent_run import main as run_agent


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def project_storage(workspace, base=None):
    """Select project-scoped learning paths without creating any files.

    SkillLibrary has no internal project scope, so a shared state base must
    still use separate SQLite files for each resolved task workspace.
    """
    workspace = Path(workspace).resolve()
    project_key = hashlib.sha256(os.path.normcase(str(workspace)).encode('utf-8')).hexdigest()[:24]
    root = Path(base).expanduser().absolute() if base is not None else Path.home() / '.agi-like' / 'projects'
    bucket = root / project_key
    for path in (root, bucket):
        for ancestor in (path, *path.parents):
            if ancestor.is_symlink() or getattr(ancestor, 'is_junction', lambda: False)():
                raise IntakeError('project store must not use symbolic links or junctions')
        if path.exists() and not path.is_dir():
            raise IntakeError('project store must be a directory')
    root, bucket = root.resolve(), bucket.resolve()
    if bucket.is_relative_to(workspace):
        raise IntakeError('project store must stay outside the tool workspace')
    databases = {'memory': bucket / 'memory.sqlite3', 'skills': bucket / 'skills.sqlite3'}
    for database in databases.values():
        for suffix in ('', '-wal', '-shm', '-journal'):
            candidate = database.with_name(database.name + suffix)
            if candidate.is_symlink() or getattr(candidate, 'is_junction', lambda: False)():
                raise IntakeError('project learning control files must not be links')
            if candidate.exists() and (not candidate.is_file() or candidate.stat().st_nlink > 1):
                raise IntakeError('project learning control files must be regular files without hard links')
    return {'root': str(root), 'directory': str(bucket), 'scope': 'workspace-' + project_key,
            'memory': str(databases['memory']), 'skills': str(databases['skills'])}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--allow-write', action='append', required=True,
                        help="host-authorized relative file/directory; trailing / means directory, . means workspace")
    parser.add_argument('--authorization-ref', required=True)
    parser.add_argument('--control-root', type=Path, required=True, help='new directory outside workspace')
    parser.add_argument('--project-store', type=Path,
                        help='persistent state base outside workspace; memory/skills use a workspace-hash subdirectory (default ~/.agi-like/projects)')
    parser.add_argument('--review-script', type=Path, help='trusted host unittest source outside workspace')
    parser.add_argument('--plan', type=Path, help='untrusted saved candidate; skips the one model planning decision')
    parser.add_argument('--model')
    parser.add_argument('--effort', choices=('low', 'medium', 'high', 'xhigh', 'max', 'ultra'))
    parser.add_argument('--isolate-model-config', action='store_true')
    parser.add_argument('--max-steps', type=int, default=16, help='total model decision budget, including planning')
    args = parser.parse_args(argv)
    if args.isolate_model_config and not args.model:
        parser.error('--isolate-model-config requires an explicit --model')
    if not args.plan and args.max_steps < 2:
        parser.error('model planning needs at least 2 total decision steps')
    try:
        context = build_context(args.goal, args.workspace, args.allow_write, args.authorization_ref, max_steps=args.max_steps)
        workspace = Path(context['workspace'])
        directory = args.control_root.absolute()
        if directory.is_symlink() or getattr(directory, 'is_junction', lambda: False)():
            raise IntakeError('control root must not be a symbolic link or junction')
        directory = directory.resolve()
        if directory.is_relative_to(workspace):
            raise IntakeError('control root must be outside workspace')
        if directory.exists():
            raise IntakeError('control root already exists; inspect its task instead of replaying')
        learning = project_storage(workspace, args.project_store)
        review_bytes = None
        if args.review_script:
            review = args.review_script.resolve(strict=True)
            if review.is_relative_to(workspace) or not review.is_file() or review.stat().st_size > 1024 * 1024:
                raise IntakeError('trusted review must be an external file of at most 1 MiB')
            review_bytes = review.read_bytes()
        supplied_plan = None
        if args.plan:
            if args.plan.stat().st_size > 1024 * 1024:
                raise IntakeError('plan exceeds 1 MiB')
            supplied_plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))
    except (IntakeError, OSError, ValueError) as exc:
        parser.error(str(exc))

    directory.mkdir(parents=True, exist_ok=False)
    task_id = 'agent-' + uuid.uuid4().hex[:12]
    receipt_path = directory / 'intake-receipt.json'
    request_path = directory / 'request.json'
    save(request_path, context)
    report = {'format': 'goal-intake-receipt/1', 'status': 'PLANNING', 'task_id': task_id,
              'original_goal_sha256': context['goal_sha256'],
              'request_sha256': hashlib.sha256(request_path.read_bytes()).hexdigest(),
              'project_store_root': learning['root'], 'project_store': learning['directory'],
              'memory_scope': learning['scope'],
              'learning_databases': {key: learning[key] for key in ('memory', 'skills')},
              'control_root': str(directory), 'planning': {'decision_steps': 0, 'mode': 'provided-plan' if args.plan else 'codex'}}
    report['planning'].update(requested_model=args.model, reasoning_effort=args.effort,
                              isolated_model_config=args.isolate_model_config,
                              usage_source=str(directory / 'planning-events') if not args.plan else None)
    save(receipt_path, report)
    if review_bytes is not None:
        (directory / 'trusted-review.py').write_bytes(review_bytes)
    started = time.monotonic()
    try:
        if args.plan:
            proposal = supplied_plan
        else:
            report['planning']['decision_steps'] = 1
            save(receipt_path, report)
            planner = CodexDecisionModel(workspace, model=args.model,
                        reasoning_effort=args.effort, isolate_user_config=args.isolate_model_config,
                        log_dir=directory / 'planning-events')
            decision = planner(planner_request(context, task_id))
            if decision.get('action') != 'tool' or decision.get('name') != 'submit_plan':
                raise IntakeError('planner must submit_plan, not execute a task or finish')
            proposal = decision.get('arguments')
        plan = validate_plan(proposal, context)
        save(directory / 'plan.json', plan)
        report['plan_sha256'] = hashlib.sha256((directory / 'plan.json').read_bytes()).hexdigest()
        report['planning']['elapsed_seconds'] = round(time.monotonic() - started, 3)
        if plan['unknowns']:
            report.update(status='NEEDS_INPUT', unknowns=plan['unknowns'])
        elif review_bytes is None:
            report.update(status='NEEDS_VERIFIER', reason='A trusted independent review script is required; no task execution started.')
        else:
            contract = compile_contract(context, plan, task_id=task_id,
                review_sha256=hashlib.sha256(review_bytes).hexdigest(), planner_steps=report['planning']['decision_steps'])
            contract_path = directory / 'contract.json'
            save(contract_path, contract)
            # A proposal, missing business input or missing verifier must not
            # create a learning database or seed unverified task knowledge.
            learning = project_storage(workspace, args.project_store)
            Path(learning['directory']).mkdir(parents=True, exist_ok=True)
            learning = project_storage(workspace, args.project_store)
            runtime_receipt = directory / 'receipt.json'
            command = ['--goal', args.goal, '--workspace', str(workspace),
                       '--memory', learning['memory'], '--skills', learning['skills'],
                       '--project-id', learning['scope'],
                       '--db', str(directory / 'control.sqlite3'), '--contract', str(contract_path),
                       '--receipt', str(runtime_receipt), '--task-id', task_id,
                       '--review-script', str(directory / 'trusted-review.py'),
                       '--auto-verify',
                       '--max-steps', str(contract['intake']['execution_decision_budget'])]
            if args.model:
                command += ['--model', args.model]
            if args.effort:
                command += ['--effort', args.effort]
            if args.isolate_model_config:
                command += ['--isolate-model-config']
            save(directory / 'runtime-arguments.json', command)
            report.update(status='RUNNING', contract=str(contract_path), runtime_receipt=str(runtime_receipt))
            save(receipt_path, report)
            captured = io.StringIO()
            with redirect_stdout(captured):
                run_agent(command)
            (directory / 'runtime.stdout.log').write_text(captured.getvalue(), encoding='utf-8')
            runtime = json.loads(runtime_receipt.read_text(encoding='utf-8'))
            report.update(status=runtime['status'], execution_steps=runtime['steps'],
                          total_decision_steps=report['planning']['decision_steps'] + runtime['steps'])
    except Exception as exc:
        report.update(status='NEEDS_RECONCILIATION' if getattr(exc, 'outcome_unknown', False) or report['status'] == 'RUNNING'
                      else 'PLAN_REJECTED', error=type(exc).__name__ + ': ' + str(exc)[:2000])
        report['planning']['elapsed_seconds'] = round(time.monotonic() - started, 3)
    save(receipt_path, report)
    print(json.dumps({**report, 'intake_receipt': str(receipt_path)}, ensure_ascii=False), flush=True)
    return 0 if report['status'] == 'COMPLETED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
