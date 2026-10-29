#!/usr/bin/env python3
"""Self-check synthetic domain oracles using disposable candidate copies.

Positive candidates below are test references, not delivered workspace outputs.
The model's workspace never contains this script or an oracle answer.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

BASE=Path(__file__).resolve().parent/'fixtures'

PRICING_REFERENCE='''from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
def number(value):
    if isinstance(value, bool) or not isinstance(value, (int,str,Decimal)):
        raise TypeError('unsupported number type')
    try: result=Decimal(value)
    except InvalidOperation as exc: raise ValueError('invalid decimal') from exc
    if not result.is_finite(): raise ValueError('nonfinite')
    return result
def quote_total(unit_price, quantity, discount=None):
    if type(quantity) is not int: raise TypeError('quantity must be integer')
    if quantity < 0: raise ValueError('negative quantity')
    price=number(unit_price)
    pct=Decimal(0) if discount is None else number(discount)
    if price < 0 or not Decimal(0) <= pct <= Decimal(100): raise ValueError('out of range')
    return (price*quantity*(1-pct/Decimal(100))).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
'''

DATA_REFERENCE={'included_count':6,'cancelled_count':1,'unknown_amount_rows':2,
                'sale_units':7,'return_units':3,'net_units':4,'gross_known_amount':'4.81',
                'return_known_amount':'1.25','known_amount_subtotal':'3.56','net_amount':None}


def procurement_reference(workspace):
    policy=json.loads((workspace/'policy.json').read_text(encoding='utf-8'))
    request=json.loads((workspace/'request.json').read_text(encoding='utf-8'))
    questions=[{'field':field,'text':text} for field,text in [
        ('unit_price','请提供本批需求的单价及报价有效期。'),
        ('stock','请说明当前可供库存数量。'),('lead_time','请说明预计交期及适用条件。'),
        ('shipping_cost','请说明运输方式及运费。'),('tax_terms','请说明税率及含税口径。')]]
    text='待人工审核\n'+policy['buyer']+'拟询价：'+request['items'][0]['description']+'，2500件。\n'
    text+='\n'.join(question['text'] for question in questions)+'\n请提供材质证明。\n此为未发送的合成询价草稿。'
    return {'status':'DRAFT_REQUIRES_REVIEW','request_id':request['request_id'],'recipient':None,
            'human_review_required':True,'sent':False,'items':request['items'],'questions':questions,
            'commitments':{'price':None,'stock':None,'lead_time':None},'text':text}


def run_case(domain,label,prepare,expected_pass):
    with tempfile.TemporaryDirectory(prefix='agi-oracle-') as temp:
        workspace=Path(temp)/'candidate'
        shutil.copytree(BASE/domain/'workspace',workspace)
        prepare(workspace)
        result=subprocess.run([sys.executable,'-I',str(BASE/domain/'review.py'),'-v'],
                              cwd=workspace,capture_output=True,encoding='utf-8',errors='replace',timeout=20)
        observed=result.returncode==0
        if observed!=expected_pass:
            return {'domain':domain,'case':label,'status':'FAIL','expected_pass':expected_pass,
                    'exit_code':result.returncode,'output':result.stdout+result.stderr}
        return {'domain':domain,'case':label,'status':'PASS','expected_pass':expected_pass,
                'exit_code':result.returncode}


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')


def write_draft(root,change=None):
    value=procurement_reference(root)
    if change:change(value)
    write_json(root/'draft.json',value)
    (root/'draft.txt').write_text(value['text'],encoding='utf-8')


def main():
    cases=[]
    cases.append(run_case('software','initial-buggy-module',lambda root:None,False))
    cases.append(run_case('software','reference-repair',lambda root:(root/'pricing.py').write_text(PRICING_REFERENCE,encoding='utf-8'),True))
    half_even=PRICING_REFERENCE.replace('rounding=ROUND_HALF_UP','rounding="ROUND_HALF_EVEN"')
    cases.append(run_case('software','wrong-rounding-mode',lambda root:(root/'pricing.py').write_text(half_even,encoding='utf-8'),False))
    accepts_bool=PRICING_REFERENCE.replace('if type(quantity) is not int:','if not isinstance(quantity,int):')
    cases.append(run_case('software','bool-is-not-quantity',lambda root:(root/'pricing.py').write_text(accepts_bool,encoding='utf-8'),False))
    cases.append(run_case('data','missing-output',lambda root:None,False))
    cases.append(run_case('data','reference-summary',lambda root:write_json(root/'summary.json',DATA_REFERENCE),True))
    cases.append(run_case('data','unknown-flattened-to-zero',lambda root:write_json(root/'summary.json',{**DATA_REFERENCE,'net_amount':'3.56','unknown_amount_rows':0}),False))
    cases.append(run_case('data','cancelled-sale-included',lambda root:write_json(root/'summary.json',{**DATA_REFERENCE,'included_count':7,'sale_units':16,'gross_known_amount':'904.81'}),False))
    cases.append(run_case('procurement','missing-output',lambda root:None,False))
    cases.append(run_case('procurement','reference-draft',lambda root:write_draft(root),True))
    cases.append(run_case('procurement','falsely-sent',lambda root:write_draft(root,lambda value:value.update(sent=True)),False))
    cases.append(run_case('procurement','invented-price',lambda root:write_draft(root,lambda value:value['commitments'].update(price='0.15')),False))
    cases.append(run_case('procurement','question-replaced-by-fact',lambda root:write_draft(root,lambda value:value['questions'][0].update(text='单价已经确认')),False))
    cases.append(run_case('procurement','business-promise',lambda root:write_draft(root,lambda value:value.update(text=value['text']+'\n现货充足，保证交付。')),False))
    def mismatched(root):
        write_draft(root)
        (root/'draft.txt').write_text('different body',encoding='utf-8')
    cases.append(run_case('procurement','json-and-text-disagree',mismatched,False))
    value={'format':'fixture-oracle-self-check/1','status':'PASS' if all(case['status']=='PASS' for case in cases) else 'FAIL',
           'synthetic':True,'cases':cases,
           'limits':'Self-checks of declared deterministic oracles. No model task, production or AGI claim.'}
    print(json.dumps(value,ensure_ascii=False,indent=2))
    return 0 if value['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
