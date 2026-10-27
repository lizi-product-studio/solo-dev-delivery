from __future__ import annotations
import copy, hashlib, importlib.util, json, pathlib, sys, traceback

ROOT = pathlib.Path(__file__).parent
TASKS = ROOT / 'tasks'

CASES = {
 '01_normalize_email': [
  (' Alice@Example.COM ', 'alice@example.com'), ('', ''), (' A+B@X.Y ', 'a+b@x.y'),
  ('  User+tag@Sub.Example.ORG  ', 'user+tag@sub.example.org'),
 ],
 '02_sum_amounts': [
  ([{'amount':10},{'name':'x'},{'amount':2.5}], 12.5), ([{'amount':0}],0),
  ([{'amount':1},{'amount':-1}], 'ValueError'), ([{'name':'x'}],0),
 ],
 '03_merge_intervals': [
  ([[1,3],[2,5],[7,8],[8,9]],[[1,5],[7,9]]), ([],[]),
  ([[5,5],[1,2],[2,4]], [[1,5]]), ([[-2,0],[-1,3]], [[-2,3]]),
 ],
 '04_parse_csv': [
  ('name,qty\n"A, B",2\nC,3\n',[{'name':'A, B','qty':'2'},{'name':'C','qty':'3'}]),
  ('a,b\n1,2\n',[{'a':'1','b':'2'}]), ('a,b\n\n1,2\n\n', [{'a':'1','b':'2'}]),
  ('name,note\n"A","hello, world"\n', [{'name':'A','note':'hello, world'}]),
 ],
 '05_inventory_reserve': [
  (({'a':5,'b':1},{'a':2,'b':1}),{'a':3,'b':0}), (({'x':1},{}),{'x':1}),
  (({'a':1},{'a':2}),'ValueError'), (({'a':1},{'b':1}),'KeyError'),
 ],
 '06_expire_sessions': [
  (({'now':'2026-01-10','sessions':[{'id':'b','expires_at':'2026-01-10'},{'id':'a','expires_at':'2026-01-11'}]}),['a']),
  (({'now':'2026-01-10','sessions':[{'id':'c','expires_at':'2027-01-01'},{'id':'a','expires_at':'2027-01-01'}]}),['a','c']),
 ],
 '07_json_patch': [
  (({'doc':{'a':1,'b':2},'ops':[{'op':'replace','path':'/a','value':9}]}),{'a':9,'b':2}),
  (({'doc':{'a':1},'ops':[{'op':'replace','path':'/a','value':None}]}),{'a':None}),
  (({'doc':{'a':1},'ops':[{'op':'replace','path':'/missing','value':2}]}),'KeyError'),
  (({'doc':{'nested':{'a':1}},'ops':[{'op':'replace','path':'/nested','value':{'b':2}}]}),{'nested':{'b':2}}),
 ],
 '08_rate_limit': [
  ({'times':[0,1,2,11],'limit':2,'window':10},[True,True,False,True]),
  ({'times':[0,10],'limit':1,'window':10},[True,True]),
  ({'times':[1,1,1],'limit':1,'window':10},[True,False,False]),
 ],
 '09_topological_sort': [
  ({'nodes':['a','b','c'],'edges':[['a','c'],['b','c']]},['a','b','c']), ({'nodes':[],'edges':[]},[]),
  ({'nodes':['b','a','c'],'edges':[['b','c'],['a','c']]},['a','b','c']),
  ({'nodes':['a','b'],'edges':[['a','b'],['b','a']]},'ValueError'),
 ],
 '10_redact_secrets': [
  ({'password':'x','nested':[{'token':'y','ok':1}]},{'password':'***','nested':[{'token':'***','ok':1}]}),
  ({'secret':None},{'secret':'***'}),
  ({'Password':'x','token':'y','items':[{'secret':'z','keep':2}]},{'Password':'x','token':'***','items':[{'secret':'***','keep':2}]}),
 ],
}


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location('candidate_' + path.stem + '_' + hashlib.sha1(str(path).encode()).hexdigest(), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hash_public(workspace: pathlib.Path):
    names = ['TASK.md', 'CONTRACT.md', 'inputs.json']
    return {name: hashlib.sha256((workspace / name).read_bytes()).hexdigest()
            for name in names if (workspace / name).is_file()}


def evaluate(task_id: str, workspace: pathlib.Path):
    workspace = pathlib.Path(workspace).resolve()
    candidate = workspace / 'src.py'
    before = _hash_public(workspace)
    result = {'task_id': task_id, 'assertions': [], 'quality_score': 0, 'task_success': False,
              'public_inputs_unchanged': True, 'candidate': str(candidate)}
    try:
        module = _load(candidate)
    except Exception as exc:
        result['assertions'].append({'id':'candidate_imports','passed':False,'details':repr(exc)})
        result['passed'] = 0; result['total'] = 1; return result
    passed = 0
    cases = CASES[task_id]
    for index, (inp, expected) in enumerate(cases, 1):
        original = copy.deepcopy(inp)
        observed = None; error = None
        try:
            observed = module.solve(inp)
        except Exception as exc:
            error = type(exc).__name__
        if isinstance(expected, str) and expected in {'ValueError','KeyError'}:
            ok = error == expected
            details = {'expected_error': expected, 'actual_error': error}
        else:
            ok = error is None and observed == expected
            details = {'expected': expected, 'actual': observed, 'error': error}
        if inp != original:
            ok = False; details['input_mutated'] = True
        result['assertions'].append({'id': f'case_{index}', 'passed': bool(ok), 'details': details})
        passed += int(bool(ok))
    after = _hash_public(workspace)
    unchanged = before == after
    result['public_inputs_unchanged'] = unchanged
    result['assertions'].append({'id':'public_inputs_unchanged','passed':unchanged,
                                 'details': {'before':before, 'after':after}})
    passed += int(unchanged)
    result['passed'] = passed
    result['total'] = len(cases) + 1
    result['quality_score'] = round(100 * passed / result['total'], 2)
    result['task_success'] = passed == result['total']
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('task_id', choices=sorted(CASES))
    parser.add_argument('workspace', type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    result = evaluate(args.task_id, args.workspace)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['task_success'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
