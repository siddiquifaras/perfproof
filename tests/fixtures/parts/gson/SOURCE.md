# gson

Repository: https://github.com/google/gson
Commit: 57731049da3e415be3d5d34db1ca3b80f3c73810

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

- `pom.xml`
- `*/pom.xml`
- `gson/src/main/java/com/google/gson/Gson.java`
- `gson/src/main/java/com/google/gson/JsonObject.java`
- `gson/src/main/java/com/google/gson/JsonParser.java`
- `gson/src/test/java/com/google/gson/GsonTest.java`
- `gson/src/test/java/com/google/gson/JsonParserTest.java`

13 files copied.
