# ripgrep

Repository: https://github.com/BurntSushi/ripgrep
Commit: 91f5d22ef48bde459d90a8c7dbeab416e97d0f2b

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

- `Cargo.toml`
- `build.rs`
- `crates/*/Cargo.toml`
- `crates/core/main.rs`
- `crates/*/src/lib.rs`
- `crates/globset/benches/bench.rs`
- `crates/ignore/benches/workloads.rs`
- `crates/ignore/tests/gitignore_skip_bom.rs`
- `crates/matcher/tests/tests.rs`
- `fuzz/Cargo.toml`
- `crates/ignore/examples/walk.rs`

27 files copied.
