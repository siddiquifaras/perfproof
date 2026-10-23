# coverage.py lcov and Cobertura reports

`lcov.info` and `cobertura.xml` were captured, unedited, from a real run
in a fresh directory holding `calc.py` and `test_calc.py` (saved here as
`calc.py.txt` and `test_calc.py.txt`):

```
python -m coverage run --branch -m pytest -q -p no:cacheprovider test_calc.py
python -m coverage lcov -o lcov.info
python -m coverage xml -o cobertura.xml
```

`never_called` is never run, so `calc.py` line 6 has zero hits.
`cobertura.xml`'s `<source>` is the absolute directory the run was made
in; the tests replace it with their own directory.

- Tool: coverage.py 7.16.2 with C extension, pytest 9.1.1, Python 3.12.12
- OS: Darwin arm64 (macOS)
- Date: 2026-10-22
- Used by: `tests/bench/test_coverage_report.py`
