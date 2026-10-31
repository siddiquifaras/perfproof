# go-sqlite3

Repository: https://github.com/mattn/go-sqlite3
Commit: dbbe931904f2ee06546448fbe7cf7780f61bc110

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

- `go.mod`
- `sqlite3.go`
- `sqlite3_test.go`
- `callback_bench_test.go`
- `sqlite3_opt_unlock_notify.c`
- `sqlite3_opt_unlock_notify.go`
- `error.go`
- `convert.go`
- `_example/simple/simple.go`

9 files copied.
