# django

Repository: https://github.com/django/django
Commit: a6a5d1e67a62328a303257e511886e1a139a0558

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
- `tox.ini`
- `package.json`
- `django/__init__.py`
- `tests/runtests.py`
- `tests/test_sqlite.py`
- `scripts/pr_quality/*.py`
- `scripts/pr_quality/tests/*.py`

11 files copied.
