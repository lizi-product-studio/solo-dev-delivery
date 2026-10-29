"""Host-owned holdout runner for parameterized recipes.

Cases and oracles belong to the reviewer, not to the learning model. This tests
the recipe's observable behavior; it cannot certify the reviewer's identity.
"""
import hashlib
import json
from pathlib import Path

from .recipes import execute_recipe


def regress_recipe(library, name, cases, tool_factory, output, *, reviewer_id):
    skill=library.get(name)
    if not reviewer_id or reviewer_id==skill['owner']:
        raise ValueError('holdout reviewer must differ from skill owner')
    if not isinstance(cases,list) or len(cases)<2:
        raise ValueError('at least two holdout cases required')
    ids=[case.get('id') for case in cases]
    if (any(not isinstance(cid,str) or not cid for cid in ids) or len(set(ids))!=len(ids)
            or set(ids)&set(skill['spec'].get('observed_tasks',[]))):
        raise ValueError('holdout IDs must be unique and disjoint from source tasks')
    root=Path(output)
    root.mkdir(parents=True,exist_ok=False)
    checks=[]
    for index,case in enumerate(cases):
        actual=None
        try:
            tools=tool_factory(case)
            actual=execute_recipe(skill['spec'],case['arguments'],tools)
            # Canonical JSON compares type as well as value (True != 1).
            expected=case['expected']
            dump=lambda x:json.dumps(x,sort_keys=True,allow_nan=False,ensure_ascii=False)
            status='PASS' if dump(actual)==dump(expected) else 'FAIL'
            message=None
        except Exception as exc:
            status='FAIL';message=type(exc).__name__+': '+str(exc)
        result={'id':case['id'],'status':status,'arguments':case.get('arguments'),
                'expected':case.get('expected'),'actual':actual,'error':message}
        path=root/('case-%03d.json'%index)
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        checks.append({'id':case['id'],'status':status,'evidence_refs':[{
            'uri':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]})
    receipt={'format':'skill-regression/1','status':'PASS' if all(c['status']=='PASS' for c in checks) else 'FAIL',
             'skill':name,'version':skill['version'],'spec_sha256':skill['spec_sha256'],
             'owner':skill['owner'],'reviewer_id':reviewer_id,'checks':checks,'suite_kind':'holdout'}
    path=root/'receipt.json'
    path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return path
