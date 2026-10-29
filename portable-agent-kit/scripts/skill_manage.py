#!/usr/bin/env python3
"""Propose, independently review, activate and recover parameterized skills."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_runtime.skills import SkillLibrary
from agent_runtime.skill_management import review_skill, activate_reviewed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    propose = sub.add_parser('propose'); propose.add_argument('--goal', required=True)
    review = sub.add_parser('review'); review.add_argument('--name', required=True)
    review.add_argument('--suite', type=Path, required=True); review.add_argument('--output', type=Path, required=True)
    review.add_argument('--reviewer', required=True)
    activate = sub.add_parser('activate'); activate.add_argument('--name', required=True)
    activate.add_argument('--receipt', type=Path, required=True)
    for operation in ('disable', 'restore', 'rollback'):
        item = sub.add_parser(operation); item.add_argument('--name', required=True); item.add_argument('--reason', required=True)
        if operation == 'rollback': item.add_argument('--version', type=int, required=True)
    failure = sub.add_parser('record-failure'); failure.add_argument('--name', required=True)
    failure.add_argument('--run-receipt','--receipt',dest='receipt',type=Path,required=True); failure.add_argument('--sha256', required=True)
    failure.add_argument('--quarantine', action='store_true')
    history = sub.add_parser('failures'); history.add_argument('--name')
    args = parser.parse_args(argv)
    try:
        if not args.library.is_file(): raise ValueError('existing project skill library is required')
        library = SkillLibrary(args.library)
        if args.command == 'list': result = {'skills': library.list_all()}
        elif args.command == 'propose':
            proposal = library.propose_from_repeated_success(args.goal)
            result = proposal or {'status': 'INSUFFICIENT_EVIDENCE'}
        elif args.command == 'review':
            path = review_skill(library, args.name, args.suite, args.output, reviewer_id=args.reviewer)
            value = json.loads(path.read_text(encoding='utf-8'))
            result = {'status': value['status'], 'receipt': str(path.resolve())}
        elif args.command == 'activate': result = activate_reviewed(library, args.name, args.receipt)
        elif args.command == 'disable':
            library.disable(args.name, reason=args.reason); result = library.get(args.name)
        elif args.command == 'restore':
            library.restore(args.name, reason=args.reason); result = library.get(args.name)
        elif args.command == 'rollback': result = library.rollback(args.name, args.version, reason=args.reason)
        elif args.command == 'record-failure':
            result = library.record_failure(args.name, args.receipt, args.sha256, quarantine=args.quarantine)
        else: result = {'failures': library.failure_history(args.name)}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'REJECTED', 'error': type(exc).__name__ + ': ' + str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 2 if result.get('status') in {'FAIL', 'INSUFFICIENT_EVIDENCE'} else 0


if __name__ == '__main__': raise SystemExit(main())
