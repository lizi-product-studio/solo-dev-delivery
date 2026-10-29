# Contract

- Public entry point: `src.py:solve(value)`
- Deterministic, standard library only.
- Input must not be mutated.
- Errors: use `ValueError` for invalid/cyclic data and `KeyError` for missing required keys.
- Output must exactly match the documented shape.
