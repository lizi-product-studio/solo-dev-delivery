"""Compare workflows on identical host-owned workloads before activation."""
import hashlib
import json
from pathlib import Path

from .skills import SkillLibrary
from .skill_management import review_skill,validate_suite,_case_fingerprint


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _workloads(suite):
    return {case['id']:_case_fingerprint({**case,'arguments':{}}) for case in validate_suite(_load(suite))['cases']}


def verify_canary(path,candidate,regression_path):
    path=Path(path).resolve();root=path.parent;value=_load(path)
    if value.get('format')!='skill-improvement-canary/1' or value.get('status')!='PASS':raise ValueError('passing canary evidence is required')
    if value.get('candidate')!={k:candidate[k] for k in ('name','version','spec_sha256')}:raise ValueError('canary candidate binding changed')
    counts={}
    suites={}
    for role in ('baseline','candidate'):
        reference=value[role+'_suite'];target=(root/reference['uri']).resolve()
        if not target.is_relative_to(root) or _sha(target)!=reference['sha256']:raise ValueError('canary suite changed')
        suites[role]=validate_suite(_load(target))
    for role in ('baseline','candidate'):
        reference=value[role+'_review'];target=(root/reference['uri']).resolve()
        if not target.is_relative_to(root) or target.is_symlink() or _sha(target)!=reference['sha256']:raise ValueError('canary review evidence changed')
        receipt=_load(target)
        expected=value[role]
        if receipt.get('skill')!=expected['name'] or receipt.get('spec_sha256')!=expected['spec_sha256'] or receipt.get('suite_sha256')!=value[role+'_suite']['sha256']:
            raise ValueError('review does not bind the declared workflow and suite')
        if {c['id'] for c in receipt['checks']}!={c['id'] for c in suites[role]['cases']}:raise ValueError('review case set differs from canary workloads')
        for check in receipt['checks']:
            SkillLibrary._verify_evidence(check['evidence_refs'],target.parent)
            evidence=next((ref for ref in check['evidence_refs'] if ref['uri'].startswith('case-') and ref['uri'].endswith('.json')),None)
            if evidence is None:raise ValueError('missing actual canary case result')
            actual=_load(target.parent/evidence['uri'])
            if actual['id']!=check['id'] or actual['status']!=check['status']:raise ValueError('canary case verdict differs from actual result')
        counts[role]=sum(check['status']!='PASS' for check in receipt['checks'])
        if role=='candidate' and (target!=Path(regression_path).resolve() or receipt['status']!='PASS'):
            raise ValueError('activation must use the actually evaluated candidate receipt')
    baseline_work=_workloads(root/value['baseline_suite']['uri']);candidate_work=_workloads(root/value['candidate_suite']['uri'])
    if baseline_work!=candidate_work or len(candidate_work)<2 or counts['candidate']!=0 or counts['baseline']<=counts['candidate']:
        raise ValueError('canary lacks independently observed improvement on identical workloads')
    return value


def compare_workflows(library,candidate_name,baseline_name,candidate_suite,baseline_suite,output,*,reviewer_id,activate=False):
    if _workloads(candidate_suite)!=_workloads(baseline_suite):raise ValueError('baseline and candidate must use identical inputs, initial outputs and assertions')
    candidate=library.get(candidate_name);baseline=library.get(baseline_name)
    executable=lambda row:json.dumps({key:row['spec'].get(key) for key in ('format','parameters','steps')},sort_keys=True)
    if executable(candidate)==executable(baseline):raise ValueError('identical executable workflows cannot demonstrate an improvement')
    root=Path(output);root.mkdir(parents=True,exist_ok=False)
    (root/'candidate-suite.json').write_bytes(Path(candidate_suite).read_bytes())
    (root/'baseline-suite.json').write_bytes(Path(baseline_suite).read_bytes())
    # Evaluating a live baseline must not disable or rewrite its project version.
    shadow=SkillLibrary(root/'baseline-copy.sqlite3')
    shadow.register(baseline_name,baseline['description'],baseline['spec'],owner=baseline['owner'])
    before=review_skill(shadow,baseline_name,root/'baseline-suite.json',root/'baseline',reviewer_id=reviewer_id)
    after=review_skill(library,candidate_name,root/'candidate-suite.json',root/'candidate',reviewer_id=reviewer_id)
    bad_before=sum(c['status']!='PASS' for c in _load(before)['checks'])
    bad_after=sum(c['status']!='PASS' for c in _load(after)['checks'])
    result={'format':'skill-improvement-canary/1','status':'PASS' if bad_before>bad_after and bad_after==0 else 'FAIL',
            'candidate':{k:candidate[k] for k in ('name','version','spec_sha256')},
            'baseline':{k:baseline[k] for k in ('name','version','spec_sha256')},
            'baseline_failures':bad_before,'candidate_failures':bad_after,
            'limits':'Host-authored bounded workload comparison; not evidence of universal learning or model superiority.'}
    for role,receipt,suite in (('baseline',before,root/'baseline-suite.json'),('candidate',after,root/'candidate-suite.json')):
        result[role+'_review']={'uri':receipt.relative_to(root).as_posix(),'sha256':_sha(receipt)}
        result[role+'_suite']={'uri':suite.name,'sha256':_sha(suite)}
    report=root/'canary.json';report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for original in (candidate,baseline):
        now=library.get(original['name'])
        if any(now[k]!=original[k] for k in ('version','spec_sha256','status')):raise ValueError('skill changed during canary')
    if result['status']=='PASS':
        verify_canary(report,candidate,after)
        if activate:
            from .skill_management import activate_reviewed
            activate_reviewed(library,candidate_name,after,canary_receipt=report)
    return {'status':result['status'],'receipt':str(report.resolve()),'activated':bool(activate and result['status']=='PASS')}
