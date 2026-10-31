# python_example

Repository: https://github.com/pybind/python_example
Commit: 3dc0f6efc437bd370f45cce19c2f24d5cf424b96

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
- `setup.py`
- `src/main.cpp`
- `tests/test.py`
- `docs/conf.py`

5 files copied.
