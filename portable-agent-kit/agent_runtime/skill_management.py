"""Host-managed recipe review using real files, holdouts and mutations.

The host owns the suite and its oracles. This verifies their concrete assertions,
not the host's identity or the sufficiency of unknown business policy.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from control_plane.evaluator import verify_evidence
from .file_tools import register_file_tools
from .file_tools import _patch
from .intake import checked_path, relative_path
from .regression import regress_recipe
from .tools import ToolRegistry, ToolError


FORMAT = 'skill-artifact-suite/1'
MAX_SUITE_BYTES = 1024 * 1024
ASSERTIONS = {'file_sha256', 'text_contains', 'json_pointer_equals'}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def _load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SUITE_BYTES:
        raise ValueError('suite must be an unlinked JSON file of at most 1 MiB')
    raw = path.read_bytes()
    value = json.loads(raw.decode('utf-8-sig'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('non-finite suite number')))
    return raw, value


def _case_fingerprint(case):
    # IDs and the order of conjunctive assertions/output names are not new
    # holdout content. Array order inside actual arguments remains meaningful.
    assertions = [{**row, 'path': os.path.normcase(row['path'])} for row in case['assertions']]
    canonical = lambda x: json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)
    payload = {'inputs': {os.path.normcase(k): v for k,v in case['inputs'].items()},
               'initial_outputs': {os.path.normcase(k): v for k,v in case.get('initial_outputs',{}).items()},
               'scope': sorted(os.path.normcase(p) for p in case['scope']),
               'arguments': case['arguments'], 'assertions': sorted(assertions,key=canonical)}
    return hashlib.sha256(canonical(payload).encode('utf-8')).hexdigest()


def _different_json_value(value):
    # Preserve the JSON type where possible so a syntax/type-only checker is
    # insufficient. Null necessarily changes to a non-null JSON value.
    if value is None:return False
    if isinstance(value,bool):return not value
    if isinstance(value,int):return value+1
    if isinstance(value,float):return 1.0 if value==0 else 0.0
    if isinstance(value,str):return value+'\u241fsemantic-mutant'
    if isinstance(value,list):return value+[{'__agi_like_mutation__':True}]
    if isinstance(value,dict):
        result=deepcopy(value);key='__agi_like_mutation__'
        while key in result:
            key = '{}_'.format(key)
        result[key]=True
        return result
    raise ValueError('oracle target must be a JSON value')


def validate_suite(value):
    if not isinstance(value, dict) or set(value) != {'format', 'cases'} or value['format'] != FORMAT:
        raise ValueError('suite must use skill-artifact-suite/1 with cases')
    if not isinstance(value['cases'], list) or not 2 <= len(value['cases']) <= 32:
        raise ValueError('suite needs 2..32 independent holdout cases')
    result = deepcopy(value)
    seen = set(); fingerprints = set()
    for case in result['cases']:
        fields = {'id', 'inputs', 'scope', 'arguments', 'assertions'}
        if not isinstance(case, dict) or not fields<=set(case) or set(case)-fields-{'initial_outputs'}:
            raise ValueError('case needs id, inputs, scope, arguments and assertions; initial_outputs is optional')
        cid = case['id']
        if not isinstance(cid, str) or not cid.strip() or len(cid) > 256 or cid in seen:
            raise ValueError('holdout case IDs must be distinct nonempty strings')
        seen.add(cid)
        if not isinstance(case['arguments'], dict) or not isinstance(case['inputs'], dict) or len(case['inputs']) > 32:
            raise ValueError('case arguments/inputs must be bounded objects')
        input_names = [relative_path(k) for k in case['inputs']]
        if len({os.path.normcase(p) for p in input_names}) != len(input_names):
            raise ValueError('fixture input aliases must be distinct')
        case['inputs'] = dict(zip(input_names,case['inputs'].values()))
        if any(not isinstance(v, str) or len(v.encode('utf-8')) > 65536 for v in case['inputs'].values()):
            raise ValueError('fixture inputs must be UTF-8 strings of at most 64 KiB')
        if not isinstance(case['scope'], list) or not 1 <= len(case['scope']) <= 32:
            raise ValueError('case scope must contain 1..32 output files')
        case['scope'] = [relative_path(p) for p in case['scope']]
        # Files are physically separate even on case-insensitive Windows.
        names = [os.path.normcase(p) for p in list(case['inputs']) + case['scope']]
        if len(set(names)) != len(names):
            raise ValueError('fixture inputs and outputs must be distinct')
        initial=case.get('initial_outputs',{})
        if not isinstance(initial,dict) or len(initial)>32:
            raise ValueError('initial_outputs must be a bounded object')
        initial_names=[relative_path(k) for k in initial]
        if len({os.path.normcase(p) for p in initial_names})!=len(initial_names):
            raise ValueError('initial output aliases must be distinct')
        case['initial_outputs']=dict(zip(initial_names,initial.values()))
        if any(not isinstance(v,str) or len(v.encode('utf-8'))>65536 for v in initial.values()):
            raise ValueError('initial outputs must be UTF-8 strings of at most 64 KiB')
        if (not set(initial_names)<=set(case['scope'])
                or {os.path.normcase(p) for p in initial_names}&{os.path.normcase(p) for p in case['inputs']}):
            raise ValueError('initial_outputs must remain inside output scope and cannot overwrite readonly inputs')
        assertions = case['assertions']
        if not isinstance(assertions, list) or not 1 <= len(assertions) <= 128:
            raise ValueError('case needs bounded concrete artifact assertions')
        covered = set()
        for assertion in assertions:
            if not isinstance(assertion, dict) or assertion.get('type') not in ASSERTIONS:
                raise ValueError('assertions must inspect file hash, text or JSON content')
            assertion['path'] = relative_path(assertion.get('path'))
            if assertion['path'] not in case['scope']:
                raise ValueError('assertion must inspect an authorized output')
            covered.add(assertion['path'])
        if covered != set(case['scope']):
            raise ValueError('every output requires an independent content assertion')
        fingerprint = _case_fingerprint(case)
        if fingerprint in fingerprints:
            raise ValueError('holdout content must be distinct; renamed copies are not independent cases')
        fingerprints.add(fingerprint)
    return result


def _check_assertions(workspace, assertions, reviewer_id, owner):
    observations = []
    for number, assertion in enumerate(assertions):
        path = checked_path(workspace, assertion['path'])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        check = {'id': 'artifact-' + str(number), 'expected': 'host-specified content assertion',
                 'environment': 'isolated', 'assertion': assertion}
        reference = {'id': 'readback-' + str(number), 'check_id': check['id'], 'uri': assertion['path'],
                     'sha256': digest, 'environment': 'isolated', 'verdict': 'PASS'}
        observations.append(verify_evidence(reference, [check], workspace, reviewer_id=reviewer_id, owner=owner))
    return observations


def review_skill(library, name, suite_path, output, *, reviewer_id):
    raw, value = _load(suite_path)
    suite = validate_suite(value)
    skill = library.get(name)
    if skill['status'] != 'CANDIDATE':
        raise ValueError('review a candidate version; disable/rollback use their own explicit operations')
    if not isinstance(reviewer_id, str) or not reviewer_id.strip() or reviewer_id == skill['owner']:
        raise ValueError('reviewer must differ from the skill owner')
    if set(case['id'] for case in suite['cases']) & set(skill['spec'].get('observed_tasks', [])):
        raise ValueError('holdout case must not be a training task')
    root = Path(output).absolute()
    if root.exists() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('review output must be a new unlinked directory')
    workspaces = {};initial_hashes={}

    def factory(case):
        # regress_recipe creates the receipt root before requesting tools.
        workspace = root / ('workspace-%03d' % suite['cases'].index(case))
        workspace.mkdir()
        workspaces[case['id']] = workspace
        for path, text in case['inputs'].items():
            target = checked_path(workspace, path); target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode('utf-8'))
        initial_hashes[case['id']]=[]
        for path,text in case['initial_outputs'].items():
            target=checked_path(workspace,path);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(text.encode('utf-8'))
            initial_hashes[case['id']].append({'path':path,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
        def write_file(path, text):
            normalized = relative_path(path)
            if normalized not in case['scope']:
                raise ToolError('write is outside the holdout case scope')
            target = checked_path(workspace, normalized)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(str(text).encode('utf-8'))
            return {'path': normalized, 'written': True}
        tools = ToolRegistry(); register_file_tools(tools, workspace, write_file,policy_inputs=list(case['inputs']))
        return tools

    def verify(case, actual):
        workspace = workspaces[case['id']]
        for path, text in case['inputs'].items():
            if checked_path(workspace, path).read_bytes() != text.encode('utf-8'):
                raise ValueError('holdout immutable input changed')
        checks = _check_assertions(workspace, case['assertions'], reviewer_id, skill['owner'])
        mutants = []
        for index,assertion in enumerate(case['assertions']):
            name=assertion['path'];target = checked_path(workspace, name); original = target.read_bytes()
            try:
                kind=assertion['type']
                if kind=='json_pointer_equals':
                    value=json.loads(original.decode('utf-8'))
                    # The good candidate already equals the fixed oracle.
                    # Change only its target value, preserving other fields.
                    replacement=_different_json_value(assertion['value'])
                    mutant=_patch(value,[{'op':'replace','path':assertion['pointer'],'value':replacement}])
                    payload=_dump(mutant).encode('utf-8')
                    evidence={'kind':'json-value','pointer':assertion['pointer'],
                              'replacement':replacement,'valid_json':True,'valid_utf8':True}
                elif kind=='text_contains':
                    text=original.decode('utf-8');needle=assertion['text'].strip()
                    if not needle:raise ValueError('text mutation requires a nonempty oracle fragment')
                    while needle in text:text=text.replace(needle,'')
                    payload=text.encode('utf-8')
                    evidence={'kind':'required-text-removed','removed_fragment':needle,'valid_utf8':True}
                else:
                    payload=(original.decode('utf-8')+'\n').encode('utf-8')
                    evidence={'kind':'utf8-bytes-changed','valid_utf8':True}
                target.write_bytes(payload)
                mutant_path=root/'mutations'/('case-%03d-check-%03d'% (suite['cases'].index(case),index)+('.json' if kind=='json_pointer_equals' else '.txt'))
                mutant_path.parent.mkdir(exist_ok=True);mutant_path.write_bytes(payload)
                rejected=False;error=None
                # Check this exact oracle, not another assertion that might
                # reject a format error or an unrelated field instead.
                try: _check_assertions(workspace,[assertion],reviewer_id,skill['owner'])
                except (OSError, UnicodeError, ValueError, KeyError) as exc:
                    rejected=True;error=type(exc).__name__+': '+str(exc)
                mutants.append({'path':name,'assertion_index':index,'rejected':rejected,'rejection_error':error,
                    **evidence,'evidence_ref':{'uri':mutant_path.relative_to(root).as_posix(),
                        'sha256':hashlib.sha256(payload).hexdigest()}})
            finally:
                target.write_bytes(original)
        return {'status': 'PASS' if all(m['rejected'] for m in mutants) else 'FAIL',
                'readbacks': checks, 'mutations': mutants,'case_fingerprint':_case_fingerprint(case),
                'mutation_protocol':'content-mutations/1','initial_output_hashes':initial_hashes[case['id']]}

    receipt_path = regress_recipe(library, name, suite['cases'], factory, root,
                                  reviewer_id=reviewer_id, verify_case=verify)
    (root / 'suite.json').write_bytes(raw)
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    receipt.update(suite_kind='file-artifact-holdout/1', suite_sha256=hashlib.sha256(raw).hexdigest(),
                   limits='Host-authored content assertions and valid-content mutation sensitivity only; not proof of complete business coverage or independent identity.')
    for index, check in enumerate(receipt['checks']):
        check['evidence_refs'].append({'uri': 'suite.json', 'sha256': receipt['suite_sha256']})
        case = suite['cases'][index]
        result=json.loads((root/('case-%03d.json'%index)).read_text(encoding='utf-8'))
        for mutation in (result.get('acceptance') or {}).get('mutations',[]):
            check['evidence_refs'].append(mutation['evidence_ref'])
        for name in case['scope']:
            path = workspaces[case['id']] / name if case['id'] in workspaces else None
            if path is not None and path.is_file():
                check['evidence_refs'].append({'uri': path.relative_to(root).as_posix(),
                                               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    receipt_path.write_text(_dump(receipt), encoding='utf-8')
    return receipt_path


def activate_reviewed(library, name, receipt_path,*,canary_receipt=None):
    path = Path(receipt_path)
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if value.get('suite_kind') != 'file-artifact-holdout/1' or value.get('status') != 'PASS':
        raise ValueError('managed activation requires a passing artifact holdout review')
    cases = [];fingerprints=[]
    for check in value.get('checks', []):
        refs = check.get('evidence_refs', [])
        library._verify_evidence(refs,path.parent)
        case_ref = next((r for r in refs if r.get('uri', '').startswith('case-') and r['uri'].endswith('.json')), None)
        if not case_ref: raise ValueError('managed review needs actual case results')
        result = json.loads((path.parent / case_ref['uri']).read_text(encoding='utf-8'))
        acceptance = result.get('acceptance') or {}
        if (result.get('status') != 'PASS' or acceptance.get('status') != 'PASS'
                or not acceptance.get('readbacks') or not acceptance.get('mutations')
                or acceptance.get('mutation_protocol')!='content-mutations/1'
                or any(m.get('rejected') is not True or m.get('valid_utf8') is not True
                       or m.get('kind') not in {'json-value','required-text-removed','utf8-bytes-changed'}
                       or (m.get('kind')=='json-value' and m.get('valid_json') is not True)
                       for m in acceptance['mutations'])):
            raise ValueError('managed review must pass readbacks and actual mutations')
        cases.append(result['id'])
        fingerprints.append(acceptance.get('case_fingerprint'))
    if len(set(cases))<2 or None in fingerprints or len(set(fingerprints))!=len(fingerprints):
        raise ValueError('managed activation requires distinct holdout IDs and content')
    return library.activate(name, path,canary_receipt=canary_receipt)
