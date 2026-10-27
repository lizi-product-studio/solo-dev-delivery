"""Post-run evidence checks; does not alter candidates or initial receipts."""
import hashlib
import json
from pathlib import Path
import subprocess
from runtime import ROOT, NEUTRAL_ROOT, REAL_HOME, REPO, fingerprint, save

def main():
    plan=json.loads((ROOT/'registration.json').read_text(encoding='utf-8'))
    frozen={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in plan['frozen_files'].items()}
    sources={'solo':fingerprint(ROOT/'sources'/'solo')==plan['solo_files'],
             'superpowers':fingerprint(ROOT/'sources'/'superpowers'/'skills')==plan['superpowers_files']}
    installed=REAL_HOME/'.agents'/'skills'/'solo-dev-delivery'
    installation={p:(installed/p).is_file() and hashlib.sha256((installed/p).read_bytes()).hexdigest()==h
                  for p,h in plan['solo_files'].items()}
    runs=[]
    for p in sorted((ROOT/'runs').glob('*/receipt.json')):
        raw=json.loads(p.read_text(encoding='utf-8'))
        workspace=Path(raw['workspace']) if raw.get('workspace') else None
        lines=[]; worktrees=[]
        if workspace:
            cp=subprocess.run(['git','-C',str(workspace),'worktree','list','--porcelain'],capture_output=True,
                              text=True,encoding='utf-8',timeout=20)
            lines=cp.stdout.splitlines()
            worktrees=[line[9:] for line in lines if line.startswith('worktree ')]
        rounds=[]
        for q in sorted(p.parent.glob('turn-*.evaluation.json')):
            ev=json.loads(q.read_text(encoding='utf-8'))
            rounds.append({'file':q.name,'selected':ev.get('selected'),
                           'selection_unknown':bool(ev.get('selection_unknown')),
                           'other_worktree_count':ev.get('other_worktree_count'),
                           'business_complete':ev['result']['completed']})
        runs.append({'id':raw['id'],'ended':bool(raw.get('ended_utc')),'rounds':rounds,
                     'worktree_count_at_final_audit':len(worktrees),
                     'selection_requires_adjudication':any(r['selection_unknown'] for r in rounds)
                        or any(r['selected'] not in ('root',) for r in rounds)})
    changed=subprocess.check_output(['git','-C',str(REPO),'diff','--name-only',plan['solo_commit'],'HEAD'],text=True,encoding='utf-8').splitlines()
    final={'frozen_control_files':frozen,'source_trees':sources,'installed_files_match':installation,
           'owned_auth_files_remaining':[str(p) for p in NEUTRAL_ROOT.rglob('auth.json')],
           'all_finished':len(runs)==18 and all(r['ended'] for r in runs),
           'only_evaluation_files_committed':all(p.startswith('evaluations/skill-comparison-2026-10-23/') for p in changed),
           'runs':runs,'path_adjudication_required':any(r['selection_requires_adjudication'] for r in runs),
           'path_rule':'If every first/final submission is accepted from root and only the root worktree exists, known prefix/sibling selector boundaries did not affect the results. Otherwise preserve originals and independently resolve exact declared owned paths; never select by highest score.'}
    save(ROOT/'integrity.json',final)
    save(ROOT/'path-adjudication.json',{'required':final['path_adjudication_required'],'runs':runs,
        'initial_results_preserved':True,'candidate_changes':False,'model_retries':False,'rule':final['path_rule']})
    print(json.dumps({'all_finished':final['all_finished'],'frozen_all_match':all(frozen.values()),
                      'sources_match':all(sources.values()),'installed_all_match':all(installation.values()),
                      'remaining_auth_files':len(final['owned_auth_files_remaining']),
                      'path_adjudication_required':final['path_adjudication_required']}))

if __name__=='__main__':main()
