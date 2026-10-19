"""Detect target classes and candidate workloads, with their evidence.

`detect` reads a repository and returns a `Proposal`: target entries,
candidate workload entries, an all-unknown goal per workload and one
project-wide how-to-run entry, every one ``proposed``. It is read-only
(it opens files and walks directories) except for the two opt-in steps
below, and nothing it finds is measured: BS-5 of ``docs/skill.md``
holds, because a proposed workload is never run to produce a number.

What it looks at:

- manifests: ``pyproject.toml``, ``setup.py``, ``setup.cfg``,
  ``requirements*.txt``, ``package.json``, ``Makefile``,
  ``CMakeLists.txt``, ``Cargo.toml``, ``meson.build``;
- existing benchmark suites: ``bench*.py`` scripts, a ``benchmarks``
  package with a ``__main__``, ``asv.conf.json``, a ``Makefile`` bench
  target, Cargo ``benches/``, ``package.json`` bench scripts;
- the slowest tests, from a JUnit XML file already in the tree, or
  from one pytest run with ``slow_tests=True`` (opt-in, because it
  executes the project's test suite);
- entry points: ``[project.scripts]`` and ``__main__`` packages;
- routes (decorators of the common Python web frameworks), serving
  configs (vLLM, SGLang, TGI launch lines) and CUDA, Triton and LLM
  client usage, as evidence for the other target classes.

A class with no runnable workload evidence (a web service's route
list, a serving config) gets a target and a question, never a
workload with an invented command. A command whose arguments cannot
be established is not proposed.

The walk skips VCS, virtualenv, build and cache directories, and
perfproof's own candidate trees, to a bounded depth and file count;
when it stops early the proposal says so in ``notes``.
"""

import os
import re
import subprocess
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from pathlib import Path, PurePosixPath
from typing import Final, cast

from perfproof.project.provenance import (
    LOCKFILES,
    file_hashes,
    make_provenance,
)
from perfproof.schema import (
    GoalEntry,
    HowToRunEntry,
    Provenance,
    TargetClass,
    TargetEntry,
    WorkloadEntry,
    WorkloadSource,
)

__all__ = ["Proposal", "Question", "detect", "slug"]

SKIP_DIRS: Final = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".perfproof",
        "candidates",
        "build",
        "dist",
        "target",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".hypothesis",
        "site-packages",
        ".claude",
        ".idea",
        ".eggs",
        "scratch",
    }
)
"""Directories the walk never enters. ``candidates`` holds perfproof's
own candidate trees (``examples/README.md``), copies of the project."""
MAX_DEPTH: Final = 5
MAX_FILES: Final = 50_000
MAX_READ_BYTES: Final = 256 * 1024
MAX_SCANNED_PY: Final = 600
SLOW_TESTS: Final = 3
"""How many of the slowest tests are proposed."""
PYTEST_TIMEOUT_SECONDS: Final = 600.0

_SOURCE_EXT: Final = frozenset(
    {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".rs", ".zig"}
)
_CUDA_EXT: Final = frozenset({".cu", ".cuh"})
_NATIVE_MANIFESTS: Final = frozenset(
    {"Makefile", "CMakeLists.txt", "Cargo.toml", "meson.build"}
)
_BENCH_PACKAGES: Final = frozenset(
    {"benchmarks", "benchmark", "bench", "asv_bench"}
)
_NOT_ENTRY_DIRS: Final = frozenset({"tests", "test", "docs", "examples"})
_WEB_DEPS: Final = frozenset(
    {
        "fastapi",
        "flask",
        "django",
        "starlette",
        "aiohttp",
        "sanic",
        "tornado",
        "litestar",
        "falcon",
        "bottle",
        "express",
        "fastify",
        "koa",
        "next",
        "nestjs",
        "@nestjs/core",
    }
)
_SERVING_DEPS: Final = frozenset(
    {"vllm", "sglang", "tensorrt-llm", "llama-cpp-python", "lmdeploy"}
)
_GPU_DEPS: Final = frozenset({"torch", "triton", "cupy", "pycuda", "jax"})
_AGENT_DEPS: Final = frozenset(
    {
        "anthropic",
        "openai",
        "langchain",
        "langgraph",
        "llama-index",
        "crewai",
        "autogen",
        "pydantic-ai",
        "litellm",
        "google-genai",
        "google-generativeai",
        "mcp",
        "@anthropic-ai/sdk",
    }
)
_ROUTE: Final = re.compile(
    r"^\s*@\w+(?:\.\w+)*\.(?:get|post|put|delete|patch|route|api_route|"
    r"websocket)\("
)
_SERVING_LINE: Final = re.compile(
    r"vllm serve|-m vllm\.|vllm\.entrypoints|sglang\.launch_server|"
    r"text-generation-launcher|trtllm-serve"
)
_SERVING_NAME: Final = re.compile(
    r"^(?:vllm|sglang|tgi|serving)[-_.].*\.(?:ya?ml|json|toml)$"
)
_GPU_SOURCE: Final = re.compile(r"@triton\.jit|__global__|cpp_extension")
_BENCH_PY: Final = re.compile(
    r"^(?:bench(?:mark)?s?(?:[-_].*)?|.*[-_]bench(?:mark)?)\.py$"
)
_MAKE_BENCH: Final = re.compile(
    r"^(bench|benchmark|benchmarks|perf)\s*:", re.M
)
_REQ_NAME: Final = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9._/@-]*")
_JUNIT_NAME: Final = re.compile(r"junit|test[-_]?results?", re.I)
_TESTCASE: Final = re.compile(r"<testcase\b([^>]*)>")
_ATTR: Final = re.compile(r'(\w+)="([^"]*)"')
_DURATION: Final = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s+call\s+(\S+)\s*$", re.M)
_PY_GOAL_FIELDS: Final = ("metric", "direction", "unit", "bar", "guardrails")

_CLASS_QUESTION: Final[Mapping[str, str]] = {
    "python_cpu": "Which command represents real use of the Python code?",
    "native": (
        "Which command and arguments represent real use of the built "
        "program? Its arguments could not be established from the tree."
    ),
    "web_service": (
        "Which request mix represents real use of the service? Routes "
        "were found, but a service workload needs a load definition."
    ),
    "llm_serving": (
        "Which model, server command and benchmark command represent "
        "real use of the serving setup?"
    ),
    "gpu_kernel": (
        "Which script or kernel benchmark represents real use of the GPU code?"
    ),
    "agentic": (
        "Which recorded run of the app's model and tool calls represents "
        "real use?"
    ),
}


type _PytestRun = tuple[list[tuple[str, float]], list[str], tuple[str, ...]]
"""Durations found, notes, and the argv that was run (empty if none)."""


@dataclass(frozen=True)
class Question:
    """Something perfproof could not establish and asks the developer.

    Attributes
    ----------
    id : str
        Stable identifier, such as ``goal:bench-tile``.
    entry : str or None
        The entry the answer belongs to, when there is one.
    text : str
        The question.
    """

    id: str
    entry: str | None
    text: str


@dataclass(frozen=True)
class Proposal:
    """What `detect` found.

    Attributes
    ----------
    targets, workloads, goals, how_to_run : tuple
        Entries, all ``proposed`` at version 1. ``goals`` has one
        entry per workload with every field unknown.
    evidence : mapping of str to tuple of str
        Human-readable evidence per entry ID. Files and commands are in
        each entry's provenance; this is the sentence that explains
        them.
    questions : tuple of Question
        What is unknown and must be asked.
    notes : tuple of str
        Limits of this run (walk stopped early, a probe that failed).
    caveats : mapping of str to str
        Per entry ID, what was guessed or left open in its command (an
        assumed interpreter, arguments not established). Written as the
        note of the entry's ``created`` history line.
    """

    targets: tuple[TargetEntry, ...] = ()
    workloads: tuple[WorkloadEntry, ...] = ()
    goals: tuple[GoalEntry, ...] = ()
    how_to_run: tuple[HowToRunEntry, ...] = ()
    evidence: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict[str, tuple[str, ...]]
    )
    questions: tuple[Question, ...] = ()
    notes: tuple[str, ...] = ()
    caveats: Mapping[str, str] = field(default_factory=dict[str, str])


def slug(text: str) -> str:
    """Make an entry ID component: letters, digits, ``.``, ``_``, ``-``.

    Parameters
    ----------
    text : str
        Any text, such as a file stem.

    Returns
    -------
    str
        Lowercase, never empty, starting with a letter or digit.
    """
    out = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-._")
    return out or "workload"


@dataclass
class _Tree:
    """The files of a repository, as relative POSIX paths."""

    root: Path
    files: list[str]
    truncated: bool

    def named(self, name: str) -> list[str]:
        return [f for f in self.files if PurePosixPath(f).name == name]

    def has(self, path: str) -> bool:
        return path in self._set

    @property
    def _set(self) -> frozenset[str]:
        return frozenset(self.files)

    def read(self, path: str) -> str | None:
        """Return up to `MAX_READ_BYTES` of a text file, or ``None``."""
        try:
            with (self.root / path).open("rb") as handle:
                data = handle.read(MAX_READ_BYTES)
        except OSError:
            return None
        return data.decode("utf-8", errors="replace")


def _walk(root: Path) -> _Tree:
    """List files under ``root``, sorted, within the walk's bounds."""
    files: list[str] = []
    truncated = False

    def visit(directory: Path, rel: str, depth: int) -> None:
        nonlocal truncated
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if len(files) >= MAX_FILES:
                truncated = True
                return
            path = f"{rel}{entry.name}"
            if entry.is_dir(follow_symlinks=False):
                if entry.name in SKIP_DIRS or entry.name.endswith(".egg-info"):
                    continue
                if depth >= MAX_DEPTH:
                    truncated = True
                    continue
                visit(Path(entry.path), path + "/", depth + 1)
            elif entry.is_file(follow_symlinks=False):
                files.append(path)

    visit(root, "", 0)
    return _Tree(root, files, truncated)


def _dir(path: str) -> str:
    """Return the directory of a relative path, ``.`` for the root."""
    return str(PurePosixPath(path).parent)


def _join(directory: str, name: str) -> str:
    return name if directory == "." else f"{directory}/{name}"


def _dep_name(spec: str) -> str | None:
    match = _REQ_NAME.match(spec.strip())
    if match is None:
        return None
    return (
        re.split(r"[<>=!~\[;@ ]", match.group(0), maxsplit=1)[0]
        .lower()
        .replace("_", "-")
    )


def _toml(tree: _Tree, path: str) -> dict[str, object]:
    text = tree.read(path)
    if text is None:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in cast("dict[str, object]", value).values():
            yield from _strings(item)


def _dependencies(tree: _Tree) -> dict[str, list[str]]:
    """Map each dependency name to the files that declare it."""
    found: dict[str, list[str]] = defaultdict(list)

    def add(name: str | None, path: str) -> None:
        if name and path not in found[name]:
            found[name].append(path)

    for path in tree.files:
        base = PurePosixPath(path).name
        if base == "pyproject.toml":
            data = _toml(tree, path)
            project = cast("dict[str, object]", data.get("project") or {})
            for spec in _strings(project.get("dependencies")):
                add(_dep_name(spec), path)
            for spec in _strings(project.get("optional-dependencies")):
                add(_dep_name(spec), path)
            for spec in _strings(data.get("dependency-groups")):
                add(_dep_name(spec), path)
            poetry = cast(
                "dict[str, object]",
                cast("dict[str, object]", data.get("tool") or {}).get("poetry")
                or {},
            )
            deps = poetry.get("dependencies")
            if isinstance(deps, dict):
                for name in cast("dict[str, object]", deps):
                    add(_dep_name(name), path)
        elif re.fullmatch(r"requirements.*\.txt", base):
            for line in (tree.read(path) or "").splitlines():
                if line.strip() and not line.lstrip().startswith(("#", "-")):
                    add(_dep_name(line), path)
        elif base == "package.json" and "/node_modules/" not in path:
            text = tree.read(path) or ""
            for key in ("dependencies", "devDependencies"):
                block = re.search(rf'"{key}"\s*:\s*\{{([^}}]*)\}}', text)
                if block:
                    for name in re.findall(r'"([^"]+)"\s*:', block.group(1)):
                        add(name.lower(), path)
    return found


def _scan_python(tree: _Tree) -> tuple[list[str], list[str], list[str]]:
    """Scan Python files for routes, GPU kernels and test-looking files.

    Returns
    -------
    routes, gpu, py_files : lists of ``path:line`` or paths
    """
    routes: list[str] = []
    gpu: list[str] = []
    py = [f for f in tree.files if f.endswith(".py")]
    for path in py[:MAX_SCANNED_PY]:
        text = tree.read(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _ROUTE.match(line):
                routes.append(f"{path}:{number}")
            if _GPU_SOURCE.search(line) and path not in gpu:
                gpu.append(path)
    return routes, gpu, py


class _Builder:
    """Accumulates entries while `detect` runs."""

    def __init__(
        self,
        tree: _Tree,
        now: datetime,
        version: str,
        run_pytest: Callable[[], _PytestRun] | None,
    ) -> None:
        self.tree = tree
        self.now = now
        self.version = version
        self.run_pytest = run_pytest
        self.targets: dict[str, TargetEntry] = {}
        self.workloads: list[WorkloadEntry] = []
        self.evidence: dict[str, tuple[str, ...]] = {}
        self.caveats: dict[str, str] = {}
        self.notes: list[str] = []
        self.used_ids: set[str] = set()
        self.uv = tree.has("uv.lock")

    def unique(self, base: str) -> str:
        candidate, n = base, 2
        while candidate in self.used_ids:
            candidate, n = f"{base}-{n}", n + 1
        self.used_ids.add(candidate)
        return candidate

    def provenance(
        self,
        paths: Sequence[str],
        commands: tuple[tuple[str, ...], ...] = (),
    ) -> Provenance:
        existing = [p for p in paths if self.tree.has(p)]
        return make_provenance(
            file_hashes(self.tree.root, existing),
            commands,
            self.version,
            self.now,
        )

    def lockfiles_for(self, directory: str) -> list[str]:
        """Lockfiles in ``directory`` and every directory above it."""
        found: list[str] = []
        parts = [] if directory == "." else directory.split("/")
        for depth in range(len(parts) + 1):
            base = "/".join(parts[:depth]) or "."
            found += [
                _join(base, n)
                for n in sorted(LOCKFILES)
                if self.tree.has(_join(base, n))
            ]
        return found

    def python(self, *args: str) -> tuple[str, ...]:
        head = ("uv", "run", "python") if self.uv else ("python",)
        return (*head, *args)

    def add_target(
        self, cls: TargetClass, evidence: list[str], paths: list[str]
    ) -> None:
        tid = f"target-{cls}"
        self.used_ids.add(tid)
        self.targets[cls] = TargetEntry(
            id=tid,
            status="proposed",
            provenance=self.provenance(paths),
            target_class=cls,
            evidence=tuple(evidence),
        )
        self.evidence[tid] = tuple(evidence)

    def add_workload(
        self,
        base_id: str,
        target: TargetClass,
        source: WorkloadSource,
        command: tuple[str, ...],
        cwd: str,
        files: Sequence[str],
        evidence: str,
        commands: tuple[tuple[str, ...], ...] = (),
        args_unknown: bool = False,
    ) -> None:
        if any(w.command == command and w.cwd == cwd for w in self.workloads):
            return
        wid = self.unique(base_id)
        paths = [*files, *self.lockfiles_for(cwd)]
        self.workloads.append(
            WorkloadEntry(
                id=wid,
                status="proposed",
                provenance=self.provenance(paths, commands),
                target_id=self.targets[target].id,
                source=source,
                command=command,
                cwd=cwd,
            )
        )
        self.evidence[wid] = (evidence,)
        notes: list[str] = []
        if command[0] == "python":
            notes.append(
                "interpreter not established: bare `python` was assumed "
                "because there is no uv.lock"
            )
        elif source == "entry_point" and len(command) == 1:
            notes.append(
                "interpreter not established: the console script is "
                "assumed to be installed on PATH"
            )
        if args_unknown:
            notes.append("arguments not established")
        if notes:
            caveat = "; ".join(notes)
            self.caveats[wid] = caveat
            self.evidence[wid] = (evidence, f"caveat: {caveat}")


def _detect_targets(b: _Builder) -> dict[str, list[str]]:
    """Add the target entries; return facts later steps reuse."""
    tree = b.tree
    deps = _dependencies(tree)
    routes, gpu_src, py = _scan_python(tree)
    manifests = [
        f
        for f in tree.files
        if PurePosixPath(f).name in {"pyproject.toml", "setup.py", "setup.cfg"}
        or re.fullmatch(r"requirements.*\.txt", PurePosixPath(f).name)
    ]
    facts: dict[str, list[str]] = {}
    if manifests or py:
        tests = [
            f
            for f in py
            if PurePosixPath(f).name.startswith("test_")
            or PurePosixPath(f).name.endswith("_test.py")
        ]
        evidence = [f"manifests: {', '.join(manifests) or 'none'}"]
        evidence.append(f"{len(py)} Python files, {len(tests)} test files")
        b.add_target("python_cpu", evidence, manifests)
    cpp = sorted(
        f for f in tree.files if PurePosixPath(f).suffix in _SOURCE_EXT
    )
    native = [
        f
        for f in tree.files
        if PurePosixPath(f).name in _NATIVE_MANIFESTS
        and (
            PurePosixPath(f).name == "Cargo.toml"
            or any(c.startswith(_dir(f) + "/") or _dir(f) == "." for c in cpp)
        )
    ]
    if native and cpp:
        evidence = [
            f"build manifests: {', '.join(native)}",
            f"{len(cpp)} C, C++ or Rust source files",
        ]
        b.add_target("native", evidence, native)
        facts["native_manifests"] = native
    web = sorted(n for n in deps if n in _WEB_DEPS)
    if web or routes:
        evidence: list[str] = []
        if web:
            evidence.append(
                "web framework dependencies: "
                + ", ".join(f"{n} ({deps[n][0]})" for n in web)
            )
        if routes:
            evidence.append(
                f"{len(routes)} route declarations, first: "
                + ", ".join(routes[:3])
            )
        paths = sorted(
            {p for n in web for p in deps[n]}
            | {r.split(":")[0] for r in routes[:5]}
        )
        b.add_target("web_service", evidence, paths)
    serving_deps = sorted(n for n in deps if n in _SERVING_DEPS)
    serving_files: list[str] = []
    for path in tree.files:
        base = PurePosixPath(path).name
        if _SERVING_NAME.match(base):
            serving_files.append(path)
        elif base.startswith(
            ("Dockerfile", "docker-compose")
        ) or base.endswith((".sh", ".yaml", ".yml")):
            text = tree.read(path)
            if text and _SERVING_LINE.search(text):
                serving_files.append(path)
    if serving_deps or serving_files:
        evidence: list[str] = []
        if serving_deps:
            evidence.append("serving dependencies: " + ", ".join(serving_deps))
        if serving_files:
            evidence.append("serving configs: " + ", ".join(serving_files[:5]))
        paths = sorted(
            {deps[n][0] for n in serving_deps} | set(serving_files[:5])
        )
        b.add_target("llm_serving", evidence, paths)
    cuda = [f for f in tree.files if PurePosixPath(f).suffix in _CUDA_EXT]
    gpu_deps = sorted(n for n in deps if n in _GPU_DEPS)
    if (
        cuda
        or gpu_src
        or (gpu_deps and ("torch" in gpu_deps or "triton" in gpu_deps))
    ):
        evidence: list[str] = []
        if gpu_deps:
            evidence.append("GPU dependencies: " + ", ".join(gpu_deps))
        if cuda:
            evidence.append(f"{len(cuda)} CUDA files, first: {cuda[0]}")
        if gpu_src:
            evidence.append(
                "kernel or extension code: " + ", ".join(gpu_src[:3])
            )
        paths = sorted(
            {deps[n][0] for n in gpu_deps} | set(cuda[:3]) | set(gpu_src[:3])
        )
        b.add_target("gpu_kernel", evidence, paths)
    agent = sorted(n for n in deps if n in _AGENT_DEPS)
    if agent:
        evidence = [
            "model client dependencies: "
            + ", ".join(f"{n} ({deps[n][0]})" for n in agent)
        ]
        b.add_target("agentic", evidence, sorted({deps[n][0] for n in agent}))
    return facts


def _pyproject_scripts(tree: _Tree) -> list[tuple[str, str]]:
    """Return ``(name, pyproject path)`` of each console script."""
    found: list[tuple[str, str]] = []
    for path in tree.named("pyproject.toml"):
        project = cast(
            "dict[str, object]", _toml(tree, path).get("project") or {}
        )
        scripts = project.get("scripts")
        if isinstance(scripts, dict):
            found += [(n, path) for n in cast("dict[str, object]", scripts)]
    return found


def _is_script(text: str | None) -> bool:
    return bool(text and "__main__" in text)


def _detect_workloads(b: _Builder) -> None:
    tree = b.tree
    if "python_cpu" in b.targets:
        _python_workloads(b)
    for manifest in tree.named("Makefile"):
        directory = _dir(manifest)
        text = tree.read(manifest) or ""
        found = _MAKE_BENCH.search(text)
        if found and "native" in b.targets:
            name = found.group(1)
            b.add_workload(
                name if directory == "." else f"{slug(directory)}-{name}",
                "native",
                "existing_benchmark",
                ("make", name),
                directory,
                [manifest],
                f"{manifest} defines a `{name}` target",
            )
    for manifest in tree.named("Cargo.toml"):
        directory = _dir(manifest)
        benches = _join(directory, "benches")
        if any(f.startswith(benches + "/") for f in tree.files) and (
            "native" in b.targets
        ):
            b.add_workload(
                "cargo-bench"
                if directory == "."
                else f"{slug(directory)}-cargo-bench",
                "native",
                "existing_benchmark",
                ("cargo", "bench"),
                directory,
                [manifest],
                f"{manifest} with a benches/ directory",
            )


def _python_workloads(b: _Builder) -> None:
    tree = b.tree
    for path in tree.named("asv.conf.json"):
        b.add_workload(
            "asv",
            "python_cpu",
            "existing_benchmark",
            ("asv", "run"),
            _dir(path),
            [path],
            f"{path}: asv benchmark suite",
        )
    packages = {
        _dir(f)
        for f in tree.files
        if PurePosixPath(f).name == "__main__.py"
        and PurePosixPath(_dir(f)).name in _BENCH_PACKAGES
    }
    for package in sorted(packages):
        parent = _dir(package)
        name = PurePosixPath(package).name
        b.add_workload(
            slug(name),
            "python_cpu",
            "existing_benchmark",
            b.python("-m", name),
            parent,
            [_join(package, "__main__.py")],
            f"{package}/ is a runnable benchmark package",
            args_unknown=True,
        )
    for path in tree.files:
        base = PurePosixPath(path).name
        if not _BENCH_PY.match(base) or any(
            path.startswith(p + "/") for p in packages
        ):
            continue
        if _is_script(tree.read(path)):
            stem = PurePosixPath(base).stem
            b.add_workload(
                slug(stem),
                "python_cpu",
                "existing_benchmark",
                b.python(path),
                ".",
                [path],
                f"{path} is a runnable benchmark script",
            )
    for name, pyproject in _pyproject_scripts(tree):
        launcher = ("uv", "run", name) if b.uv else (name,)
        b.add_workload(
            slug(name),
            "python_cpu",
            "entry_point",
            launcher,
            _dir(pyproject),
            [pyproject],
            f"{pyproject} [project.scripts] {name}",
        )
    for path in tree.named("__main__.py"):
        directory = _dir(path)
        parts = directory.split("/")
        if (
            directory == "."
            or directory in packages
            or any(p in _NOT_ENTRY_DIRS for p in parts)
            or PurePosixPath(directory).name in _BENCH_PACKAGES
        ):
            continue
        module = PurePosixPath(directory).name
        base = "/".join(parts[:-1]) or "."
        b.add_workload(
            f"{slug(module)}-main",
            "python_cpu",
            "entry_point",
            b.python("-m", module),
            "." if parts[0] == "src" else base,
            [path],
            f"{path}: `python -m {module}`",
            args_unknown=True,
        )
    for nodeid, seconds, origin, commands in _slow_tests(b):
        file = nodeid.split("::")[0]
        b.add_workload(
            f"slow-{slug(nodeid.split('::')[-1])}",
            "python_cpu",
            "slow_test",
            b.python("-m", "pytest", nodeid),
            ".",
            [file, origin] if origin else [file],
            f"{nodeid}: slowest test, {seconds:.2f} s "
            f"({origin or 'from one pytest run'})",
            commands,
        )


def _junit_nodeids(
    tree: _Tree,
) -> list[tuple[str, float, str]]:
    """Return ``(node id, seconds, file)`` from JUnit XML files."""
    out: list[tuple[str, float, str]] = []
    present = frozenset(tree.files)
    for path in tree.files:
        base = PurePosixPath(path).name
        if not base.endswith(".xml") or not _JUNIT_NAME.search(base):
            continue
        text = tree.read(path) or ""
        for match in _TESTCASE.finditer(text):
            attrs = {k: unescape(v) for k, v in _ATTR.findall(match.group(1))}
            try:
                seconds = float(attrs["time"])
                parts = attrs["classname"].split(".")
                name = attrs["name"]
            except (KeyError, ValueError):
                continue
            for i in range(len(parts), 0, -1):
                module = "/".join(parts[:i]) + ".py"
                if module in present:
                    node = "::".join([module, *parts[i:], name])
                    out.append((node, seconds, path))
                    break
    return out


def _slow_tests(
    b: _Builder,
) -> list[tuple[str, float, str, tuple[tuple[str, ...], ...]]]:
    """Return the slowest tests: id, seconds, origin file, commands."""
    ranked = sorted(_junit_nodeids(b.tree), key=lambda t: (-t[1], t[0]))
    chosen = [(n, s, o, ()) for n, s, o in ranked[:SLOW_TESTS]]
    if chosen or b.run_pytest is None:
        return chosen
    durations, notes, argv = b.run_pytest()
    b.notes += notes
    present = frozenset(b.tree.files)
    return [
        (n, s, "", (argv,))
        for n, s in durations[:SLOW_TESTS]
        if n.split("::")[0] in present
    ]


def _goal_for(b: _Builder, workload: WorkloadEntry) -> GoalEntry:
    reason = "the developer has not said what should get faster"
    return GoalEntry(
        id=f"goal-{workload.id}",
        status="proposed",
        provenance=b.provenance([]),
        workload_id=workload.id,
        metric=None,
        direction=None,
        unit=None,
        bar=None,
        guardrails=None,
        missing=dict.fromkeys(_PY_GOAL_FIELDS, reason),
    )


def _how_to_run(
    b: _Builder, facts: Mapping[str, list[str]]
) -> HowToRunEntry | None:
    if not b.targets:
        return None
    setup: tuple[tuple[str, ...], ...] | None = None
    missing: dict[str, str] = {}
    paths: list[str] = []
    if b.uv:
        setup = (("uv", "sync", "--frozen"),)
        paths += ["uv.lock"]
    else:
        missing["setup"] = (
            "no uv.lock; the install command was not established"
        )
    builds: list[tuple[str, ...]] = []
    for manifest in facts.get("native_manifests", []):
        directory = _dir(manifest)
        base = PurePosixPath(manifest).name
        paths.append(manifest)
        if base == "Makefile":
            builds.append(
                ("make",) if directory == "." else ("make", "-C", directory)
            )
        elif base == "Cargo.toml":
            builds.append(
                ("cargo", "build", "--release")
                if directory == "."
                else (
                    "cargo",
                    "build",
                    "--release",
                    "--manifest-path",
                    manifest,
                )
            )
        elif base == "CMakeLists.txt":
            out = _join(directory, "build")
            builds.append(("cmake", "-S", directory, "-B", out))
            builds.append(("cmake", "--build", out))
    build: tuple[tuple[str, ...], ...] | None = (
        tuple(builds) if builds else None
    )
    if build is None:
        missing["build"] = "no build command found in the manifests"
    missing["services"] = "the services a workload needs were not established"
    missing["reset"] = "the reset between rounds was not established"
    entry = HowToRunEntry(
        id="how-to-run",
        status="proposed",
        provenance=b.provenance(paths),
        workload_id=None,
        setup=setup,
        build=build,
        services=None,
        reset=None,
        missing=missing,
    )
    evidence = [f"{k} unknown: {v}" for k, v in missing.items()]
    if setup:
        evidence.insert(0, "setup from uv.lock")
    if build:
        evidence.insert(0, f"build from {', '.join(sorted(set(paths)))}")
    b.evidence[entry.id] = tuple(evidence)
    return entry


def _questions(
    b: _Builder,
    workloads: Sequence[WorkloadEntry],
    how: HowToRunEntry | None,
) -> tuple[Question, ...]:
    out: list[Question] = []
    if len(workloads) > 1:
        ids = ", ".join(w.id for w in workloads)
        out.append(
            Question("workload", None, f"Which of {ids} represents real use?")
        )
    covered = {
        next(t.target_class for t in b.targets.values() if t.id == w.target_id)
        for w in workloads
    }
    out.extend(
        Question(f"workload:{cls}", f"target-{cls}", _CLASS_QUESTION[cls])
        for cls in b.targets
        if cls not in covered
    )
    out.extend(
        Question(
            f"goal:{w.id}",
            f"goal-{w.id}",
            f"What should get faster for {w.id}: wall time, CPU time, "
            "peak memory, p99 latency or something else, and by how much?",
        )
        for w in workloads
    )
    if b.targets:
        out.append(
            Question(
                "constraints",
                None,
                "Anything that must not change: public APIs, numeric "
                "tolerance, off-limits dependencies, protected files?",
            )
        )
    if how is not None and how.missing:
        out.append(
            Question(
                "how-to-run",
                how.id,
                "How is the project set up, built and reset between "
                f"rounds, and which services does it need? Unknown: "
                f"{', '.join(sorted(how.missing))}.",
            )
        )
    return tuple(out)


def _pytest_runner(
    root: Path, python: Path | None, timeout: float
) -> Callable[[], _PytestRun]:
    def run() -> _PytestRun:
        interpreter = python or root / ".venv" / "bin" / "python"
        if not interpreter.exists():
            return (
                [],
                [
                    f"slow tests not run: no interpreter at {interpreter}; "
                    "pass --python"
                ],
                (),
            )
        argv = [
            str(interpreter),
            "-m",
            "pytest",
            f"--durations={SLOW_TESTS}",
            "--durations-min=0",
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            done = subprocess.run(
                argv,
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return (
                [],
                [f"slow tests not found: pytest did not finish: {exc}"],
                tuple(argv),
            )
        found = [
            (m.group(2), float(m.group(1)))
            for m in _DURATION.finditer(done.stdout)
        ]
        notes = (
            []
            if found
            else [f"pytest exited {done.returncode} and printed no durations"]
        )
        ranked = sorted(found, key=lambda t: (-t[1], t[0]))
        return ranked, notes, tuple(argv)

    return run


def detect(
    root: Path,
    *,
    now: datetime,
    version: str,
    slow_tests: bool = False,
    python: Path | None = None,
    test_timeout: float = PYTEST_TIMEOUT_SECONDS,
) -> Proposal:
    """Propose a project model for the repository at ``root``.

    Parameters
    ----------
    root : Path
        Repository root. Nothing under it is written.
    now : datetime
        Derivation time, with time zone.
    version : str
        Perfproof version recorded in each provenance.
    slow_tests : bool, optional
        Run the project's pytest once to find the slowest tests when no
        JUnit XML file names them. Executes the project's tests.
    python : Path, optional
        Interpreter for that run; default ``<root>/.venv/bin/python``.
    test_timeout : float, optional
        Time limit of that run, in seconds.

    Returns
    -------
    Proposal
        Possibly empty; an empty proposal says in ``notes`` that no
        target class was found. Cost is linear in the files visited
        (at most `MAX_FILES`), plus reading at most `MAX_READ_BYTES` of
        each file examined.
    """
    tree = _walk(root)
    runner = _pytest_runner(root, python, test_timeout) if slow_tests else None
    b = _Builder(tree, now, version, runner)
    facts = _detect_targets(b)
    _detect_workloads(b)
    if tree.truncated:
        b.notes.append(
            f"the walk stopped at depth {MAX_DEPTH} or {MAX_FILES} files; "
            "deeper files were not examined"
        )
    if not b.targets:
        b.notes.append("no target class was detected")
    workloads = tuple(b.workloads)
    goals = tuple(_goal_for(b, w) for w in workloads)
    how = _how_to_run(b, facts)
    for goal in goals:
        b.evidence[goal.id] = ("unknown: asked, never defaulted",)
    return Proposal(
        targets=tuple(b.targets.values()),
        workloads=workloads,
        goals=goals,
        how_to_run=(how,) if how else (),
        evidence=b.evidence,
        questions=_questions(b, workloads, how),
        notes=tuple(b.notes),
        caveats=dict(b.caveats),
    )
