# full-stack-fastapi-template

Repository: https://github.com/fastapi/full-stack-fastapi-template
Commit: f4bcfaba7c85be3c6332cc74a20819f586dcdbc6

A trimmed copy: every build or test manifest, and a few
real source and test files so the language count, test
discovery and the native, benchmark and entry-point
detectors have something to read. The files are
unmodified; Python files are stored as `<name>.py.fixture`
so the linter, type checker and pytest leave them
alone, and the tests restore the name. Language counts
of this tree are of the
copy, not of the repository. The patterns used
(relative to the repository root, one `/` level per
`*`):

- `pyproject.toml`
- `package.json`
- `uv.lock`
- `backend/pyproject.toml`
- `backend/app/*.py`
- `backend/app/api/*.py`
- `backend/app/api/routes/*.py`
- `backend/app/core/*.py`
- `backend/tests/*.py`
- `backend/tests/api/routes/*.py`
- `backend/tests/crud/*.py`
- `frontend/package.json`
- `frontend/src/main.tsx`
- `frontend/src/routes/*.tsx`
- `frontend/src/routes/_layout/*.tsx`
- `frontend/tests/*.ts`
- `packages/react-email/package.json`
- `packages/react-email/emails/*.tsx`

56 files copied.
