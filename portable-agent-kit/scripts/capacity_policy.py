#!/usr/bin/env python3
"""Plan bounded recovery when a model call cannot be served.

This is an advisory, host-neutral policy. It does not call a model, change
the account, or silently switch models. A host can use the JSON plan to
persist a receipt, keep doing work that does not need the model, and retry a
capacity error later. After the bounded retry window, capacity failures stay
recoverable instead of becoming a false task failure or an immediate request
for the user to take over.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


POLICY_FORMAT = "capacity-recovery/1"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_DELAY = 300


def _attempt(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _delay(attempt, max_delay=DEFAULT_MAX_DELAY, base=5):
    cap = _positive_int(max_delay, DEFAULT_MAX_DELAY)
    # Preserve the original classify() delay contract: attempts 0-2 remain
    # at the five-second floor, then the bounded power-of-two backoff grows.
    return min(cap, max(base, 2 ** min(_attempt(attempt), 8)))


def _classify_text(message):
    value = str(message or "").strip()
    lowered = value.lower()
    if re.search(r"at capacity|capacity|模型容量|too many requests", lowered):
        return "capacity_error"
    if re.search(r"quota|usage limit|rate limit|限额|用量", lowered):
        return "quota_error"
    if re.search(r"auth|unauthorized|forbidden|登录|权限", lowered):
        return "auth_error"
    if re.search(r"timeout|timed out|connection|network|连接|超时", lowered):
        return "transport_error"
    return "unknown_error"


def _base_result(message, attempt=0, max_delay=DEFAULT_MAX_DELAY):
    """Return the backwards-compatible classification and safe defaults."""
    attempt = _attempt(attempt)
    failure_class = _classify_text(message)
    common = {
        "policy_format": POLICY_FORMAT,
        "failure_class": failure_class,
        "attempt": attempt,
        "switch_model": False,
        "continue_independent_work": True,
        "checkpoint_required": True,
        "user_action_required": False,
    }
    if failure_class == "capacity_error":
        return {
            **common,
            "status": "RETRY_LATER",
            "retry_after_seconds": _delay(attempt, max_delay),
            "reason": "Provider capacity is unavailable; preserve the receipt and keep the frozen model.",
        }
    if failure_class == "quota_error":
        return {
            **common,
            "status": "STOP_NEEDS_ACCOUNT_ACTION",
            "retry_after_seconds": None,
            "user_action_required": True,
            "reason": "This looks like account or workspace usage control, not a transient capacity signal.",
        }
    if failure_class == "auth_error":
        return {
            **common,
            "status": "STOP_NEEDS_AUTH",
            "retry_after_seconds": None,
            "user_action_required": True,
            "reason": "Identity or permission is required; do not loop or infer authorization.",
        }
    if failure_class == "transport_error":
        return {
            **common,
            "status": "RETRY_DIAGNOSTIC",
            "retry_after_seconds": 15,
            "reason": "Check the transport and preserve the original attempt before a bounded retry.",
        }
    return {
        **common,
        "status": "NEEDS_CONTEXT",
        "retry_after_seconds": None,
        "reason": "The error does not identify a safe automatic recovery; collect context before escalating.",
    }


def classify(text, attempt=0, max_delay=DEFAULT_MAX_DELAY):
    """Classify a failure without scheduling or executing a retry.

    Existing callers can keep using this function. ``plan_recovery`` adds the
    attempt budget and the concrete host actions.
    """
    return _base_result(text, attempt=attempt, max_delay=max_delay)


def _receipt_id(result, message):
    payload = json.dumps(
        {"failure_class": result["failure_class"], "attempt": result["attempt"], "message": str(message or "")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def plan_recovery(message, attempt=0, max_attempts=DEFAULT_MAX_ATTEMPTS, max_delay=DEFAULT_MAX_DELAY):
    """Build a deterministic recovery plan for one observed failure.

    ``max_attempts`` counts model-call retries after the current observation.
    A capacity error at the limit becomes ``PAUSE_RECOVERABLE``: the host
    should checkpoint and continue independent work, with no user prompt.
    Account/auth failures remain the only classes that explicitly require a
    user action. Unknown errors request more diagnostic context first.
    """
    limit = _positive_int(max_attempts, DEFAULT_MAX_ATTEMPTS)
    result = _base_result(message, attempt=attempt, max_delay=max_delay)
    observed = result["attempt"]
    failure_class = result["failure_class"]
    if failure_class == "capacity_error":
        if observed >= limit:
            result.update(
                status="PAUSE_RECOVERABLE",
                retry_after_seconds=None,
                retry_exhausted=True,
                next_action="checkpoint_then_continue_independent_work_and_recheck_later",
                reason="Capacity retry budget is exhausted; preserve the checkpoint and keep the task resumable.",
            )
        else:
            result.update(
                retry_exhausted=False,
                next_action="persist_receipt_then_continue_independent_work_and_retry_same_model",
            )
    elif failure_class == "transport_error":
        if observed >= limit:
            result.update(
                status="PAUSE_RECOVERABLE",
                retry_after_seconds=None,
                retry_exhausted=True,
                next_action="checkpoint_then_continue_independent_work_and_recheck_transport",
                reason="Transport retry budget is exhausted; preserve the checkpoint before any handoff.",
            )
        else:
            result.update(
                retry_exhausted=False,
                next_action="persist_receipt_then_run_transport_diagnostic_and_retry",
            )
    elif failure_class in {"quota_error", "auth_error"}:
        result.update(
            retry_exhausted=True,
            next_action="persist_receipt_then_request_minimal_account_or_auth_action",
        )
    else:
        result.update(
            retry_exhausted=False,
            next_action="persist_receipt_then_collect_error_context_before_escalation",
        )
    result["max_attempts"] = limit
    result["receipt_id"] = _receipt_id(result, message)
    result["host_actions"] = [
        "persist_failure_receipt",
        "continue_independent_work",
        result["next_action"],
    ]
    return result


def append_receipt(path, plan, message=None):
    """Append one JSONL receipt and return its absolute path.

    Receipt writing is local and explicit; it does not imply that the model
    call succeeded. Parent directories are created for a resumable handoff.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    row = dict(plan)
    if message is not None:
        row["observed_message"] = str(message)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return str(target.resolve())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message")
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--max-delay", type=int, default=DEFAULT_MAX_DELAY)
    parser.add_argument("--receipt", type=Path, help="Append the plan to a local JSONL receipt")
    args = parser.parse_args(argv)
    result = plan_recovery(
        args.message,
        attempt=args.attempt,
        max_attempts=args.max_attempts,
        max_delay=args.max_delay,
    )
    if args.receipt:
        result["receipt_path"] = append_receipt(args.receipt, result, args.message)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
