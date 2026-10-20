import sys
from pathlib import Path


def assess(q,t,actor,proof):
    p=q.reviews.plan(t['id'])
    return q.reviews.assess(t['id'],actor,{'revision':p['revision'],'contract_hash':t['contract_hash'],
        'risk_reasoning':'Test fixture scope examined; no production or external effects.',
        'scope_reasoning':'Only declared local fixture files.', 'acknowledged_checks':list(p['checks']),
        'unresolved':[],'evidence':str(proof)})


def bind_all(q,t,actor,module='test_probe.py'):
    p=q.reviews.plan(t['id'])
    return q.reviews.bind(t['id'],actor,{'revision':p['revision'],'local_only':True,'checks':{
        cid:{'argv':[sys.executable,'-m','unittest','-v',module],'adapter':'unittest','environment':c['environment'],
            'rationale':'Run the reviewed synthetic counterexample suite for this declared obligation.','timeout':20}
        for cid,c in p['checks'].items()}})


def review_data(q,t,proof,commit):
    p=q.reviews.plan(t['id'])
    return {'revision':p['revision'],'contract_hash':t['contract_hash'],'commit':commit,'verdict':'PASS',
        'observations':{cid:'Observed actual isolated test results for '+cid for cid in p['checks']},
        'uncovered':[],'resolved_findings':[fid for fid,f in p['findings'].items() if f['status']=='OPEN'],
        'evidence':str(proof),'independence_evidence':str(proof)}
