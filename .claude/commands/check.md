---
description: Run the full local gate the way CI does
allowed-tools: Bash(make:*), Bash(uv sync:*), Bash(uv run:*), Bash(uvx:*)
---

Run the gate from the repository root, keeping going after a failure so the report covers everything:

```bash
make -k
```

That is `sync`, `lint`, `typecheck`, `testcov`, `docs` and `audit` — the same order CI runs them in.
Coverage is enforced at 100% with branches, so a non-zero `coverage report` is a real failure, not a threshold to lower.
Report the results; do not fix anything unless I ask.
