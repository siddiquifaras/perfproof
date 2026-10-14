---
name: verify
description: Run perfproof's full local check (format, lint, types, tests, and calibration when stats changed) and report exact results. Use before claiming anything works, before a commit, or before opening a PR.
---

Run each step and keep going after a failure so the report is complete.

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

If `git diff origin/main --name-only` lists anything under
`src/perfproof/stats/` or `tests/calibration/`, also run:

```bash
uv run pytest -m calibration
```

If the diff touches a profiler adapter or the runner and the tool it
wraps is installed, run its integration tests:

```bash
uv run pytest -m integration -k <adapter or module name>
```

Report one line per step: the command, pass or fail, and the counts
(tests passed, failed, skipped, with the skip reasons). Name every step
you skipped and why. "Passed" without the counts is not a report.
