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

If the diff touches the profiler summariser or the runner and the tool it
wraps is installed, run its integration tests:

```bash
uv run pytest -m integration -k <module name>
```

While GitHub Actions is manual-only, also run the Linux check in
Docker on the exact commit under review. It matches CI's ubuntu job on
x86_64 (emulated on Apple silicon; about a minute):

```bash
git archive --format=tar <commit> | docker run --rm -i \
  --platform linux/amd64 -e UV_PYTHON=<3.12|3.13> \
  -e UV_PYTHON_PREFERENCE=only-system python:<3.12|3.13>-bookworm sh -c '
  python -c "import uuid; print(uuid.uuid4().hex)" > /etc/machine-id
  mkdir /w && cd /w && tar -x
  pip -q install --root-user-action=ignore uv==0.12.20
  git init -q && git -c user.name=v -c user.email=v@v add -A
  git -c user.name=v -c user.email=v@v commit -qm v
  uv sync -q --frozen && uv lock --check
  uv run --frozen ruff format --check . && uv run --frozen ruff check .
  uv run --frozen pyright && uv run --frozen pytest -q'
```

The container has no machine-id or git repository of its own, so the
command writes a random machine-id and commits the archive; the
machine and lock tests need both.

Report one line per step: the command, pass or fail, and the counts
(tests passed, failed, skipped, with the skip reasons). Name every step
you skipped and why. "Passed" without the counts is not a report.
