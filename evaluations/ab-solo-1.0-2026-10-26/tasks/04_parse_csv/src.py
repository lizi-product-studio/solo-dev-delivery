"""Intentionally incomplete baseline; repair according to TASK.md and CONTRACT.md."""

def solve(value):
    # BUG: deliberately ignores edge cases required by the contract.
    return [dict(zip(lines[0].split(","),x.split(","))) for x in lines[1:]]
