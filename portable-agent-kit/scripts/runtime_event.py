#!/usr/bin/env python3
"""Submit one host/Agent event to a persisted AGI-like Runtime task."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from control_plane.runtime import DeliveryRuntime

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--db',type=Path,required=True); parser.add_argument('--task-id',required=True)
parser.add_argument('--lease-id',required=True); parser.add_argument('--op',required=True)
parser.add_argument('--payload',default='{}'); parser.add_argument('--evidence-root',type=Path)
parser.add_argument('--reviewer-id'); args=parser.parse_args()
payload=json.loads(Path(args.payload[1:]).read_text(encoding='utf-8') if args.payload.startswith('@') else args.payload)
runtime=DeliveryRuntime(args.db,evidence_root=args.evidence_root)
state,response=runtime.apply(args.task_id,args.lease_id,args.op,payload,evidence_root=args.evidence_root,reviewer_id=args.reviewer_id)
print(json.dumps({'state':state,'response':response},ensure_ascii=False,indent=2))
