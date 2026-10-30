# Native hook recovery hotfix 1.9.2

The interrupted-task write fence previously denied all tools, including status and read-only diagnosis. The installed hook now has a conservative inspection lane. It abstains from host permission decisions for recognized reads; it does not grant permission, reset budgets, clear pending effects or replay writes.

## Checks actually run

- `python -X utf8 -m unittest discover -s tests -p "test_native*.py" -v`: 27 tests passed, including 4 new recovery regressions, native MCP, native sessions and existing review compatibility.
- An existing interrupted local task retained two unknown calls. Installed hook decision probes admitted the exact installed readback CLI and denied a write probe. The readback CLI then actually read all four declared artifacts with exit code 0; there were no artifact read errors. The task record, budget, pending calls and lease were unchanged. See [sanitized installation evidence](installed-acceptance.json).
- Full installation verification compared 90 files against the canonical export. Hook definitions and global permission configuration were not changed.

These are hook-decision and local file-read checks. No business write was executed, no blocked business conversation was resumed, and no business delivery was declared complete. Old unknown calls with only input hashes still require operation-specific trusted evidence. `recover` returns that route; it is not an automatic reconciliation adapter.

The initial ad hoc installed-module probe omitted the exported `scripts` import path and failed before reading or changing task state. The corrected probe used the same module paths as the installed CLI and produced the evidence above.
