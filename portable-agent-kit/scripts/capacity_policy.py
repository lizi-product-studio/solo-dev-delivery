#!/usr/bin/env python3
"""Classify model-capacity failures and return a bounded recovery recommendation.

This command never retries, switches models, or changes account settings. It
makes the safe next action explicit so a host can continue independent work
without pretending a capacity error was a task failure or a success.
"""
from __future__ import annotations
import argparse, json, re


def classify(text, attempt=0, max_delay=300):
    value = str(text or "").strip()
    lowered = value.lower()
    if re.search(r"at capacity|capacity|模型容量|too many requests", lowered):
        delay = min(max_delay, max(5, 2 ** min(max(attempt, 0), 8)))
        return {"status":"RETRY_LATER","failure_class":"capacity_error","retry_after_seconds":delay,
                "switch_model":False,"continue_independent_work":True,
                "reason":"Provider capacity is unavailable; keep the frozen model and preserve the receipt."}
    if re.search(r"quota|usage limit|rate limit|限额|用量", lowered):
        return {"status":"STOP_NEEDS_ACCOUNT_ACTION","failure_class":"quota_error","retry_after_seconds":None,
                "switch_model":False,"continue_independent_work":True,
                "reason":"This looks like account or workspace usage control, not a transient capacity signal."}
    if re.search(r"auth|unauthorized|forbidden|登录|权限", lowered):
        return {"status":"STOP_NEEDS_AUTH","failure_class":"auth_error","retry_after_seconds":None,
                "switch_model":False,"continue_independent_work":True,
                "reason":"Identity or permission is required; do not loop or infer authorization."}
    if re.search(r"timeout|timed out|connection|network|连接|超时", lowered):
        return {"status":"RETRY_DIAGNOSTIC","failure_class":"transport_error","retry_after_seconds":15,
                "switch_model":False,"continue_independent_work":True,
                "reason":"Check the transport and preserve the original attempt before a bounded retry."}
    return {"status":"NEEDS_CONTEXT","failure_class":"unknown_error","retry_after_seconds":None,
            "switch_model":False,"continue_independent_work":True,
            "reason":"The error does not identify a safe automatic recovery."}


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('message'); p.add_argument('--attempt',type=int,default=0); p.add_argument('--max-delay',type=int,default=300)
    args=p.parse_args(); print(json.dumps(classify(args.message,args.attempt,args.max_delay),ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
