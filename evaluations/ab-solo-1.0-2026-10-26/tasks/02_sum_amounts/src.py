"""Intentionally incomplete baseline; repair according to TASK.md and CONTRACT.md."""

def solve(value):
    # BUG: deliberately ignores edge cases required by the contract.
    return sum(r.get("amount",0) for r in records)
