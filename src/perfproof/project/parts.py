"""Split a repository into parts, each with what `perfproof find` needs.

`detect_parts` is Unit A of ``docs/find.md``. A **part** is a directory
that holds a build or test manifest. For each one it reports, from the
files alone and without running anything of the project's:

- the language, by the largest share of source files the part owns;
- the project-model target it maps to, and that target's class;
- the test command, normalised so a profiler can wrap it;
- native code and the image name its frames will carry;
- existing benchmarks and entry points;
- which profiler, native recorder and coverage tool this machine has.

A fact that cannot be established is an `Established` holding ``None``
and a reason; nothing is guessed. The two limits of a static reading are
named where they bite: a ``setup.py`` that builds its extensions from a
variable, and a ``package.json`` test script that is a shell pipeline,
are not resolved.

The walk is `perfproof.project.detect`'s: the same skipped directories
and `MAX_FILES`, with manifests taken to `MAX_DEPTH` and the language
count to `COUNT_DEPTH`. The probes run argv lists only, each with a
timeout, and write nothing.
"""

import ast
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final, Literal, cast

from perfproof.profilers.run import default_profiler
from perfproof.project.detect import (
    _ROUTE as _ROUTE_LINE,  # pyright: ignore[reportPrivateUsage]
)
from perfproof.project.detect import (
    COUNT_DEPTH,
    MAX_DEPTH,
    MAX_SCANNED_PY,
)
from perfproof.project.detect import (
    _source_files as _count_sources,  # pyright: ignore[reportPrivateUsage]
)
from perfproof.project.detect import (
    _Tree as _SourceTree,  # pyright: ignore[reportPrivateUsage]
)
from perfproof.project.detect import (
    _walk as _walk_tree,  # pyright: ignore[reportPrivateUsage]
)
from perfproof.schema import (
    EntryPoint,
    Established,
    ExistingBenchmark,
    NativeCode,
    Part,
    PartTooling,
    ProjectModel,
    TargetClass,
    TestCommand,
    ToolStatus,
)

__all__ = ["JACOCO_DIR", "JACOCO_VERSION", "detect_parts"]

PROBE_TIMEOUT_SECONDS: Final = 10.0
"""Timeout of every subprocess a probe starts, in seconds."""
JACOCO_VERSION: Final = "0.8.12"
"""The JaCoCo release ``skills/perfproof/java.md`` names."""
JACOCO_DIR: Final = Path.home() / ".cache" / "perfproof" / "jacoco"
"""Where the JaCoCo jars are looked for and where the install argv puts
them. ``java.md`` says only "a directory outside the tree"; this is the
one place Unit B and the skill can both name."""
_MAVEN: Final = "https://repo1.maven.org/maven2/org/jacoco"

_Language = Literal[
    "python",
    "javascript",
    "typescript",
    "rust",
    "go",
    "java",
    "kotlin",
    "c",
    "cpp",
    "csharp",
    "ruby",
    "other",
]
_LANGUAGES: Final[tuple[_Language, ...]] = (
    "python",
    "javascript",
    "typescript",
    "rust",
    "go",
    "java",
    "kotlin",
    "c",
    "cpp",
    "csharp",
    "ruby",
    "other",
)
_MANIFEST_NAMES: Final = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "CMakeLists.txt",
)
_MANIFEST_LANGUAGE: Final[Mapping[str, _Language]] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "package.json": "javascript",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "Gemfile": "ruby",
    "CMakeLists.txt": "cpp",
}
_PREFERRED_TARGETS: Final[Mapping[str, tuple[TargetClass, ...]]] = {
    "python": (
        "web_service",
        "llm_serving",
        "agentic",
        "gpu_kernel",
        "python_cpu",
    ),
    "javascript": ("web_service", "node"),
    "typescript": ("web_service", "node"),
    "rust": ("web_service", "native"),
    "go": ("web_service", "native"),
    "c": ("native",),
    "cpp": ("native",),
    "java": ("web_service", "jvm"),
    "kotlin": ("web_service", "jvm"),
}
_SCRIPT_SUFFIXES: Final = (".py", ".js", ".mjs", ".cjs", ".sh")
_C_SOURCE: Final = frozenset(
    {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"}
)
_CMAKE_TARGET: Final = re.compile(
    r"add_(library|executable)\s*\(\s*([^\s()]+)([^)]*)\)", re.I
)
_GO_CGO: Final = re.compile(r'^\s*import\s+"C"', re.M)
_GO_MAIN_PKG: Final = re.compile(r"^package\s+main\b", re.M)
_GO_BENCH: Final = re.compile(r"^func\s+Benchmark\w*\s*\(", re.M)
_PY_BENCH_FIXTURE: Final = re.compile(
    r"^\s*def\s+test_\w+\([^)]*\bbenchmark\b", re.M
)
_NODE_RUNNERS: Final = ("vitest", "jest", "mocha")
_SHELL_TOKENS: Final = frozenset({"&&", "||", ";", "|", ">", ">>", "<", "&"})


@dataclass
class _Env:
    """What the probes may look at, and what they have learned."""

    root: Path
    path: str
    platform: str
    python: Path | None
    neutral: Path
    environ: Mapping[str, str]
    cache: dict[str, object] = field(default_factory=dict[str, object])

    def which(self, name: str) -> str | None:
        return shutil.which(name, path=self.path)

    def run(self, argv: Sequence[str]) -> tuple[int, str] | str:
        """Run a probe: ``(exit code, output)``, or an error text.

        It runs in an empty directory, never in the repository, so
        nothing the repository holds (a ``sysconfig.py``, a
        ``rust-toolchain.toml``) is read by the tool.
        """
        env = {**os.environ, "PATH": self.path}
        try:
            done = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
                stdin=subprocess.DEVNULL,
                env=env,
                cwd=self.neutral,
            )
        except subprocess.TimeoutExpired:
            return f"{argv[0]} did not finish in {PROBE_TIMEOUT_SECONDS:g} s"
        except OSError as exc:
            return f"{argv[0]} could not start: {exc}"
        return done.returncode, (done.stdout + done.stderr).strip()


@dataclass(frozen=True)
class _Ctx:
    env: _Env
    tree: _SourceTree
    model: ProjectModel
    roots: tuple[str, ...]
    files_of: Mapping[str, tuple[str, ...]]
    languages: Mapping[str, str]


def _est[T](value: T) -> Established[T]:
    return Established[T](value=value, reason=None)


def _no_command(reason: str) -> Established[TestCommand]:
    return Established[TestCommand](value=None, reason=reason)


def _no_class(reason: str) -> Established[TargetClass]:
    return Established[TargetClass](value=None, reason=reason)


def _dict(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _strs(value: object) -> list[str]:
    return [v for v in _list(value) if isinstance(v, str)]


def _join(directory: str, name: str) -> str:
    return name if directory == "." else f"{directory}/{name}"


def _dir(path: str) -> str:
    return str(PurePosixPath(path).parent)


def _toml(tree: _SourceTree, path: str) -> dict[str, object]:
    text = tree.read(path)
    if text is None:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _json(tree: _SourceTree, path: str) -> dict[str, object]:
    text = tree.read(path)
    if text is None:
        return {}
    try:
        return _dict(json.loads(text))
    except json.JSONDecodeError:
        return {}


def _line_of(text: str, pattern: str) -> int | None:
    found = re.search(pattern, text, re.M)
    return None if found is None else text.count("\n", 0, found.start()) + 1


# ---------------------------------------------------------------- parts


def _manifest_dirs(tree: _SourceTree) -> dict[str, list[str]]:
    """Map each directory with a manifest to its manifests, sorted.

    A ``CMakeLists.txt`` below another ``CMakeLists.txt`` is that
    project's subdirectory, not a part, unless the directory has a
    manifest of its own.
    """
    found: dict[str, list[str]] = defaultdict(list)
    for path in tree.files:
        pure = PurePosixPath(path)
        if len(pure.parts) - 1 > MAX_DEPTH:
            continue
        name = pure.name
        if name in _MANIFEST_NAMES or name.endswith((".csproj", ".sln")):
            found[_dir(path)].append(path)
    cmake_dirs = {
        d for d, m in found.items() if _has_name(m, "CMakeLists.txt")
    }
    for directory in sorted(cmake_dirs):
        manifests = found[directory]
        above = PurePosixPath(directory).parents
        if len(manifests) == 1 and any(
            str(p) in cmake_dirs for p in above if str(p) != directory
        ):
            del found[directory]
    return found


def _has_name(paths: Sequence[str], name: str) -> bool:
    return any(PurePosixPath(p).name == name for p in paths)


def part_id(root: str, taken: set[str]) -> str:
    """Return a root's part id, adding ``-2``, ``-3`` on a clash."""
    base = "root" if root == "." else root.replace("/", ".")
    base = re.sub(r"[^A-Za-z0-9._-]", "-", base)
    if not re.match(r"[A-Za-z0-9]", base):
        base = "p" + base
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    taken.add(candidate)
    return candidate


def _owner(path: str, roots: frozenset[str]) -> str | None:
    """Return the deepest part root that contains a path."""
    for parent in PurePosixPath(path).parents:
        if str(parent) in roots:
            return str(parent)
    return None


def _file_language(path: str, group: str) -> str:
    suffix = PurePosixPath(path).suffix
    if group == "js":
        ts = (".ts", ".tsx", ".mts", ".cts")
        return "typescript" if suffix in ts else "javascript"
    if group == "jvm":
        return {".java": "java", ".kt": "kotlin", ".kts": "kotlin"}.get(
            suffix, "other"
        )
    if group == "c_family":
        if suffix == ".c":
            return "c"
        return "h" if suffix == ".h" else "cpp"
    if group == "dotnet":
        return "csharp" if suffix == ".cs" else "other"
    return group if group in _LANGUAGES else "other"


def _part_language(
    paths: Sequence[str], groups: Mapping[str, str]
) -> _Language:
    """Pick the language with most source files; ties by enum order.

    A part that owns no source file (a workspace root, an aggregator)
    is ``other``: its manifests do not say what its code is.
    """
    count: Counter[str] = Counter(groups[p] for p in paths if p in groups)
    header = count.pop("h", 0)
    if header:
        count["cpp" if count["cpp"] > count["c"] else "c"] += header
    if not count:
        return "other"
    best = max(
        count,
        key=lambda k: (count[k], -_LANGUAGES.index(cast("_Language", k))),
    )
    return cast("_Language", best)


def _tool_language(language: str, manifests: Sequence[str]) -> str:
    """Return the language whose tools a part needs.

    A part with no source files of its own (``other``) still has a
    build, so its tools follow its first manifest.
    """
    if language != "other":
        return language
    for manifest in manifests:
        name = PurePosixPath(manifest).name
        if name in _MANIFEST_LANGUAGE:
            return _MANIFEST_LANGUAGE[name]
        if name.endswith((".csproj", ".sln")):
            return "csharp"
    return language


def _order_manifests(
    manifests: Sequence[str], language: _Language
) -> tuple[str, ...]:
    def key(path: str) -> tuple[int, int, str]:
        name = PurePosixPath(path).name
        own = _MANIFEST_LANGUAGE.get(name)
        if own is None and name.endswith((".csproj", ".sln")):
            own = "csharp"
        rank = _MANIFEST_NAMES.index(name) if name in _MANIFEST_NAMES else 99
        return (0 if own == language else 1, rank, path)

    return tuple(sorted(manifests, key=key))


def _target_of(
    ctx: _Ctx, root: str, language: str
) -> tuple[str | None, Established[TargetClass]]:
    """Choose the project-model target whose evidence the part owns.

    The target with the most evidence paths owned by the part wins; a
    tie goes to the class that fits the part's language. Evidence paths
    belong to the deepest part that contains them.
    """
    rootset = frozenset(ctx.roots)
    best: tuple[tuple[int, int], str, TargetClass] | None = None
    prefer = _PREFERRED_TARGETS.get(language, ())
    for target in ctx.model.targets:
        owned = sum(
            1
            for f in target.provenance.files
            if _owner(f.path, rootset) == root
        )
        if owned == 0:
            continue
        rank = (
            prefer.index(target.target_class)
            if target.target_class in prefer
            else len(prefer)
        )
        key = (-owned, rank)
        if best is None or key < best[0]:
            best = (key, target.id, target.target_class)
    if best is None:
        return None, _no_class(
            f"no project-model target has evidence under {root}"
        )
    return best[1], _est(best[2])


# --------------------------------------------------------- interpreters


def _interpreter(ctx: _Ctx, root: str) -> str | None:
    """Return the project's interpreter, if one is established.

    The given ``python``, else ``.venv`` or ``venv`` of the part, then
    of the repository, then ``$VIRTUAL_ENV``. It is only a file that
    exists: `_pytest_command` checks that it can import pytest.
    """
    if ctx.env.python is not None:
        return str(ctx.env.python)
    for base in (ctx.env.root / root, ctx.env.root):
        for name in (".venv", "venv"):
            candidate = base / name / "bin" / "python"
            if candidate.exists():
                return str(candidate)
    active = ctx.env.environ.get("VIRTUAL_ENV")
    if active:
        candidate = Path(active) / "bin" / "python"
        if candidate.exists():
            return str(candidate)
    return None


def _ext_suffix(ctx: _Ctx, interpreter: str | None) -> str:
    """Return the extension-module suffix a built Python module has.

    Asked of the project's interpreter; with none, of the interpreter
    perfproof runs in, which is the only suffix this machine can name.
    """
    key = f"ext:{interpreter}"
    cached = ctx.env.cache.get(key)
    if isinstance(cached, str):
        return cached
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    if interpreter is not None:
        ran = ctx.env.run(
            [
                interpreter,
                "-I",
                "-c",
                "import sysconfig;"
                "print(sysconfig.get_config_var('EXT_SUFFIX'))",
            ]
        )
        if not isinstance(ran, str) and ran[0] == 0 and ran[1].startswith("."):
            suffix = ran[1].splitlines()[-1]
    ctx.env.cache[key] = suffix
    return suffix


# ------------------------------------------------------------ tests


def _pytest_command(
    ctx: _Ctx, root: str, manifests: Sequence[str]
) -> Established[TestCommand]:
    tree = ctx.tree
    evidence: str | None = None
    for manifest in manifests:
        name = PurePosixPath(manifest).name
        text = tree.read(manifest) or ""
        if name == "pyproject.toml":
            tool = _dict(_toml(tree, manifest).get("tool"))
            if "pytest" in tool:
                evidence = f"{manifest}: [tool.pytest]"
        elif name == "setup.cfg" and "[tool:pytest]" in text:
            evidence = f"{manifest}: [tool:pytest]"
    for name, marker in (("pytest.ini", None), ("tox.ini", "[pytest]")):
        path = _join(root, name)
        if tree.has(path) and (
            marker is None or marker in (tree.read(path) or "")
        ):
            evidence = f"{path}: pytest configuration"
    own = ctx.files_of.get(root, ())
    if evidence is None:
        own_dirs = (_join(root, "tests/"), _join(root, "test/"))
        tests = [
            f
            for f in own
            if re.fullmatch(r"(?:test_.*|.*_test)\.py", PurePosixPath(f).name)
            and f.startswith(own_dirs)
        ]
        if not tests:
            return _no_command(
                f"no pytest configuration and no test_*.py under "
                f"{own_dirs[0]} or {own_dirs[1]}"
            )
        runner = next(
            (
                r
                for r in ("tests/runtests.py", "runtests.py", "manage.py")
                if ctx.tree.has(_join(root, r))
            ),
            None,
        )
        if runner is not None:
            return _no_command(
                f"{_join(root, runner)} is the project's own test runner and "
                "there is no pytest configuration; a pytest command would "
                "not be the project's tests"
            )
        evidence = f"{tests[0]}: test_*.py under the part's own tests dir"
    interpreter = _interpreter(ctx, root)
    if interpreter is None:
        return _no_command(
            f"pytest found ({evidence}) but there is no interpreter: no "
            f".venv or venv under {root} or the repository, and "
            "VIRTUAL_ENV is not set; create the project's virtual "
            "environment with pytest installed"
        )
    ran = ctx.env.run([interpreter, "-I", "-c", "import pytest"])
    if isinstance(ran, str) or ran[0] != 0:
        why = ran if isinstance(ran, str) else ran[1][-200:]
        return _no_command(
            f"pytest found ({evidence}) but {interpreter} cannot import "
            f"pytest ({why}); install pytest into that environment"
        )
    argv = (interpreter, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider")
    return _est(TestCommand(argv=argv, cwd=root, evidence=evidence))


def _node_bin(ctx: _Ctx, root: str, binary: str) -> str | None:
    """Find ``node_modules/.bin/<binary>`` in the part or above it."""
    base = PurePosixPath(root)
    for directory in (base, *base.parents):
        candidate = ctx.env.root / directory / "node_modules" / ".bin" / binary
        if candidate.exists():
            return str(candidate)
    return None


def _node_command(
    ctx: _Ctx, root: str, manifests: Sequence[str]
) -> Established[TestCommand]:
    manifest = _join(root, "package.json")
    if manifest not in manifests:
        return _no_command(f"no package.json in {root}")
    scripts = _dict(_json(ctx.tree, manifest).get("scripts"))
    script = scripts.get("test")
    if not isinstance(script, str) or not script.strip():
        return _no_command(f"{manifest} has no scripts.test")
    if "no test specified" in script:
        return _no_command(f"{manifest} scripts.test is npm's placeholder")
    import shlex

    try:
        words = shlex.split(script)
    except ValueError:
        return _no_command(
            f"{manifest} scripts.test does not parse: {script!r}"
        )
    if not words or _SHELL_TOKENS & set(words) or "=" in words[0]:
        return _no_command(
            f"{manifest} scripts.test is a shell command line, not one "
            f"runner: {script!r}"
        )
    evidence = f"{manifest} scripts.test: {script}"
    if words[0] == "node" and "--test" in words[1:]:
        return _est(
            TestCommand(argv=tuple(words), cwd=root, evidence=evidence)
        )
    runner = words[0]
    if runner not in _NODE_RUNNERS:
        return _no_command(
            f"{manifest} scripts.test runs {runner!r}, not vitest, jest, "
            "mocha or node --test"
        )
    found = _node_bin(ctx, root, runner)
    if found is None:
        return _no_command(
            f"scripts.test runs {runner} but node_modules/.bin/{runner} "
            f"is not under {root} or above it; install the dependencies"
        )
    argv = (found, *words[1:])
    return _est(TestCommand(argv=argv, cwd=root, evidence=evidence))


def _jvm_command(
    ctx: _Ctx, root: str, manifests: Sequence[str]
) -> Established[TestCommand]:
    names = {PurePosixPath(m).name: m for m in manifests}
    own = ctx.files_of.get(root, ())
    has_tests = any(
        "/src/test/" in f"/{f}" or f.startswith("src/test/") for f in own
    )
    if not has_tests:
        return _no_command(f"no src/test sources in {root}")
    if "pom.xml" in names:
        return _est(
            TestCommand(
                argv=("mvn", "-q", "test"),
                cwd=root,
                evidence=f"{names['pom.xml']} with src/test sources",
            )
        )
    gradle = next((n for n in names if n.startswith("build.gradle")), None)
    if gradle is None:
        return _no_command(f"no pom.xml or build.gradle in {root}")
    base = PurePosixPath(root)
    wrapper = any(
        ctx.tree.has(str(d / "gradlew")) for d in (base, *base.parents)
    ) or ctx.tree.has("gradlew")
    argv = ("./gradlew", "test") if wrapper else ("gradle", "test")
    return _est(
        TestCommand(
            argv=argv,
            cwd=root,
            evidence=f"{names[gradle]} with src/test sources"
            + ("" if wrapper else " and no gradlew wrapper"),
        )
    )


def _go_command(ctx: _Ctx, root: str) -> Established[TestCommand]:
    tests = [f for f in ctx.files_of.get(root, ()) if f.endswith("_test.go")]
    if not tests:
        return _no_command(f"no *_test.go files in {root}")
    return _est(
        TestCommand(
            argv=("go", "test", "./..."),
            cwd=root,
            evidence=f"{tests[0]} and go.mod",
        )
    )


def _dotnet_command(
    ctx: _Ctx, root: str, manifests: Sequence[str]
) -> Established[TestCommand]:
    for manifest in manifests:
        if manifest.endswith(".csproj") and "Microsoft.NET.Test.Sdk" in (
            ctx.tree.read(manifest) or ""
        ):
            return _est(
                TestCommand(
                    argv=("dotnet", "test"),
                    cwd=root,
                    evidence=f"{manifest} references Microsoft.NET.Test.Sdk",
                )
            )
    return _no_command(
        f"no .csproj in {root} references Microsoft.NET.Test.Sdk"
    )


def _ruby_command(ctx: _Ctx, root: str) -> Established[TestCommand]:
    gemfile = _join(root, "Gemfile")
    own = ctx.files_of.get(root, ())
    if re.search(r"""gem\s+["']rspec""", ctx.tree.read(gemfile) or "") or any(
        f.startswith(_join(root, "spec/")) for f in own
    ):
        return _est(
            TestCommand(
                argv=("bundle", "exec", "rspec"),
                cwd=root,
                evidence=f"{gemfile} or spec/ names rspec",
            )
        )
    if ctx.tree.has(_join(root, "Rakefile")) and any(
        f.startswith(_join(root, "test/")) for f in own
    ):
        return _est(
            TestCommand(
                argv=("rake", "test"),
                cwd=root,
                evidence="Rakefile and a test/ directory",
            )
        )
    return _no_command(f"no rspec in {gemfile} and no Rakefile with test/")


def _test_command(
    ctx: _Ctx, root: str, language: str, manifests: Sequence[str]
) -> Established[TestCommand]:
    """Return the part's test command in the form a profiler wraps."""
    names = {PurePosixPath(m).name for m in manifests}
    candidates: list[tuple[str, Established[TestCommand]]] = []
    if names & {"pyproject.toml", "setup.py", "setup.cfg"}:
        candidates.append(("python", _pytest_command(ctx, root, manifests)))
    if "package.json" in names:
        candidates.append(("javascript", _node_command(ctx, root, manifests)))
    if "Cargo.toml" in names:
        candidates.append(
            (
                "rust",
                _est(
                    TestCommand(
                        argv=("cargo", "test"),
                        cwd=root,
                        evidence=f"{_join(root, 'Cargo.toml')}",
                    )
                ),
            )
        )
    if "go.mod" in names:
        candidates.append(("go", _go_command(ctx, root)))
    if names & {"pom.xml", "build.gradle", "build.gradle.kts"}:
        candidates.append(("java", _jvm_command(ctx, root, manifests)))
    if any(m.endswith((".csproj", ".sln")) for m in manifests):
        candidates.append(("csharp", _dotnet_command(ctx, root, manifests)))
    if "Gemfile" in names:
        candidates.append(("ruby", _ruby_command(ctx, root)))
    if not candidates:
        return _no_command(
            f"no test runner is established for {root}'s manifests "
            "(CMake tests are not detected statically)"
        )
    family = {
        "typescript": "javascript",
        "kotlin": "java",
        "c": "cpp",
    }.get(language, language)
    candidates.sort(key=lambda c: c[0] != family)
    for _, command in candidates:
        if command.value is not None:
            return command
    return candidates[0][1]


# ------------------------------------------------------------- native


def _names(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _literal_strings(node: ast.expr | None) -> list[str]:
    if isinstance(node, ast.List | ast.Tuple):
        return [
            e.value
            for e in node.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    return []


def _setup_py_extensions(
    ctx: _Ctx, directory: str, path: str
) -> list[tuple[str, list[str]]]:
    """Read ``Extension(...)`` calls with literal names from setup.py.

    The file is parsed, never run. An extension built from a variable,
    a glob or ``cythonize`` is not found.
    """
    text = ctx.tree.read(path)
    if text is None:
        return []
    try:
        module = ast.parse(text)
    except SyntaxError:
        return []
    found: list[tuple[str, list[str]]] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not _names(node).endswith(
            "Extension"
        ):
            continue
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        first = node.args[0] if node.args else keywords.get("name")
        if not (
            isinstance(first, ast.Constant) and isinstance(first.value, str)
        ):
            continue
        sources = _literal_strings(
            node.args[1] if len(node.args) > 1 else keywords.get("sources")
        )
        found.append((first.value, [_join(directory, s) for s in sources]))
    return found


def _rs_sources(ctx: _Ctx, cargo_dir: str) -> tuple[str, ...]:
    prefix = _join(cargo_dir, "src/")
    return tuple(
        sorted(
            f
            for f in ctx.tree.files
            if f.startswith(prefix) and f.endswith(".rs")
        )
    )


def _lib_image(name: str, platform: str) -> str:
    return f"lib{name}.{'dylib' if platform == 'darwin' else 'so'}"


def _native_code(
    ctx: _Ctx, root: str, manifests: Sequence[str]
) -> tuple[NativeCode, ...]:
    """Find native code a part builds, with its expected image name."""
    found: list[NativeCode] = []
    tree = ctx.tree
    interpreter = _interpreter(ctx, root)
    own = ctx.files_of.get(root, ())
    seen_cargo: set[str] = set()
    # maturin in pyproject.toml claims its Cargo.toml before it is read.
    by_cargo_last = sorted(
        manifests, key=lambda m: PurePosixPath(m).name == "Cargo.toml"
    )
    for manifest in by_cargo_last:
        name = PurePosixPath(manifest).name
        directory = _dir(manifest)
        if name == "setup.py":
            for ext, sources in _setup_py_extensions(ctx, directory, manifest):
                leaf = ext.rsplit(".", 1)[-1]
                found.append(
                    NativeCode(
                        kind="setuptools_ext",
                        name=ext,
                        own_module=leaf + _ext_suffix(ctx, interpreter),
                        sources=tuple(sources),
                    )
                )
        elif name == "pyproject.toml":
            data = _toml(tree, manifest)
            tool = _dict(data.get("tool"))
            for ext in _list(
                _dict(_dict(tool.get("setuptools"))).get("ext-modules")
            ):
                spec = _dict(ext)
                ext_name = spec.get("name")
                if isinstance(ext_name, str):
                    found.append(
                        NativeCode(
                            kind="setuptools_ext",
                            name=ext_name,
                            own_module=ext_name.rsplit(".", 1)[-1]
                            + _ext_suffix(ctx, interpreter),
                            sources=tuple(
                                _join(directory, s)
                                for s in _strs(spec.get("sources"))
                            ),
                        )
                    )
            maturin = _dict(tool.get("maturin"))
            requires = _strs(_dict(data.get("build-system")).get("requires"))
            if maturin or any(
                r.lower().startswith("maturin") for r in requires
            ):
                cargo = _join(
                    directory, str(maturin.get("manifest-path", "Cargo.toml"))
                )
                if tree.has(cargo):
                    cargo_dir = _dir(cargo)
                    seen_cargo.add(cargo)
                    module = maturin.get("module-name")
                    project = _dict(data.get("project")).get("name")
                    lib = _dict(_dict(_toml(tree, cargo)).get("lib")).get(
                        "name"
                    )
                    label = next(
                        (
                            v
                            for v in (module, lib, project)
                            if isinstance(v, str)
                        ),
                        None,
                    )
                    if label is not None:
                        found.append(
                            NativeCode(
                                kind="maturin",
                                name=label,
                                own_module=_abi3_or(
                                    ctx, cargo, interpreter, label
                                ),
                                sources=_rs_sources(ctx, cargo_dir),
                            )
                        )
        elif name == "Cargo.toml" and manifest not in seen_cargo:
            data = _toml(tree, manifest)
            package = _dict(data.get("package")).get("name")
            lib = _dict(data.get("lib"))
            label = lib.get("name") or package
            crate_types = _strs(lib.get("crate-type"))
            if not isinstance(label, str):
                continue
            label = label.replace("-", "_")
            deps = _dict(data.get("dependencies"))
            if "pyo3" in deps:
                found.append(
                    NativeCode(
                        kind="pyo3",
                        name=label,
                        own_module=_abi3_or(ctx, manifest, interpreter, label),
                        sources=_rs_sources(ctx, directory),
                    )
                )
            elif "cdylib" in crate_types:
                found.append(
                    NativeCode(
                        kind="cargo_cdylib",
                        name=label,
                        own_module=_lib_image(label, ctx.env.platform),
                        sources=_rs_sources(ctx, directory),
                    )
                )
        elif name == "CMakeLists.txt":
            continue
    found += _gyp_targets(ctx, own)
    found += _cgo(ctx, root, own)
    found += _cmake_targets(ctx, own)
    return tuple(found)


def _abi3_or(
    ctx: _Ctx, cargo: str, interpreter: str | None, label: str
) -> str:
    leaf = label.rsplit(".", 1)[-1].replace("-", "_")
    deps = _dict(_toml(ctx.tree, cargo).get("dependencies"))
    features = _strs(_dict(deps.get("pyo3")).get("features"))
    if any(f.startswith("abi3") for f in features):
        return f"{leaf}.abi3.so"
    return leaf + _ext_suffix(ctx, interpreter)


def _gyp_targets(ctx: _Ctx, own: Sequence[str]) -> list[NativeCode]:
    found: list[NativeCode] = []
    for path in own:
        if PurePosixPath(path).name != "binding.gyp":
            continue
        text = ctx.tree.read(path) or ""
        try:
            data = _dict(ast.literal_eval(text.strip()))
        except (ValueError, SyntaxError):
            continue
        for target in _list(data.get("targets")):
            spec = _dict(target)
            name = spec.get("target_name")
            if isinstance(name, str):
                found.append(
                    NativeCode(
                        kind="node_gyp",
                        name=name,
                        own_module=f"{name}.node",
                        sources=tuple(
                            _join(_dir(path), s)
                            for s in _strs(spec.get("sources"))
                        ),
                    )
                )
    return found


def _cgo(ctx: _Ctx, root: str, own: Sequence[str]) -> list[NativeCode]:
    gomod = _join(root, "go.mod")
    if not ctx.tree.has(gomod):
        return []
    sources = [
        f
        for f in own
        if f.endswith(".go")
        and not f.endswith("_test.go")
        and _GO_CGO.search(ctx.tree.read(f) or "")
    ]
    if not sources:
        return []
    module = re.search(r"^module\s+(\S+)", ctx.tree.read(gomod) or "", re.M)
    name = (
        module.group(1).rstrip("/").rsplit("/", 1)[-1]
        if module
        else PurePosixPath(root).name
    )
    dirs = {_dir(s) for s in sources}
    extra = [
        f
        for f in own
        if _dir(f) in dirs and PurePosixPath(f).suffix in _C_SOURCE
    ]
    return [
        NativeCode(
            kind="cgo",
            name=name,
            own_module=name,
            sources=tuple(sorted({*sources, *extra})),
        )
    ]


def _cmake_targets(ctx: _Ctx, own: Sequence[str]) -> list[NativeCode]:
    found: list[NativeCode] = []
    for path in own:
        if PurePosixPath(path).name != "CMakeLists.txt":
            continue
        text = ctx.tree.read(path) or ""
        for kind, name, rest in _CMAKE_TARGET.findall(text):
            if "$" in name:
                continue
            words = rest.split()
            library = kind.lower() == "library"
            if library:
                shared = next(
                    (w for w in words[:1] if w in {"SHARED", "MODULE"}), None
                )
                if shared is None:
                    continue
                suffix = (
                    "dylib"
                    if ctx.env.platform == "darwin" and shared == "SHARED"
                    else "so"
                )
                image = f"lib{name}.{suffix}"
            else:
                if words and words[0] in {"IMPORTED", "ALIAS"}:
                    continue
                image = name
            sources = tuple(
                _join(_dir(path), w)
                for w in words
                if "$" not in w and PurePosixPath(w).suffix in _C_SOURCE
            )
            found.append(
                NativeCode(
                    kind="cmake", name=name, own_module=image, sources=sources
                )
            )
    return found


# --------------------------------------------------------- benchmarks


def _benchmarks(
    ctx: _Ctx, root: str, manifests: Sequence[str]
) -> tuple[ExistingBenchmark, ...]:
    """Find existing benchmark suites of the part."""
    tree = ctx.tree
    own = ctx.files_of.get(root, ())
    found: list[ExistingBenchmark] = []
    interpreter = _interpreter(ctx, root)
    texts = [tree.read(m) or "" for m in manifests]
    texts.extend(
        tree.read(_join(root, n)) or ""
        for n in ("Makefile", "tox.ini", "noxfile.py")
        if tree.has(_join(root, n))
    )
    py_manifest = any(
        PurePosixPath(m).name in {"pyproject.toml", "setup.py", "setup.cfg"}
        for m in manifests
    )
    if py_manifest and interpreter is not None:
        reqs = [
            tree.read(f) or ""
            for f in own
            if re.fullmatch(r"requirements.*\.txt", PurePosixPath(f).name)
        ]
        declared = re.search(
            r"pytest[-_]benchmark", "\n".join([*texts, *reqs]), re.I
        )
        used = any(
            _PY_BENCH_FIXTURE.search(tree.read(f) or "")
            for f in own
            if PurePosixPath(f).name.startswith("test_")
            or PurePosixPath(f).name.endswith("_test.py")
        )
        if declared or used:
            found.append(
                ExistingBenchmark(
                    kind="pytest_benchmark",
                    name="pytest-benchmark",
                    argv=(
                        interpreter,
                        "-m",
                        "pytest",
                        "--benchmark-only",
                        "-q",
                        "-p",
                        "no:cacheprovider",
                    ),
                    cwd=root,
                )
            )
        asv_dir = next(
            (
                d
                for d in (".", "asv_bench")
                if tree.has(
                    _join(
                        _join(root, d) if d != "." else root, "asv.conf.json"
                    )
                )
            ),
            None,
        )
        if asv_dir is not None:
            found.append(
                ExistingBenchmark(
                    kind="asv",
                    name="asv",
                    argv=(interpreter, "-m", "asv", "run"),
                    cwd=root if asv_dir == "." else _join(root, asv_dir),
                )
            )
    for manifest in manifests:
        if PurePosixPath(manifest).name == "Cargo.toml":
            found += _cargo_benches(ctx, root, manifest, own)
    found += _go_benches(ctx, root, own)
    found += _script_benchmarks(ctx, root, manifests, own, texts, interpreter)
    return tuple(found)


def _cargo_benches(
    ctx: _Ctx, root: str, manifest: str, own: Sequence[str]
) -> list[ExistingBenchmark]:
    declared = [
        n
        for b in _list(_toml(ctx.tree, manifest).get("bench"))
        if isinstance(n := _dict(b).get("name"), str)
    ]
    prefix = _join(_dir(manifest), "benches/")
    stems = [
        PurePosixPath(f).stem
        for f in own
        if f.startswith(prefix)
        and f.endswith(".rs")
        and f.count("/") == prefix.count("/")
    ]
    return [
        ExistingBenchmark(
            kind="cargo_bench",
            name=name,
            argv=("cargo", "bench", "--bench", name),
            cwd=root,
        )
        for name in sorted({*declared, *stems})
    ]


def _go_benches(
    ctx: _Ctx, root: str, own: Sequence[str]
) -> list[ExistingBenchmark]:
    dirs = sorted(
        {
            _dir(f)
            for f in own
            if f.endswith("_test.go")
            and _GO_BENCH.search(ctx.tree.read(f) or "")
        }
    )
    found: list[ExistingBenchmark] = []
    for directory in dirs:
        rel = (
            "."
            if directory == root
            else str(PurePosixPath(directory).relative_to(root))
            if root != "."
            else directory
        )
        label = (
            "go-bench" if rel == "." else "go-bench-" + rel.replace("/", ".")
        )
        found.append(
            ExistingBenchmark(
                kind="go_bench",
                name=re.sub(r"[^A-Za-z0-9._-]", "-", label),
                argv=("go", "test", "-run=^$", "-bench=.", f"./{rel}"),
                cwd=root,
            )
        )
    return found


def _script_benchmarks(
    ctx: _Ctx,
    root: str,
    manifests: Sequence[str],
    own: Sequence[str],
    texts: Sequence[str],
    interpreter: str | None,
) -> list[ExistingBenchmark]:
    found: list[ExistingBenchmark] = []
    package = _join(root, "package.json")
    if package in manifests:
        scripts = _dict(_json(ctx.tree, package).get("scripts"))
        for key, value in sorted(scripts.items()):
            if re.search(r"bench", f"{key} {value}", re.I) and key != "test":
                found.append(
                    ExistingBenchmark(
                        kind="script",
                        name=re.sub(r"[^A-Za-z0-9._-]", "-", key),
                        argv=("npm", "run", key),
                        cwd=root,
                    )
                )
    joined = "\n".join(texts)
    for path in own:
        base = PurePosixPath(path).name
        if not (
            re.match(r"bench", base, re.I) and base.endswith(_SCRIPT_SUFFIXES)
        ):
            continue
        if base not in joined:
            continue
        rel = (
            path if root == "." else str(PurePosixPath(path).relative_to(root))
        )
        if base.endswith(".py"):
            if interpreter is None:
                continue
            argv: tuple[str, ...] = (interpreter, rel)
        elif base.endswith(".sh"):
            argv = ("sh", rel)
        else:
            argv = ("node", rel)
        found.append(
            ExistingBenchmark(
                kind="script",
                name=re.sub(r"[^A-Za-z0-9._-]", "-", PurePosixPath(base).stem),
                argv=argv,
                cwd=root,
            )
        )
    return found


# -------------------------------------------------------- entry points


def _module_file(ctx: _Ctx, root: str, module: str) -> str | None:
    rel = module.replace(".", "/")
    for base in (_join(root, "src"), root):
        for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
            path = _join(base, candidate)
            if ctx.tree.has(path):
                return path
    return None


def _console_scripts(
    ctx: _Ctx, root: str, manifests: Sequence[str]
) -> list[EntryPoint]:
    specs: list[tuple[str, str, str]] = []
    for manifest in manifests:
        name = PurePosixPath(manifest).name
        if name == "pyproject.toml":
            data = _toml(ctx.tree, manifest)
            project = _dict(data.get("project"))
            poetry = _dict(_dict(_dict(data.get("tool")).get("poetry")))
            for table in (project.get("scripts"), poetry.get("scripts")):
                for key, value in _dict(table).items():
                    if isinstance(value, str):
                        specs.append((key, value, manifest))
        elif name == "setup.cfg":
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read_string(ctx.tree.read(manifest) or "")
            except configparser.Error:
                continue
            raw = parser.get(
                "options.entry_points", "console_scripts", fallback=""
            )
            for line in raw.splitlines():
                key, _, value = line.partition("=")
                if key.strip() and value.strip():
                    specs.append((key.strip(), value.strip(), manifest))
        elif name == "setup.py":
            specs += _setup_py_scripts(ctx, manifest)
    found: list[EntryPoint] = []
    for key, value, manifest in specs:
        module, _, function = value.partition(":")
        file = _module_file(ctx, root, module.strip())
        line = None
        if file is not None and function.strip():
            line = _line_of(
                ctx.tree.read(file) or "",
                rf"^(?:async\s+)?def\s+{re.escape(function.strip().split('.')[0])}\b",
            )
        found.append(
            EntryPoint(
                kind="console_script",
                name=key,
                file=file or manifest,
                line=line,
            )
        )
    return found


def _setup_py_scripts(ctx: _Ctx, path: str) -> list[tuple[str, str, str]]:
    try:
        module = ast.parse(ctx.tree.read(path) or "")
    except SyntaxError:
        return []
    found: list[tuple[str, str, str]] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "entry_points" or not isinstance(kw.value, ast.Dict):
                continue
            for key, value in zip(kw.value.keys, kw.value.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "console_scripts"
                ):
                    for item in _literal_strings(value):
                        name, _, target = item.partition("=")
                        found.append((name.strip(), target.strip(), path))
    return found


def _public_modules(ctx: _Ctx, root: str) -> list[EntryPoint]:
    """List the top-level public modules of each Python package."""
    found: list[EntryPoint] = []
    own = ctx.files_of.get(root, ())
    bases = (_join(root, "src"), root)
    packages = sorted(
        {
            f.rsplit("/", 1)[0]
            for f in own
            if PurePosixPath(f).name == "__init__.py"
            and str(PurePosixPath(f).parent.parent) in bases
            and PurePosixPath(f).parent.name[:1] not in {"_", "."}
            and PurePosixPath(f).parent.name
            not in {"tests", "test", "docs", "examples"}
        }
    )
    for package in packages:
        label = PurePosixPath(package).name
        for path in own:
            pure = PurePosixPath(path)
            if str(pure.parent) == package and pure.suffix == ".py":
                if pure.name.startswith("_"):
                    continue
                found.append(
                    EntryPoint(
                        kind="public_module",
                        name=f"{label}.{pure.stem}",
                        file=path,
                        line=None,
                    )
                )
            elif (
                pure.name == "__init__.py"
                and str(pure.parent.parent) == package
                and not pure.parent.name.startswith("_")
            ):
                found.append(
                    EntryPoint(
                        kind="public_module",
                        name=f"{label}.{pure.parent.name}",
                        file=path,
                        line=None,
                    )
                )
    return found


def _routes(ctx: _Ctx, root: str) -> list[EntryPoint]:
    """List route declarations, scanned as `detect` scans them."""
    found: list[EntryPoint] = []
    scanned = [f for f in ctx.files_of.get(root, ()) if f.endswith(".py")]
    for path in scanned[:MAX_SCANNED_PY]:
        for number, line in enumerate(
            (ctx.tree.read(path) or "").splitlines(), start=1
        ):
            if _ROUTE_LINE.match(line):
                found.append(
                    EntryPoint(
                        kind="route",
                        name=line.strip(),
                        file=path,
                        line=number,
                    )
                )
    return found


def _other_entries(
    ctx: _Ctx, root: str, manifests: Sequence[str]
) -> list[EntryPoint]:
    found: list[EntryPoint] = []
    own = ctx.files_of.get(root, ())
    for manifest in manifests:
        name = PurePosixPath(manifest).name
        directory = _dir(manifest)
        if name == "package.json":
            data = _json(ctx.tree, manifest)
            bins = data.get("bin")
            if isinstance(bins, str) and isinstance(data.get("name"), str):
                bins = {str(data["name"]).rsplit("/", 1)[-1]: bins}
            for key, value in _dict(bins).items():
                if isinstance(value, str):
                    found.append(
                        EntryPoint(
                            kind="bin",
                            name=key,
                            file=_join(directory, str(PurePosixPath(value))),
                            line=None,
                        )
                    )
        elif name == "Cargo.toml":
            data = _toml(ctx.tree, manifest)
            package = _dict(data.get("package")).get("name")
            declared = {
                str(_dict(b).get("name")): str(
                    _dict(b).get("path")
                    or f"src/bin/{_dict(b).get('name')}.rs"
                )
                for b in _list(data.get("bin"))
                if isinstance(_dict(b).get("name"), str)
            }
            main = _join(directory, "src/main.rs")
            if ctx.tree.has(main) and isinstance(package, str):
                declared.setdefault(package, "src/main.rs")
            bin_prefix = _join(directory, "src/bin/")
            for f in own:
                if (
                    f.startswith(bin_prefix)
                    and f.endswith(".rs")
                    and f.count("/") == bin_prefix.count("/")
                ):
                    declared.setdefault(
                        PurePosixPath(f).stem,
                        f[len(directory) + 1 :] if directory != "." else f,
                    )
            for key, rel in sorted(declared.items()):
                path = _join(directory, rel)
                found.append(
                    EntryPoint(
                        kind="bin",
                        name=key,
                        file=path,
                        line=_line_of(
                            ctx.tree.read(path) or "",
                            r"^[ \t]*(?:pub\s+)?fn\s+main\b",
                        ),
                    )
                )
        elif name == "go.mod":
            for f in own:
                if (
                    f.endswith(".go")
                    and not f.endswith("_test.go")
                    and _GO_MAIN_PKG.search(ctx.tree.read(f) or "")
                ):
                    parent = _dir(f)
                    found.append(
                        EntryPoint(
                            kind="main_package",
                            name=PurePosixPath(
                                root if parent == "." else parent
                            ).name
                            if (root != "." or parent != ".")
                            else "main",
                            file=f,
                            line=_line_of(
                                ctx.tree.read(f) or "", r"^func\s+main\s*\("
                            ),
                        )
                    )
    return found


def _entry_points(
    ctx: _Ctx, root: str, language: str, manifests: Sequence[str]
) -> tuple[EntryPoint, ...]:
    found = _console_scripts(ctx, root, manifests)
    found += _other_entries(ctx, root, manifests)
    if language == "python":
        found += _routes(ctx, root)
        found += _public_modules(ctx, root)
    return tuple(
        sorted(found, key=lambda e: (e.kind, e.name, e.file, e.line or 0))
    )


# ------------------------------------------------------------ tooling


def _recorder(env: _Env) -> ToolStatus:
    """Say whether the native recorder can run here.

    Follows `perfproof.profilers.native`: ``sample`` on macOS, ``perf``
    elsewhere, and on Linux one ``perf stat`` to see whether
    ``perf_event_open`` is allowed.
    """
    cached = env.cache.get("recorder")
    if isinstance(cached, ToolStatus):
        return cached
    if env.platform == "darwin":
        found = env.which("sample")
        status = ToolStatus(
            name="sample",
            available=found is not None,
            reason=None if found else "sample was not found on the PATH",
        )
    elif env.platform.startswith("linux"):
        found = env.which("perf")
        if found is None:
            status = ToolStatus(
                name="perf", available=False, reason="perf was not found"
            )
        else:
            ran = env.run([found, "stat", "-e", "cpu-clock", "--", "true"])
            if isinstance(ran, str):
                status = ToolStatus(name="perf", available=False, reason=ran)
            elif ran[0] != 0:
                first = ran[1].splitlines()[0] if ran[1] else "no output"
                status = ToolStatus(
                    name="perf",
                    available=False,
                    reason=f"perf_event_open refused: {first}",
                )
            else:
                status = ToolStatus(name="perf", available=True, reason=None)
    else:
        status = ToolStatus(
            name="none",
            available=False,
            reason=f"no native recorder is wired for {env.platform}",
        )
    env.cache["recorder"] = status
    return status


def _binary(env: _Env, name: str, hint: str) -> ToolStatus:
    found = env.which(name)
    return ToolStatus(
        name=name,
        available=found is not None,
        reason=None if found else f"{name} is not on the PATH; {hint}",
    )


def _profiler(env: _Env, language: str) -> ToolStatus:
    """Say whether the language's profiler binary is on this machine."""
    if language == "python":
        try:
            chosen = default_profiler(env.platform)
        except Exception as exc:
            return ToolStatus(name="none", available=False, reason=str(exc))
        if chosen == "py-spy":
            return _binary(
                env, "py-spy", "install it with `uv tool install py-spy`"
            )
        return _binary(
            env, "uv", "perfproof runs pyinstrument through `uv run --with`"
        )
    if language in {"javascript", "typescript"}:
        return _binary(env, "node", "install Node")
    if language in {"java", "kotlin"}:
        java = env.which("java")
        jfr = env.which("jfr")
        reason = None
        if java is None:
            reason = "java is not on the PATH; a JDK is needed for JFR"
        elif jfr is None:
            reason = (
                "jfr is not on the PATH; the JDK's jfr tool prints recordings"
            )
        return ToolStatus(name="jfr", available=reason is None, reason=reason)
    if language == "go":
        return _binary(env, "go", "install Go")
    if language in {"rust", "c", "cpp"}:
        return _recorder(env)
    if language == "csharp":
        return _binary(
            env, "dotnet-trace", "`dotnet tool install -g dotnet-trace`"
        )
    if language == "ruby":
        ruby = env.which("ruby")
        if ruby is None:
            return ToolStatus(
                name="stackprof",
                available=False,
                reason="ruby is not on the PATH",
            )
        ran = env.run([ruby, "-e", "require 'stackprof'"])
        ok = not isinstance(ran, str) and ran[0] == 0
        return ToolStatus(
            name="stackprof",
            available=ok,
            reason=None
            if ok
            else "the stackprof gem is not installed for this Ruby",
        )
    return ToolStatus(
        name="none",
        available=False,
        reason=f"no profiler is known for language {language}",
    )


def _clang_tools(env: _Env) -> tuple[bool, str | None]:
    if env.platform == "darwin" and env.which("xcrun"):
        for tool in ("llvm-cov", "llvm-profdata"):
            ran = env.run(["xcrun", "-f", tool])
            if isinstance(ran, str) or ran[0] != 0:
                return False, f"`xcrun -f {tool}` found nothing"
        return True, None
    missing = [t for t in ("llvm-cov", "llvm-profdata") if not env.which(t)]
    return not missing, (
        f"{', '.join(missing)} not on the PATH" if missing else None
    )


def _jacoco_argv() -> tuple[str, ...]:
    v = JACOCO_VERSION
    agent = f"org.jacoco.agent/{v}/org.jacoco.agent-{v}-runtime.jar"
    cli = f"org.jacoco.cli/{v}/org.jacoco.cli-{v}-nodeps.jar"
    return (
        "curl",
        "-fL",
        "--create-dirs",
        "--output-dir",
        str(JACOCO_DIR),
        "-O",
        f"{_MAVEN}/{agent}",
        "-O",
        f"{_MAVEN}/{cli}",
    )


def _coverage(
    ctx: _Ctx, root: str, language: str, native: Sequence[NativeCode]
) -> tuple[
    Literal["not_needed", "present", "missing"], tuple[str, ...] | None
]:
    """Return the coverage tool's state and its install argv."""
    env = ctx.env
    effective = language
    if language == "python":
        kinds = {n.kind for n in native}
        if kinds & {"setuptools_ext", "cmake", "node_gyp"}:
            effective = "cpp"
        elif kinds & {"pyo3", "maturin", "cargo_cdylib"}:
            effective = "rust"
        else:
            return "not_needed", None
    if effective in {"javascript", "typescript"}:
        found = _node_bin(ctx, root, "c8") is not None
        return (
            ("present", None)
            if found
            else (
                "missing",
                ("npm", "install", "--no-save", "--ignore-scripts", "c8"),
            )
        )
    if effective in {"java", "kotlin"}:
        has_jars = bool(
            list(JACOCO_DIR.glob("org.jacoco.agent-*-runtime.jar"))
            and list(JACOCO_DIR.glob("org.jacoco.cli-*-nodeps.jar"))
        )
        return ("present", None) if has_jars else ("missing", _jacoco_argv())
    if effective == "rust":
        cargo = env.which("cargo")
        if cargo is not None:
            ran = env.run([cargo, "llvm-cov", "--version"])
            if not isinstance(ran, str) and ran[0] == 0:
                return "present", None
        return "missing", ("cargo", "install", "cargo-llvm-cov", "--locked")
    if effective == "go":
        return ("present", None) if env.which("go") else ("missing", None)
    if effective in {"c", "cpp"}:
        ok, _ = _clang_tools(env)
        if ok:
            return "present", None
        install = (
            ("xcode-select", "--install") if env.platform == "darwin" else None
        )
        return "missing", install
    if effective == "csharp":
        refs = any(
            "coverlet.collector" in (ctx.tree.read(f) or "")
            for f in ctx.files_of.get(root, ())
            if f.endswith(".csproj")
        )
        return ("present", None) if refs else ("missing", None)
    if effective == "ruby":
        gemfile = ctx.tree.read(_join(root, "Gemfile")) or ""
        return (
            ("present", None) if "simplecov" in gemfile else ("missing", None)
        )
    return "missing", None


# ----------------------------------------------------------- the entry


def detect_parts(
    root: Path,
    model: ProjectModel,
    *,
    python: Path | None = None,
    path: str | None = None,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[Part, ...]:
    """Split a repository into parts and read what each one offers.

    Parameters
    ----------
    root : pathlib.Path
        Repository root. Nothing under it is written, and nothing of
        the project is run; only availability probes (each an argv
        list with a 10 s timeout) are.
    model : ProjectModel
        The project model; a part's ``target_id`` is one of its targets.
    python : pathlib.Path, optional
        The project's interpreter. Default: ``.venv/bin/python`` of the
        part, then of the repository.
    path : str, optional
        ``PATH`` the probes search; the process's when omitted.
    platform : str, optional
        A `sys.platform` value; this one when omitted.
    environ : mapping, optional
        Where ``VIRTUAL_ENV`` is read; the process's when omitted.

    Returns
    -------
    tuple of Part
        One per directory with a manifest, sorted by root. Anything
        that could not be established is an `Established` with
        ``value`` ``None`` and a reason. Cost is linear in the files the
        walk visits (at most `perfproof.project.detect.MAX_FILES`),
        plus at most one pass of reading per part for each detector.
    """
    with tempfile.TemporaryDirectory(prefix="perfproof-probe-") as neutral:
        env = _Env(
            root=root,
            path=path if path is not None else os.environ.get("PATH", ""),
            platform=platform if platform is not None else sys.platform,
            python=python,
            neutral=Path(neutral),
            environ=os.environ if environ is None else environ,
        )
        return _detect(env, model)


def _detect(env: _Env, model: ProjectModel) -> tuple[Part, ...]:
    """Build the parts once the probe environment is set up."""
    root = env.root
    walked = _walk_tree(root, COUNT_DEPTH)
    manifest_map = _manifest_dirs(walked)
    roots = tuple(sorted(manifest_map))
    rootset = frozenset(roots)
    groups: dict[str, str] = {}
    for group, files in _count_sources(walked).items():
        for f in files:
            groups[f] = _file_language(f, group)
    owned: dict[str, list[str]] = {r: [] for r in roots}
    for f in walked.files:
        owner = _owner(f, rootset) if "/" in f else None
        if owner is None and "." in rootset:
            owner = "."
        if owner is not None:
            owned[owner].append(f)
    ctx = _Ctx(
        env=env,
        tree=walked,
        model=model,
        roots=roots,
        files_of={r: tuple(v) for r, v in owned.items()},
        languages=groups,
    )
    taken: set[str] = set()
    parts: list[Part] = []
    for directory in roots:
        manifests = manifest_map[directory]
        language = _part_language(ctx.files_of[directory], groups)
        ordered = _order_manifests(manifests, language)
        target_id, target_class = _target_of(ctx, directory, language)
        native = _native_code(ctx, directory, ordered)
        tool_language = _tool_language(language, ordered)
        coverage, install = _coverage(ctx, directory, tool_language, native)
        parts.append(
            Part(
                id=part_id(directory, taken),
                root=directory,
                manifests=ordered,
                target_id=target_id,
                language=language,
                target_class=target_class,
                test_command=_test_command(ctx, directory, language, ordered),
                native=native,
                benchmarks=_benchmarks(ctx, directory, ordered),
                entry_points=_entry_points(ctx, directory, language, ordered),
                tooling=PartTooling(
                    profiler=_profiler(env, tool_language),
                    native_recorder=_recorder(env),
                    coverage=coverage,
                    coverage_install=install,
                ),
            )
        )
    return tuple(parts)
