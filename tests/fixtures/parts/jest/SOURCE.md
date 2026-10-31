# jest

Repository: https://github.com/jestjs/jest
Commit: 61050e9323e742539dc2360236671110e37329e6

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

- `package.json`
- `packages/*/package.json`
- `website/package.json`
- `examples/*/package.json`
- `packages/jest-cli/src/*.ts`
- `packages/jest-cli/bin/*`
- `packages/jest-config/src/index.ts`
- `packages/jest-core/src/*.ts`

103 files copied.
