"""Independent local package exercise; no model, host hooks or global state."""
import argparse
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--package', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(args.package / 'scripts'))
import challenge

checks_source = '''import unittest, app
class Business(unittest.TestCase): pass
def make(value):
 def test(self): self.assertEqual(app.increment(value), value + 1)
 return test
for value in range(1000):
 method=make(value)
 if SKIP and value==999: method=unittest.skip('declared missing observation')(method)
 setattr(Business,'test_increment_with_real_representative_integer_input_%04d'%value,method)
'''
results = []
for mode, expected in [('pass', 'CHALLENGE_PASS'), ('fail', 'FAIL'), ('skip', 'UNKNOWN'), ('zero', 'UNKNOWN'), ('early_exit', 'UNKNOWN'), ('noisy', 'CHALLENGE_PASS')]:
    root = args.output / mode
    work = root / 'candidate'
    work.mkdir(parents=True)
    (work / 'REQUEST.md').write_text('Adding one to an integer returns its successor.\n', encoding='utf-8')
    code = 'def increment(value): return value + 1\n'
    if mode == 'fail': code = 'def increment(value): return -1 if value == 999 else value + 1\n'
    (work / 'app.py').write_text(code, encoding='utf-8')
    test = root / 'checks.py'
    source = 'SKIP=' + repr(mode == 'skip') + '\n' + checks_source
    if mode == 'noisy': source = 'import sys\nprint("x"*200000)\nprint("y"*200000,file=sys.stderr)\n' + source
    if mode == 'zero': source = 'import unittest\nclass Business(unittest.TestCase): pass\n'
    if mode == 'early_exit': source = 'import os\nos._exit(0)\n'
    test.write_text(source, encoding='utf-8')
    receipt = root / 'receipt.json'
    summary = challenge.verify(work, ['REQUEST.md'], ['app.py'], test, receipt)
    raw = json.loads(receipt.read_text(encoding='utf-8'))
    count = 0 if mode in ('zero', 'early_exit') else 1000
    observed = {'mode': mode, 'status': summary['status'], 'expected': expected,
                'exit_code': raw['exit_code'], 'case_count': len(raw['test_cases']),
                'executed_tests': summary['executed_tests'], 'stdout_bytes': raw['stdout']['bytes'],
                'stderr_bytes': raw['stderr']['bytes'], 'stdout_truncated': raw['stdout']['truncated'],
                'stderr_truncated': raw['stderr']['truncated'],
                'pass': summary['status'] == expected and len(raw['test_cases']) == count}
    if mode == 'pass':
        try:
            reuse = challenge.verify(work, ['REQUEST.md'], ['app.py'], test, receipt)
            observed['reused'] = reuse['reused']
            observed['reuse_executed_tests'] = reuse['executed_tests']
            observed['pass'] &= reuse['reused'] and reuse['executed_tests'] == 0
        except ValueError as error:
            observed['reuse_error'] = str(error)
            observed['pass'] = False
        (work / 'app.py').write_text('def increment(value): return value + 2\n', encoding='utf-8')
        observed['after_edit_status'] = challenge.status(receipt)['status']
        observed['pass'] &= observed['after_edit_status'] == 'STALE'
    results.append(observed)
    print(json.dumps(observed), flush=True)
(args.output / 'summary.json').write_text(json.dumps({'cases': results, 'pass': all(x['pass'] for x in results)}, indent=2) + '\n', encoding='utf-8')
sys.exit(0 if all(x['pass'] for x in results) else 1)
