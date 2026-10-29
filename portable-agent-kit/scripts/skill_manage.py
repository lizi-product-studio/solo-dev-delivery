#!/usr/bin/env python3
"""Propose, independently review, activate and recover parameterized skills."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_runtime.skills import SkillLibrary
from agent_runtime.skill_management import review_skill, activate_reviewed
from agent_runtime.failure_learning import propose_improvement
from agent_runtime.improvement_canary import compare_workflows
from agent_runtime.file_tools import register_file_tools
from agent_runtime.tools import ToolRegistry,ToolError
from agent_runtime.models import CodexDecisionModel,OpenAICompatibleModel,ModelAdapterError
from agent_runtime.codex_host import CodexHostError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    propose = sub.add_parser('propose'); propose.add_argument('--goal', required=True)
    improve=sub.add_parser('improve');improve.add_argument('--run',type=Path,action='append',required=True)
    improve.add_argument('--output',type=Path,required=True);improve.add_argument('--model')
    improve.add_argument('--provider',choices=('codex','api'),default='codex')
    canary=sub.add_parser('canary');canary.add_argument('--name',required=True);canary.add_argument('--baseline',required=True)
    canary.add_argument('--suite',type=Path,required=True);canary.add_argument('--baseline-suite',type=Path,required=True)
    canary.add_argument('--output',type=Path,required=True);canary.add_argument('--reviewer',required=True)
    canary.add_argument('--activate',action='store_true')
    review = sub.add_parser('review'); review.add_argument('--name', required=True)
    review.add_argument('--suite', type=Path, required=True); review.add_argument('--output', type=Path, required=True)
    review.add_argument('--reviewer', required=True)
    activate = sub.add_parser('activate'); activate.add_argument('--name', required=True)
    activate.add_argument('--receipt', type=Path, required=True)
    activate.add_argument('--canary',type=Path)
    for operation in ('disable', 'restore', 'rollback'):
        item = sub.add_parser(operation); item.add_argument('--name', required=True); item.add_argument('--reason', required=True)
        if operation == 'rollback': item.add_argument('--version', type=int, required=True)
    failure = sub.add_parser('record-failure'); failure.add_argument('--name', required=True)
    failure.add_argument('--run-receipt','--receipt',dest='receipt',type=Path,required=True); failure.add_argument('--sha256', required=True)
    failure.add_argument('--quarantine', action='store_true')
    history = sub.add_parser('failures'); history.add_argument('--name')
    monitor=sub.add_parser('monitor');monitor.add_argument('--run',type=Path,required=True)
    health=sub.add_parser('health');health.add_argument('--name')
    args = parser.parse_args(argv)
    try:
        if not args.library.is_file(): raise ValueError('existing project skill library is required')
        library = SkillLibrary(args.library)
        if args.command == 'list': result = {'skills': library.list_all()}
        elif args.command == 'propose':
            proposal = library.propose_from_repeated_success(args.goal)
            result = proposal or {'status': 'INSUFFICIENT_EVIDENCE'}
        elif args.command=='improve':
            tools=ToolRegistry()
            def no_write(*args,**kwargs):raise ToolError('proposal analysis cannot execute tools')
            register_file_tools(tools,args.output.resolve(),no_write)
            def model_factory(directory):
                return (CodexDecisionModel(directory,model=args.model,log_dir=directory/'model-events')
                        if args.provider=='codex' else OpenAICompatibleModel(model=args.model))
            result=propose_improvement(library,args.run,model_factory,tools,args.output)
        elif args.command == 'review':
            path = review_skill(library, args.name, args.suite, args.output, reviewer_id=args.reviewer)
            value = json.loads(path.read_text(encoding='utf-8'))
            result = {'status': value['status'], 'receipt': str(path.resolve())}
        elif args.command=='canary':result=compare_workflows(library,args.name,args.baseline,args.suite,args.baseline_suite,args.output,reviewer_id=args.reviewer,activate=args.activate)
        elif args.command == 'activate': result = activate_reviewed(library, args.name, args.receipt,canary_receipt=args.canary)
        elif args.command == 'disable':
            library.disable(args.name, reason=args.reason); result = library.get(args.name)
        elif args.command == 'restore':
            library.restore(args.name, reason=args.reason); result = library.get(args.name)
        elif args.command == 'rollback': result = library.rollback(args.name, args.version, reason=args.reason)
        elif args.command == 'record-failure':
            result = library.record_failure(args.name, args.receipt, args.sha256, quarantine=args.quarantine)
        elif args.command=='monitor':result=library.monitor_run(args.run)
        elif args.command=='health':result={'observations':library.health_history(args.name)}
        else: result = {'failures': library.failure_history(args.name)}
    except (ValueError, OSError, KeyError, TypeError,ModelAdapterError,CodexHostError) as exc:
        print(json.dumps({'status': 'NEEDS_RECONCILIATION' if getattr(exc,'outcome_unknown',False) else 'REJECTED', 'error': type(exc).__name__ + ': ' + str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 2 if result.get('status') in {'FAIL', 'INSUFFICIENT_EVIDENCE'} else 0


if __name__ == '__main__': raise SystemExit(main())
