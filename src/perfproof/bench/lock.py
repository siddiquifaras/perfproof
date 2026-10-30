"""The lock, procedure and runtime gates, and recorded differences.

These are the integrity checks of ``docs/method.md`` that compare the
two arms of one comparison:

- `lock_gate`: the locked files (harness, oracle, input data, config)
  of both trees must hash as in the manifest frozen with the spec.
- `procedure_gate`: both arms ran under the spec in force and the same
  perfproof version, and every measured round launched exactly the
  spec's argv, declared environment, working directory and the tree's
  resolved executable.
- `runtime_gate`: both trees run the same interpreter or runtime,
  unless the spec allows runtime changes. `probe_environment` finds
  the interpreter behind launchers (shebangs, ``env``, ``uv run`` and
  the command it runs); a runtime it cannot identify makes the gate
  ``not_available``, never passed. A native executable built in the
  tree is judged by the libraries it links outside the tree and, when
  git tracks it, by its bytes (see `runtime_gate` for the rule and its
  stated limit).
- `environment_warnings`: everything recorded but not gated (the
  environment variable list, values of variables known to affect
  performance, dependency lockfiles) becomes a warning when the arms
  differ.

Each gate returns a `perfproof.schema.GateResult` and never raises for
a property of the trees it checks; it raises only for a malformed call.

Locked paths may name files or directories. A directory locks every
regular file below it except the byproducts in `IGNORED_NAMES`. Files
are hashed in fixed-size chunks, so memory stays bounded at
`LOCK_CHUNK_BYTES` whatever the size of the input data. When a spec
lists a file under `HARNESS_DIR`, every file of that directory is
locked as harness, listed or not.
"""

import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast, get_args

from pydantic import ValidationError

from perfproof.bench.gobuildinfo import go_build_version
from perfproof.bench.nativebuildinfo import compiler_family, native_build_info
from perfproof.bench.spec import (
    STATE_DIR,
    normalize_launch_path,
    spec_command_hash,
)
from perfproof.schema import (
    Arm,
    BenchmarkSpec,
    DependencyLock,
    Difference,
    GateName,
    GateResult,
    LockedFile,
    LockManifest,
    LockRole,
    RuntimeKind,
    RunWarning,
    Sample,
    TreeEnvironment,
)

__all__ = [
    "DEPENDENCY_LOCKFILES",
    "HARNESS_DIR",
    "IGNORED_NAMES",
    "LOCK_CHUNK_BYTES",
    "MAX_DIFFERENCES",
    "PERFORMANCE_ENV_VARS",
    "PROBE_TIMEOUT_SECONDS",
    "LockError",
    "build_manifest",
    "environment_warnings",
    "fetching_launch",
    "hash_file",
    "lock_gate",
    "owns_harness",
    "probe_environment",
    "procedure_gate",
    "resolve_executable",
    "runtime_gate",
]

HARNESS_DIR: Final = f"{STATE_DIR}/harness"
"""Tree-relative directory whose contents belong to perfproof."""
LOCK_CHUNK_BYTES: Final = 1 << 20
"""Read size when hashing a file, in bytes; the hashing memory bound."""
MAX_DIFFERENCES: Final = 20
"""Most differences a gate lists; the rest are counted in its detail."""
PROBE_TIMEOUT_SECONDS: Final = 30.0
"""Time limit for the runtime version probe, in seconds."""
IGNORED_NAMES: Final = frozenset({".git", "__pycache__"})
"""Names skipped inside a locked directory. Git and Python write them
on their own (a worktree's ``.git`` file, bytecode caches that embed
source timestamps), so they differ between trees by construction and
say nothing about the experiment."""
PERFORMANCE_ENV_VARS: Final = frozenset(
    {
        "CUDA_VISIBLE_DEVICES",
        "DYLD_INSERT_LIBRARIES",
        "DOTNET_GCgen0size",
        "DOTNET_ReadyToRun",
        "DOTNET_TC_QuickJitForLoops",
        "DOTNET_TieredCompilation",
        "DOTNET_TieredPGO",
        "DOTNET_gcConcurrent",
        "DOTNET_gcServer",
        "DYLD_LIBRARY_PATH",
        "GOGC",
        "GOMAXPROCS",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "MALLOC_ARENA_MAX",
        "MKL_NUM_THREADS",
        "NODE_OPTIONS",
        "NUMEXPR_NUM_THREADS",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "PYTHONDEVMODE",
        "PYTHONHASHSEED",
        "PYTHONMALLOC",
        "PYTHONOPTIMIZE",
        "PYTHON_GIL",
        "PYTHON_JIT",
        "PYTORCH_CUDA_ALLOC_CONF",
        "RAYON_NUM_THREADS",
        "RUBYLIB",
        "RUBYOPT",
        "RUBY_GC_HEAP_GROWTH_FACTOR",
        "RUBY_GC_HEAP_INIT_SLOTS",
        "RUBY_YJIT_ENABLE",
        "TOKENIZERS_PARALLELISM",
        "UV_THREADPOOL_SIZE",
        "VECLIB_MAXIMUM_THREADS",
    }
)
"""Variables whose values are recorded, because they are known to change
how fast a process runs. Only names are recorded for all others."""
DEPENDENCY_LOCKFILES: Final = (
    "Cargo.lock",
    "Gemfile.lock",
    "Pipfile.lock",
    "go.sum",
    "package-lock.json",
    "packages.lock.json",
    "pdm.lock",
    "pixi.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
)
"""Dependency lockfiles looked for at a tree's root."""

_ARMS: Final[tuple[Arm, ...]] = get_args(Arm)
_PYTHON_NAME: Final = re.compile(r"^(?:python|pypy)[0-9.]*t?$")
_INTERPRETER_PROBE: Final = (
    "import sys; sys.stdout.write(sys.executable + '\\n' + sys.version)"
)
_PATH_PROBE: Final = "import os, sys; sys.stdout.write(os.environ['PATH'])"
_UV_SCRIPT_OPTIONS: Final = frozenset({"-s", "--script", "--gui-script"})
_INLINE_METADATA: Final = re.compile(r"^# /// script\s*$")
_LOADER_VAR_PREFIXES: Final = ("DYLD_", "LD_")
_SHARED_CACHE_PREFIXES: Final = ("/usr/lib/", "/System/Library/")
_DYLIB_LOADS: Final = frozenset(
    {
        "LC_LAZY_LOAD_DYLIB",
        "LC_LOAD_DYLIB",
        "LC_LOAD_UPWARD_DYLIB",
        "LC_LOAD_WEAK_DYLIB",
        "LC_REEXPORT_DYLIB",
    }
)
_MAX_IMAGES: Final = 512
_KERNEL_DSO: Final = re.compile(r"linux-(?:vdso|gate)[0-9]*\.so\.[0-9]+")
"""The kernel-provided vDSO, which ``ldd`` lists without a path."""
_RUSTC_HASH: Final = re.compile(rb"/rustc/([0-9a-f]{40})/")
"""The compiler's commit in the standard library's source paths, which a
Rust binary carries for panic locations on every platform."""
_RUSTC_BANNER: Final = re.compile(
    rb"rustc version ([0-9]+\.[0-9]+\.[0-9]+ \([0-9a-f]{9,} [0-9-]{10}\))"
)
"""The compiler's version line, which an ELF binary carries in its
``.comment`` section (and, unstripped, in its debug strings). The match
ends at the closing parenthesis, so a line cut by a read boundary does
not match until the overlap holds all of it."""
_STAMP_OVERLAP: Final = 128
_RUST_TOOLCHAIN_NAMES: Final = frozenset({"cargo", "rustc", "rustup"})
_PT_DYNAMIC: Final = 2
_PT_INTERP: Final = 3
_LIBRARIES_AT_GATE: Final = (
    "not listed by the probe; the runtime gate lists them from both "
    "trees when it compares them"
)
"""Why a native build's `TreeEnvironment.linked_libraries` is unset."""
_LAUNCHER_NAMES: Final = frozenset(
    {
        "asdf",
        "bun",
        "chruby-exec",
        "conda",
        "corepack",
        "deno",
        "hatch",
        "invoke",
        "mamba",
        "micromamba",
        "mise",
        "npm",
        "nox",
        "npx",
        "pdm",
        "pip-run",
        "pipenv",
        "pipx",
        "pixi",
        "pn",
        "pnpm",
        "pnpx",
        "pnx",
        "poetry",
        "rbenv",
        "rvm",
        "rye",
        "tox",
        "uvx",
        "yarn",
        "yarnpkg",
    }
)
"""Tools that pick and start another runtime, which the probe does not
follow. Launched directly, as a console script, or as a ``-m`` module
(``python -m tox``, ``uv run python -m tox``) they are unknown wherever
they live. Names match case-insensitively with ``_`` and ``-`` alike,
and ``-m uv`` is unknown too."""
_INSTALL_DIRS: Final = frozenset(
    {"dist-packages", "node_modules", "site-packages"}
)
_NODE_NAMES: Final = frozenset({"node", "nodejs"})
_COMPILER_NAME: Final = re.compile(
    r"(?:[a-z0-9_.]+-)*(?:cc|c\+\+|gcc|g\+\+|clang|clang\+\+)(?:-\d+(?:\.\d+)*)?"
)
"""A C or C++ compiler driver's name: ``cc``, ``gcc-14``, ``clang++``,
``aarch64-linux-gnu-g++``."""
_COMPILER_BANNER: Final = re.compile(r"(?m)^InstalledDir: (?P<dir>.+)$")
_GO_SUBCOMMANDS: Final = frozenset({"build", "run", "test", "vet"})
"""The ``go`` subcommands whose runtime is the toolchain that runs them:
they compile the code under test, and ``run`` and ``test`` then run it.
Every other subcommand (``install``, ``tool``, ``generate``, ``get``,
``mod``, ``work``, ``env``) starts other programs or fetches code."""
_GO_ENV_NAMES: Final = (
    "CGO_ENABLED",
    "GOAMD64",
    "GOARCH",
    "GOARM64",
    "GOEXPERIMENT",
    "GOFLAGS",
    "GOOS",
    "GOROOT",
    "GOVERSION",
)
"""What the Go probe asks ``go env`` for. The variables other than
``GOROOT`` and ``GOVERSION`` change the code the toolchain generates."""
_GO_MODULE_FETCH: Final = re.compile(r"[\w.-]+\.[\w.-]+/\S*@\S+")
_RUBY_NAME: Final = re.compile(r"(?:ruby|jruby|truffleruby)[0-9.]*")
_BUNDLE_NAMES: Final = frozenset({"bundle", "bundler"})
_RUBY_PROBE: Final = (
    "require 'rbconfig'; puts RbConfig.ruby; puts RUBY_DESCRIPTION"
)
"""What a Ruby probe prints: the real path of its own executable, then
the line ``ruby -v`` prints, one per line."""
_DOTNET_SUBCOMMANDS: Final = frozenset({"build", "exec", "run", "test"})
"""The ``dotnet`` subcommands whose runtime is the SDK and runtime that
run them: they compile the code under test, and ``run``, ``test`` and
``exec`` start it. Every other subcommand (``tool``, ``new``, ``add``,
``restore``, ``nuget``, ``workload``) starts other programs or fetches
code. A path ending in ``.dll`` is an application the host runs."""
_RUNTIME_PATH: Final = re.compile(r"\s*\[[^\]]*\]\s*$")
_SHELL_NAMES: Final = frozenset(
    {"ash", "bash", "csh", "dash", "fish", "ksh", "sh", "tcsh", "zsh"}
)
_NODE_LAUNCHERS: Final = frozenset({"npm", "npx"})
_NO_FETCH_FLAGS: Final = frozenset({"--no", "--no-install"})
_NODE_PROBE: Final = (
    "const fs = require('fs');"
    "console.log(fs.realpathSync(process.execPath));"
    "console.log(process.version);"
)
"""What a Node probe prints: its executable's real path, then its
version, one per line."""
_PROBE_OUTPUT_CHARS: Final = 4096
"""Longest probe output taken as a report."""
_SHEBANG_BYTES: Final = 512
_MAX_LAUNCH_DEPTH: Final = 4
_MACHO_MAGIC: Final = frozenset(
    {
        b"\xcf\xfa\xed\xfe",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xfe\xed\xfa\xce",
        b"\xca\xfe\xba\xbe",
        b"\xca\xfe\xba\xbf",
    }
)
_UV_VALUE_OPTIONS: Final = frozenset(
    {
        "--allow-insecure-host",
        "--cache-dir",
        "--color",
        "--config-file",
        "--config-setting",
        "--config-settings-package",
        "--default-index",
        "--directory",
        "--env-file",
        "--exclude-newer",
        "--exclude-newer-package",
        "--extra",
        "--extra-index-url",
        "--find-links",
        "--fork-strategy",
        "--group",
        "--index",
        "--index-strategy",
        "--index-url",
        "--keyring-provider",
        "--link-mode",
        "--no-binary-package",
        "--no-build-isolation-package",
        "--no-build-package",
        "--no-editable-package",
        "--no-extra",
        "--no-group",
        "--no-sources-package",
        "--only-group",
        "--package",
        "--prerelease",
        "--prerelease-package",
        "--project",
        "--python",
        "--python-platform",
        "--refresh-package",
        "--reinstall-package",
        "--resolution",
        "--upgrade-group",
        "--upgrade-package",
        "--with",
        "--with-editable",
        "--with-requirements",
        "-C",
        "-P",
        "-f",
        "-i",
        "-p",
        "-w",
    }
)
"""``uv run`` options that take a separate value, from ``uv run --help``
of uv 0.12.20."""


_RuntimeKind = Literal["interpreter", "native_build", "unknown"]


@dataclass(frozen=True, slots=True)
class _Runtime:
    """What a launch resolves to.

    An ``interpreter`` has a version; a ``native_build`` is a native
    executable built in the tree; anything else is ``unknown`` and
    carries the reason.
    """

    kind: _RuntimeKind
    executable: Path | None
    version: str | None = None
    reason: str | None = None
    family: str | None = None

    @classmethod
    def unknown(cls, executable: Path | None, reason: str) -> "_Runtime":
        """Return a runtime that could not be identified."""
        return cls("unknown", executable, None, reason)

    def explained(self, prefix: str) -> "_Runtime":
        """Prefix an unknown runtime's reason with the launcher."""
        if self.kind != "unknown":
            return self
        return _Runtime.unknown(self.executable, f"{prefix} -> {self.reason}")


class LockError(ValueError):
    """A locked path is missing, unreadable or not a plain file."""


def hash_file(path: Path) -> tuple[str, int]:
    """Hash a file's bytes with SHA-256 in bounded memory.

    Parameters
    ----------
    path : Path
        File to hash. Symlinks are followed.

    Returns
    -------
    tuple of (str, int)
        Hex digest and the number of bytes read.

    Raises
    ------
    OSError
        If the file cannot be read.
    """
    digest = hashlib.sha256()
    buffer = bytearray(LOCK_CHUNK_BYTES)
    view = memoryview(buffer)
    size = 0
    with path.open("rb", buffering=0) as handle:
        while n := handle.readinto(view):
            digest.update(view[:n])
            size += n
    return digest.hexdigest(), size


def _lock_file(root: Path, rel: str, role: LockRole) -> LockedFile:
    """Hash one regular file of a tree as a manifest entry."""
    path = root / rel
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            msg = f"{rel} is not a regular file"
            raise LockError(msg)
        sha, size = hash_file(path)
        return LockedFile(path=rel, role=role, sha256=sha, size_bytes=size)
    except OSError as exc:
        msg = f"{rel} cannot be read: {exc}"
        raise LockError(msg) from exc
    except ValidationError as exc:
        msg = f"{rel} cannot be locked: {exc}"
        raise LockError(msg) from exc


def _walk(root: Path, declared: str) -> Iterable[str]:
    """Yield the tree-relative paths of the files below a directory."""
    top = root / declared

    def fail(exc: OSError) -> None:
        raise LockError(f"{declared} cannot be read: {exc}") from exc

    for dirpath, dirnames, filenames in os.walk(top, onerror=fail):
        rel_dir = Path(dirpath).relative_to(top).as_posix()
        base = declared if rel_dir == "." else f"{declared}/{rel_dir}"
        for name in list(dirnames):
            if name in IGNORED_NAMES:
                dirnames.remove(name)
            elif os.path.islink(os.path.join(dirpath, name)):
                msg = (
                    f"{base}/{name} is a directory symlink, which the lock "
                    "does not follow; declare its target instead"
                )
                raise LockError(msg)
        dirnames.sort()
        for name in sorted(filenames):
            if name not in IGNORED_NAMES:
                yield posixpath.join(base, name)


def owns_harness(spec: BenchmarkSpec) -> bool:
    """Say whether the spec lists a file under `HARNESS_DIR`.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force; its ``harness``, ``inputs`` and ``config``
        paths are read.

    Returns
    -------
    bool
        True if any of them is `HARNESS_DIR` or below it.
    """
    declared = (*spec.harness, *spec.inputs, *spec.config)
    return any(
        path == HARNESS_DIR or path.startswith(f"{HARNESS_DIR}/")
        for path in declared
    )


def _collect(
    spec: BenchmarkSpec, root: Path
) -> tuple[dict[str, LockedFile], list[str]]:
    """Hash every locked file of one tree.

    Returns
    -------
    tuple of (dict of str to LockedFile, list of str)
        Entries by path, and the declared paths absent from the tree.

    Raises
    ------
    LockError
        If a locked file is not a regular file or cannot be read, a
        directory holds a directory symlink, or a file is reached
        through two declared paths.
    """
    groups: tuple[tuple[LockRole, tuple[str, ...]], ...] = (
        ("harness", spec.harness),
        ("oracle", spec.oracle),
        ("input", spec.inputs),
        ("config", spec.config),
    )
    files: dict[str, LockedFile] = {}
    missing: list[str] = []
    for role, declared_paths in groups:
        for declared in declared_paths:
            path = root / declared
            if not path.exists():
                missing.append(declared)
                continue
            rels = _walk(root, declared) if path.is_dir() else (declared,)
            for rel in rels:
                if rel in files:
                    msg = (
                        f"{rel} is locked more than once, as "
                        f"{files[rel].role} and as {role}"
                    )
                    raise LockError(msg)
                files[rel] = _lock_file(root, rel, role)
    if owns_harness(spec) and (root / HARNESS_DIR).is_dir():
        # A helper beside a listed benchmark is code too; it is locked
        # even though the spec never named it.
        for rel in _walk(root, HARNESS_DIR):
            if rel not in files:
                files[rel] = _lock_file(root, rel, "harness")
    return files, missing


def build_manifest(spec: BenchmarkSpec, tree_root: Path) -> LockManifest:
    """Hash a tree's locked files into the manifest frozen with a spec.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec whose harness, oracle, inputs and config are locked.
    tree_root : Path
        Root of the tree the spec is defined in.

    Returns
    -------
    LockManifest
        One entry per locked file, sorted by path.

    Raises
    ------
    LockError
        If a declared path is missing, or any locked file is not a
        regular file, cannot be read, or is declared twice.
    """
    files, missing = _collect(spec, tree_root)
    if missing:
        msg = f"declared paths are missing: {', '.join(missing)}"
        raise LockError(msg)
    return LockManifest(spec_hash=spec.spec_hash, files=tuple(files.values()))


def _gate(
    gate: GateName,
    problems: list[str],
    differences: list[Difference],
    *,
    passed_detail: str = "",
) -> GateResult:
    """Build a passed or failed gate result, truncating differences."""
    if not problems:
        return GateResult(gate=gate, status="passed", detail=passed_detail)
    return GateResult(
        gate=gate,
        status="failed",
        detail="; ".join(problems),
        differences=tuple(differences[:MAX_DIFFERENCES]),
        truncated=len(differences) > MAX_DIFFERENCES,
    )


def lock_gate(
    spec: BenchmarkSpec,
    frozen: LockManifest,
    *,
    baseline_root: Path,
    candidate_root: Path,
) -> GateResult:
    """Check both trees' locked files against the frozen manifest.

    Comparing each tree with the manifest, not only with each other,
    also catches an edit made identically in both trees after the spec
    was frozen.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force.
    frozen : LockManifest
        The manifest frozen with ``spec``.
    baseline_root, candidate_root : Path
        Roots of the two trees.

    Returns
    -------
    GateResult
        Gate ``lock``: passed when every locked file of both trees
        matches ``frozen`` and no declared path is missing; failed
        otherwise, with one difference per mismatching path (the hash in
        each tree, ``None`` where absent), sorted by path. Hashing is
        linear in the bytes of the locked files.
    """
    if frozen.spec_hash != spec.spec_hash:
        detail = (
            f"the lock manifest belongs to spec {frozen.spec_hash}, not the "
            f"spec in force {spec.spec_hash}"
        )
        return _gate("lock", [detail], [])
    trees: dict[Arm, dict[str, LockedFile]] = {}
    problems: list[str] = []
    roots: tuple[tuple[Arm, Path], ...] = (
        ("baseline", baseline_root),
        ("candidate", candidate_root),
    )
    for arm, root in roots:
        try:
            files, missing = _collect(spec, root)
        except LockError as exc:
            problems.append(f"{arm} tree: {exc}")
            continue
        if missing:
            problems.append(
                f"declared paths missing from the {arm} tree: "
                f"{', '.join(missing)}"
            )
        trees[arm] = files
    if len(trees) < len(_ARMS):
        return _gate("lock", problems, [])
    expected = {f.path: f.sha256 for f in frozen.files}
    found = {
        arm: {path: f.sha256 for path, f in files.items()}
        for arm, files in trees.items()
    }
    differences: list[Difference] = []
    absent_in_both: list[str] = []
    for path in sorted(
        expected.keys() | found["baseline"].keys() | found["candidate"].keys()
    ):
        want = expected.get(path)
        b, c = found["baseline"].get(path), found["candidate"].get(path)
        if b == want and c == want:
            continue
        if b is None and c is None:
            absent_in_both.append(path)
        else:
            differences.append(Difference(item=path, baseline=b, candidate=c))
    changed = len(differences) + len(absent_in_both)
    if changed:
        problems.insert(
            0,
            f"{changed} locked files differ from the manifest frozen with "
            f"spec {spec.spec_hash}",
        )
    if absent_in_both:
        problems.append(f"absent from both trees: {', '.join(absent_in_both)}")
    return _gate(
        "lock",
        problems,
        differences,
        passed_detail=f"{len(expected)} locked files match in both trees",
    )


def _require_arms(name: str, mapping: Mapping[Arm, str]) -> None:
    """Refuse a per-arm mapping that misses an arm."""
    absent = [arm for arm in _ARMS if arm not in mapping]
    if absent:
        msg = f"{name} has no entry for {', '.join(absent)}"
        raise ValueError(msg)


def procedure_gate(
    spec: BenchmarkSpec,
    samples: Iterable[Sample],
    *,
    spec_hashes: Mapping[Arm, str],
    perfproof_versions: Mapping[Arm, str],
    executables: Mapping[Arm, str],
) -> GateResult:
    """Check both arms ran the spec's procedure, round by round.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force.
    samples : iterable of Sample
        Measured rounds so far; may be empty before the first round.
    spec_hashes : mapping of Arm to str
        `BenchmarkSpec.spec_hash` each arm was set up and measured
        under.
    perfproof_versions : mapping of Arm to str
        perfproof version that measured each arm.
    executables : mapping of Arm to str
        Each tree's resolved executable for the benchmark command, as
        `perfproof.bench.spec.normalize_launch_path` spells it.

    Returns
    -------
    GateResult
        Gate ``procedure``: failed when either arm's spec hash is not
        the spec in force (difference ``spec_hash``), the perfproof
        versions differ (``perfproof_version``), or any sample's
        ``command_hash`` is not
        `perfproof.bench.spec.spec_command_hash` for its arm (one
        ``round <i> command_hash`` difference per such round, with
        each arm's hash in that round). Passed otherwise.

    Raises
    ------
    ValueError
        If a mapping lacks an arm.
    """
    for name, mapping in (
        ("spec_hashes", spec_hashes),
        ("perfproof_versions", perfproof_versions),
        ("executables", executables),
    ):
        _require_arms(name, mapping)
    problems: list[str] = []
    differences: list[Difference] = []
    if any(spec_hashes[arm] != spec.spec_hash for arm in _ARMS):
        problems.append(
            f"an arm was measured under a spec other than the spec in force "
            f"{spec.spec_hash}"
        )
        differences.append(
            Difference(
                item="spec_hash",
                baseline=spec_hashes["baseline"],
                candidate=spec_hashes["candidate"],
            )
        )
    if perfproof_versions["baseline"] != perfproof_versions["candidate"]:
        problems.append("the arms ran under different perfproof versions")
        differences.append(
            Difference(
                item="perfproof_version",
                baseline=perfproof_versions["baseline"],
                candidate=perfproof_versions["candidate"],
            )
        )
    expected = {
        arm: spec_command_hash(spec, executables[arm]) for arm in _ARMS
    }
    launched: dict[int, dict[Arm, str]] = {}
    bad_rounds: set[int] = set()
    for s in samples:
        seen = launched.setdefault(s.round, {})
        if s.command_hash != expected[s.version]:
            bad_rounds.add(s.round)
            seen[s.version] = s.command_hash
        else:
            seen.setdefault(s.version, s.command_hash)
    if bad_rounds:
        problems.append(
            f"{len(bad_rounds)} rounds launched a command other than the "
            f"spec's (expected baseline {expected['baseline']}, candidate "
            f"{expected['candidate']})"
        )
        differences.extend(
            Difference(
                item=f"round {r} command_hash",
                baseline=launched[r].get("baseline"),
                candidate=launched[r].get("candidate"),
            )
            for r in sorted(bad_rounds)
        )
    return _gate("procedure", problems, differences)


def _native_kind(path: Path) -> str | None:
    """Return ``"ELF"`` or ``"Mach-O"`` for a native binary."""
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return None
    if magic == b"\x7fELF":
        return "ELF"
    return "Mach-O" if magic in _MACHO_MAGIC else None


def _is_python_name(name: str) -> bool:
    """Whether a file name is an interpreter's, e.g. ``python3.13t``."""
    return _PYTHON_NAME.fullmatch(name) is not None


def _arm_runtime(
    env: TreeEnvironment, root: Path
) -> tuple[str | None, str | None, str | None, bool]:
    """Classify one arm's recorded runtime for the gate.

    Only the probe's explicit record decides the kind:
    `TreeEnvironment.runtime_kind`. A native build must also still be a
    native file inside the tree. Anything else is unknown, whatever the
    path looks like.

    Returns
    -------
    tuple of (str or None, str or None, str or None, bool)
        The executable spelled tree-independently, the version, why the
        runtime is unknown (``None`` when it is known), and whether it
        is a native build in the tree, which has no interpreter.
    """
    exe = env.runtime_executable
    if exe is None:
        reason = env.missing.get("runtime_executable", "not recorded")
        return None, None, reason, False
    path = Path(exe)
    tree = root.resolve()
    spelled = normalize_launch_path(path, tree) if path.is_absolute() else exe
    if env.runtime_kind == "interpreter":
        return spelled, env.runtime_version, None, False
    if env.runtime_kind is None:
        reason = env.missing.get("runtime_kind", "not recorded")
        return spelled, None, reason, False
    if not (
        path.is_absolute()
        and path.resolve().is_relative_to(tree)
        and _native_kind(path) is not None
    ):
        return (
            spelled,
            None,
            f"recorded as a native build, but {exe} is not a native "
            "executable inside the tree",
            False,
        )
    return spelled, None, None, True


def _run_tool(
    argv: Sequence[str],
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Run a helper tool; a tool that cannot start exits 127."""
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            env=None if env is None else dict(env),
            cwd=cwd,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def _macho_load_commands(
    image: Path,
) -> tuple[list[tuple[str, bool]], list[str]] | str:
    """Read an image's dylib load commands and ``LC_RPATH`` entries.

    Returns
    -------
    tuple of (list of (str, bool), list of str), or str
        Each linked install name with whether it is a weak link, and
        the raw rpaths; or why ``otool -l`` failed.
    """
    code, out, err = _run_tool(["otool", "-l", str(image)])
    if code != 0:
        return f"otool -l {image} failed: {err.strip()[-200:] or code}"
    deps: list[tuple[str, bool]] = []
    rpaths: list[str] = []
    command = ""
    for raw in out.splitlines():
        line = raw.strip()
        if line.startswith("cmd "):
            command = line.removeprefix("cmd ")
        elif line.startswith("name ") and command in _DYLIB_LOADS:
            name = line.removeprefix("name ").rsplit(" (offset", 1)[0]
            deps.append((name, command == "LC_LOAD_WEAK_DYLIB"))
        elif line.startswith("path ") and command == "LC_RPATH":
            rpaths.append(line.removeprefix("path ").rsplit(" (offset", 1)[0])
    return deps, rpaths


def _label(path: Path, tree: Path) -> str:
    """Spell an image tree-relatively when inside the tree."""
    real = path.resolve()
    return (
        real.relative_to(tree).as_posix()
        if real.is_relative_to(tree)
        else str(real)
    )


def _macho_closure(exe: Path, tree: Path, cwd: Path) -> dict[str, str] | str:
    """Walk every image a Mach-O executable loads, as dyld would.

    Each image's dependencies are resolved against that image:
    ``@loader_path`` is its own directory, ``@executable_path`` the
    executable's, and ``@rpath`` is searched through the ``LC_RPATH``
    entries of the image and then of each image that loaded it. A
    relative install name or rpath that remains is taken against
    ``cwd``, the benchmark's working directory, as dyld takes it
    against the process's. Images inside the tree are walked but not
    recorded.

    Returns
    -------
    dict of str to str, or str
        ``"<loader> -> <install name>"`` to the identity of each library
        outside the tree, or why the closure cannot be listed. At most
        `_MAX_IMAGES` images are walked; one ``otool`` call per image.
    """
    exe_dir = exe.parent

    def expand(value: str, loader_dir: Path) -> str:
        path = Path(
            value.replace("@executable_path", str(exe_dir)).replace(
                "@loader_path", str(loader_dir)
            )
        )
        return str(path if path.is_absolute() else cwd / path)

    libraries: dict[str, str] = {}
    visited: set[Path] = set()
    pending: list[tuple[Path, tuple[str, ...]]] = [(exe, ())]
    while pending:
        image, inherited = pending.pop()
        real = image.resolve()
        if real in visited:
            continue
        visited.add(real)
        if len(visited) > _MAX_IMAGES:
            return f"more than {_MAX_IMAGES} images are loaded"
        parsed = _macho_load_commands(real)
        if isinstance(parsed, str):
            return parsed
        deps, raw_rpaths = parsed
        rpaths = (*(expand(r, image.parent) for r in raw_rpaths), *inherited)
        loader = _label(image, tree)
        for name, weak in deps:
            if name.startswith("@rpath/"):
                rest = name.removeprefix("@rpath/")
                hits = [
                    Path(r) / rest for r in rpaths if (Path(r) / rest).exists()
                ]
                path = hits[0] if hits else None
            else:
                path = Path(expand(name, image.parent))
            key = f"{loader} -> {name}"
            if path is None or not path.exists():
                if name.startswith(_SHARED_CACHE_PREFIXES):
                    libraries[key] = f"{name} (dyld shared cache)"
                elif weak:
                    libraries[key] = f"{name} (weak, absent)"
                else:
                    return f"{name}, loaded by {loader}, cannot be resolved"
                continue
            dep = path.resolve()
            if not dep.is_relative_to(tree):
                try:
                    libraries[key] = f"{dep} sha256:{hash_file(dep)[0]}"
                except OSError as exc:
                    return f"{dep} cannot be read: {exc}"
            pending.append((path, rpaths))
    return libraries


def _elf_is_static(exe: Path) -> bool | None:
    """Whether an ELF file has no interpreter and no dynamic section.

    Returns ``None`` when the header cannot be read.
    """
    try:
        with exe.open("rb") as handle:
            header = handle.read(64)
            wide = header[4] == 2
            order = "little" if header[5] == 1 else "big"
            if wide:
                phoff = int.from_bytes(header[0x20:0x28], order)
                size = int.from_bytes(header[0x36:0x38], order)
                count = int.from_bytes(header[0x38:0x3A], order)
            else:
                phoff = int.from_bytes(header[0x1C:0x20], order)
                size = int.from_bytes(header[0x2A:0x2C], order)
                count = int.from_bytes(header[0x2C:0x2E], order)
            handle.seek(phoff)
            table = handle.read(size * count)
    except (OSError, IndexError):
        return None
    if len(table) != size * count:
        return None
    types = {
        int.from_bytes(table[i * size : i * size + 4], order)
        for i in range(count)
    }
    return not types & {_PT_DYNAMIC, _PT_INTERP}


def _elf_closure(exe: Path, tree: Path, cwd: Path) -> dict[str, str] | str:
    """List every library an ELF executable loads, as ``ldd`` reports.

    ``ldd`` runs in the benchmark's working directory, because relative
    ``RPATH`` entries resolve against it, with no loader variables set
    (the caller has ruled them out).

    Returns
    -------
    dict of str to str, or str
        Library name to the identity of each library outside the tree,
        or why the list cannot be made.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_LOADER_VAR_PREFIXES)
    }
    code, out, err = _run_tool(["ldd", str(exe)], env, cwd)
    text = out + err
    if "not a dynamic executable" in text or "statically linked" in text:
        if _elf_is_static(exe) is True:
            return {}
        return f"ldd reports {exe} is not dynamic, but its header disagrees"
    if code != 0:
        return f"ldd failed: {err.strip()[-200:] or code}"
    libraries: dict[str, str] = {}
    for raw in out.splitlines():
        line = raw.strip().rsplit(" (0x", 1)[0]
        if "=>" in line:
            name, _, target = (part.strip() for part in line.partition("=>"))
        elif line.startswith("/"):
            name = target = line
        elif _KERNEL_DSO.fullmatch(line) or not line:
            continue
        else:
            return f"ldd printed a line that is not understood: {line!r}"
        if target == "not found" or not target:
            return f"{name} cannot be resolved"
        path = Path(target)
        if not path.is_absolute():
            path = cwd / path
        if not path.exists():
            return f"{name} resolves to {path}, which does not exist"
        real = path.resolve()
        if real.is_relative_to(tree):
            continue
        try:
            libraries[name] = f"{real} sha256:{hash_file(real)[0]}"
        except OSError as exc:
            return f"{real} cannot be read: {exc}"
    return libraries


def _outside_libraries(
    exe: Path, root: Path, cwd: Path, env: TreeEnvironment
) -> dict[str, str] | str:
    """Map each outside-tree library ``exe`` loads to its identity.

    Loader variables (any ``DYLD_*`` or ``LD_*``) change where libraries
    come from in ways this does not follow, so any of them in the
    benchmark's environment fails closed, as does an environment whose
    names were not recorded.

    Returns
    -------
    dict of str to str, or str
        The whole load closure's outside libraries, keyed so the two
        trees compare entry by entry: ``"<realpath> sha256:<hex>"``,
        ``"<path> (dyld shared cache)"`` for a macOS system library
        that exists only in the shared cache, or ``"<name> (weak,
        absent)"``. Or why the list cannot be made.
    """
    if env.env_names is None:
        return (
            "the environment's variable names were not recorded, so loader "
            "variables cannot be ruled out"
        )
    loader_vars = [
        n for n in env.env_names if n.startswith(_LOADER_VAR_PREFIXES)
    ]
    if loader_vars:
        return (
            f"loader variables are set ({', '.join(loader_vars)}); how they "
            "change library resolution is not followed"
        )
    tree = root.resolve()
    kind = _native_kind(exe)
    if kind == "Mach-O":
        return _macho_closure(exe, tree, cwd)
    if kind == "ELF":
        return _elf_closure(exe, tree, cwd)
    return f"{exe} is not a native executable"


def _is_tracked(path: Path) -> bool | str:
    """Whether git tracks the file at ``path``, or why that is unknown.

    git is asked from the file's own directory, so a file inside a
    submodule is judged by the submodule. Only a clean "tracked" or
    "did not match" answer counts.
    """
    code, _, err = _run_tool(
        [
            "git",
            "-C",
            str(path.parent),
            "ls-files",
            "--error-unmatch",
            "--",
            path.name,
        ]
    )
    if code == 0:
        return True
    if code == 1 and "did not match" in err:
        return False
    return f"git ls-files exited {code}: {err.strip()[-200:]}"


type _Stamp = tuple[frozenset[str], frozenset[str]]


def _rustc_stamp(path: Path) -> _Stamp | str:
    """Read which Rust compiler built a binary, from the binary.

    The commit of the compiler is in the ``/rustc/<40 hex digits>/``
    prefix of the standard library's source paths, which a Rust binary
    carries on macOS and on Linux; an ELF binary also has the compiler's
    ``rustc version`` line in its ``.comment`` section. The file is read
    in chunks, so memory does not grow with its size.

    Returns
    -------
    tuple of (frozenset of str, frozenset of str), or str
        The commits and the version lines found (both empty for a binary
        that is not Rust), or why the file
        cannot be read.
    """
    commits: set[str] = set()
    banners: set[str] = set()
    tail = b""
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(LOCK_CHUNK_BYTES):
                data = tail + chunk
                commits.update(
                    m.group(1).decode("ascii")
                    for m in _RUSTC_HASH.finditer(data)
                )
                banners.update(
                    m.group(1).decode("ascii", errors="replace").strip()
                    for m in _RUSTC_BANNER.finditer(data)
                )
                tail = data[-_STAMP_OVERLAP:]
    except OSError as exc:
        return str(exc)
    return frozenset(commits), frozenset(banners)


def _rustc_label(stamp: _Stamp) -> str:
    """Write a compiler stamp as ``commit; version`` for a report."""
    commits, banners = stamp
    parts = [f"rustc commit {c}" for c in sorted(commits)]
    parts.extend(f"rustc {b}" for b in sorted(banners))
    return "; ".join(parts)


def _rust_profile(rel: str) -> str | None:
    """Name the cargo profile directory a binary's path is in."""
    parts = rel.split("/")
    for name in ("debug", "release"):
        if name in parts[:-1]:
            return name
    return None


def _compare_native(
    rel: str,
    spec: BenchmarkSpec,
    envs: Mapping[Arm, TreeEnvironment],
    roots: Mapping[Arm, Path],
) -> tuple[list[str], list[str], list[Difference], str]:
    """Compare a native executable built at ``rel`` in each tree.

    Returns
    -------
    tuple of (list of str, list of str, list of Difference, str)
        Problems (evidence the trees differ), what could not be read,
        differences, and the detail to report if neither.
    """
    problems: list[str] = []
    unverified: list[str] = []
    differences: list[Difference] = []
    paths: dict[Arm, Path] = {
        arm: Path(str(envs[arm].runtime_executable)) for arm in _ARMS
    }
    status = {arm: _is_tracked(paths[arm]) for arm in _ARMS}
    for arm, state in status.items():
        if isinstance(state, str):
            unverified.append(
                f"whether git tracks the {arm} {rel} is unknown: {state}"
            )
    tracked = any(state is True for state in status.values())
    if tracked:
        try:
            shas = {arm: hash_file(paths[arm])[0] for arm in _ARMS}
        except OSError as exc:
            shas = {}
            unverified.append(f"{rel} cannot be read: {exc}")
        if shas and shas["baseline"] != shas["candidate"]:
            problems.append(
                f"{rel} is tracked by git and differs between the trees: a "
                "binary swap, not a build of the code under test"
            )
            differences.append(
                Difference(
                    item=f"{rel} sha256",
                    baseline=shas["baseline"],
                    candidate=shas["candidate"],
                )
            )
    libraries: dict[Arm, dict[str, str]] = {}
    for arm in _ARMS:
        cwd = (roots[arm] / spec.cwd).absolute()
        listed = _outside_libraries(paths[arm], roots[arm], cwd, envs[arm])
        if isinstance(listed, str):
            unverified.append(
                f"the libraries the {arm} {rel} links cannot be listed: "
                f"{listed}"
            )
        else:
            libraries[arm] = listed
    if len(libraries) == len(_ARMS):
        b, c = libraries["baseline"], libraries["candidate"]
        changed = [
            Difference(
                item=f"library {name}",
                baseline=b.get(name),
                candidate=c.get(name),
            )
            for name in sorted(b.keys() | c.keys())
            if b.get(name) != c.get(name)
        ]
        if changed:
            problems.append(
                f"{rel} links different libraries outside the tree in the "
                "two trees"
            )
            differences.extend(changed)
    stamps: dict[Arm, _Stamp] = {}
    for arm in _ARMS:
        found = _rustc_stamp(paths[arm])
        if isinstance(found, str):
            unverified.append(
                f"the compiler of the {arm} {rel} cannot be read: {found}"
            )
        else:
            stamps[arm] = found
    built_by = ""
    if len(stamps) == len(_ARMS):
        b_stamp, c_stamp = stamps["baseline"], stamps["candidate"]
        if b_stamp[0] != c_stamp[0]:
            if b_stamp[0] and c_stamp[0]:
                problems.append(
                    f"{rel} was built by different Rust compilers in the "
                    "two trees"
                )
                differences.append(
                    Difference(
                        item="rustc",
                        baseline=_rustc_label(b_stamp),
                        candidate=_rustc_label(c_stamp),
                    )
                )
            else:
                unverified.append(
                    f"only one tree's {rel} names a Rust compiler "
                    f"(baseline: {_rustc_label(b_stamp) or 'none'}; "
                    f"candidate: {_rustc_label(c_stamp) or 'none'})"
                )
        elif b_stamp[0]:
            # Equal commits: the version lines are detail, and a build
            # that keeps debug strings has one more than a stripped one.
            profile = _rust_profile(rel)
            built_by = f"; built by {_rustc_label(b_stamp)} in both trees" + (
                f" (cargo {profile} profile directory)" if profile else ""
            )
    toolchains = _go_toolchains(rel, paths, problems, unverified, differences)
    if toolchains is None:
        toolchains = _native_toolchains(
            rel, paths, problems, unverified, differences
        )
    how = (
        "tracked by git and identical in both trees"
        if tracked
        else "untracked, so treated as a build of the code under test and "
        "not compared itself"
    )
    if toolchains is not None:
        how += f"; built by {toolchains} in both"
    shared = "; ".join(
        f"{name} = {value}"
        for name, value in sorted(libraries.get("baseline", {}).items())
    )
    detail = (
        f"{rel} is a native executable built in each tree ({how}); it has "
        "no interpreter to compare; libraries outside the tree match: "
        f"{shared or 'none'}{built_by}"
    )
    return problems, unverified, differences, detail


def _go_toolchains(
    rel: str,
    paths: Mapping[Arm, Path],
    problems: list[str],
    unverified: list[str],
    differences: list[Difference],
) -> str | None:
    """Compare the Go toolchain that built each arm's executable.

    An executable built from Go source names its toolchain in the file
    (`go_build_version`), so two trees whose executables were built by
    different toolchains differ in more than the code under test. This
    adds to ``problems`` and ``differences`` when both name one and the
    names differ, and to ``unverified`` when only one does or a file
    cannot be read.

    Returns
    -------
    str or None
        The toolchain both arms were built by, or ``None`` when neither
        is a Go build or the arms differ.
    """
    found: dict[Arm, str | None] = {}
    for arm in _ARMS:
        try:
            found[arm] = go_build_version(paths[arm])
        except OSError as exc:
            unverified.append(f"the {arm} {rel} cannot be read: {exc}")
            return None
    base, cand = found["baseline"], found["candidate"]
    if base is None and cand is None:
        return None
    if base is None or cand is None:
        unverified.append(
            f"only the {'candidate' if base is None else 'baseline'} {rel} "
            "is a Go build, so the toolchains cannot be compared"
        )
        return None
    if base != cand:
        problems.append(
            f"{rel} was built by different Go toolchains ({base} and {cand})"
        )
        differences.append(
            Difference(
                item=f"{rel} go toolchain", baseline=base, candidate=cand
            )
        )
        return None
    return base


def _native_toolchains(
    rel: str,
    paths: Mapping[Arm, Path],
    problems: list[str],
    unverified: list[str],
    differences: list[Difference],
) -> str | None:
    """Compare the C or C++ toolchain that built each arm's executable.

    An ELF executable names its compilers in ``.comment`` and a Mach-O
    one its SDK and linker (`native_build_info`). Two trees whose
    executables name different toolchains differ in more than the code
    under test. This adds to ``problems`` and ``differences`` when both
    name one and the names differ, and to ``unverified`` when only one
    does or a file cannot be read. An executable that names none (not a
    C or C++ build, or stripped) adds nothing.

    Returns
    -------
    str or None
        The toolchain both arms were built by, or ``None`` when neither
        names one or the arms differ.
    """
    found: dict[Arm, tuple[str, ...]] = {}
    for arm in _ARMS:
        try:
            found[arm] = native_build_info(paths[arm])
        except OSError as exc:
            unverified.append(f"the {arm} {rel} cannot be read: {exc}")
            return None
    base, cand = found["baseline"], found["candidate"]
    if not base and not cand:
        return None
    if not base or not cand:
        unverified.append(
            f"only the {'candidate' if not base else 'baseline'} {rel} names "
            "the toolchain that built it, so the toolchains cannot be "
            "compared"
        )
        return None
    if base != cand:
        problems.append(
            f"{rel} was built by different toolchains "
            f"({'; '.join(base)} and {'; '.join(cand)})"
        )
        differences.append(
            Difference(
                item=f"{rel} native toolchain",
                baseline="; ".join(base),
                candidate="; ".join(cand),
            )
        )
        return None
    family = compiler_family(base)
    named = f"{family}: " if family else ""
    return f"{named}{'; '.join(base)}"


def runtime_gate(
    spec: BenchmarkSpec,
    baseline: TreeEnvironment,
    candidate: TreeEnvironment,
    *,
    baseline_root: Path,
    candidate_root: Path,
) -> GateResult:
    """Check both trees run the same interpreter or runtime.

    The executables are compared as `TreeEnvironment` records them,
    with paths inside a tree made relative to it, so a
    ``.venv/bin/python`` copied into each tree compares equal and is
    then told apart by version.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force; ``allow_runtime_change`` waives the gate.
    baseline, candidate : TreeEnvironment
        What each tree runs with, from `probe_environment`.
    baseline_root, candidate_root : Path
        Roots of the two trees.

    Returns
    -------
    GateResult
        Gate ``runtime``. Passed when both trees run the same
        interpreter at the same version. Failed only on evidence: both
        trees' runtimes identified and different, with differences
        ``runtime_executable`` and ``runtime_version``, or (for native
        builds) a tracked binary that differs or different outside
        libraries. ``not_available``, with the launcher and reason in
        the detail, when either tree's runtime is unknown or something
        a native build is compared by could not be read: perfproof
        can't tell, which is not a difference ("Unknown is not wrong"
        in ``docs/method.md``).

        Only positively identified runtimes pass. The kind comes from
        the probe's record, never from where the file sits: a known
        launcher (``uv`` other than ``uv run``, ``uvx``, ``poetry``
        and the like), a build tool or toolchain (``cargo``, ``go``,
        ``make``), a shell, or a native executable installed into the
        tree (inside a virtualenv, ``site-packages`` or
        ``node_modules``), is unknown, so the gate is ``not_available``.

        A native executable built at the same path inside each tree
        has no interpreter, so it is judged by what it runs on:

        - every library in its load closure that lives outside the tree
          must resolve to the same path and contents in both trees; the
          detail lists them when they do. On macOS the closure is walked
          image by image with ``otool -l``, in-tree libraries included,
          resolving ``@loader_path``, ``@executable_path`` and
          ``@rpath`` per loader, up to `_MAX_IMAGES` images; on Linux
          ``ldd`` reports it, run in the benchmark's working directory.
          macOS system libraries present only in the dyld shared cache
          are compared by path. A closure that cannot be listed is
          ``not_available``.
        - any loader variable (``DYLD_*``, ``LD_*``) in the benchmark's
          environment, or an environment whose names were not recorded,
          makes the gate ``not_available``: their effect on resolution
          is not followed.
        - if git tracks the executable in either tree (asked from the
          file's own directory, so submodules count), its bytes must be
          identical: a tracked binary that differs is a swap, not a
          build of the code under test. Any git answer other than a
          clear tracked or untracked is ``not_available``.
        - an untracked executable is taken to be a build of the code
          under test and its own bytes are not compared. This is a
          stated limit: a different untracked interpreter copied into
          the tree under another name passes if it links the same
          outside libraries, and the detail says the binary was not
          compared.

        Under the spec's ``allow_runtime_change`` every failure and
        every runtime that could not be identified is waived instead,
        with the same detail.

        What the gate covers is what perfproof launches, followed
        through the launch forms above. A stated limit remains: a
        program the gate identifies as a Python or native program
        that itself starts a different interpreter (other than the
        known launchers in `_LAUNCHER_NAMES`, which are unknown) is
        recorded under the program's own runtime.

    Raises
    ------
    ValueError
        If ``baseline`` and ``candidate`` are not the baseline and
        candidate environments.
    """
    if (baseline.version, candidate.version) != _ARMS:
        msg = (
            "expected the baseline and the candidate environment, got "
            f"{baseline.version} and {candidate.version}"
        )
        raise ValueError(msg)
    b_exe, b_ver, b_unknown, b_native = _arm_runtime(baseline, baseline_root)
    c_exe, c_ver, c_unknown, c_native = _arm_runtime(candidate, candidate_root)
    problems: list[str] = []
    unverified: list[str] = [
        f"the {arm} runtime is unknown: {unknown}"
        for arm, unknown in (("baseline", b_unknown), ("candidate", c_unknown))
        if unknown is not None
    ]
    differences: list[Difference] = []
    if not unverified:
        differences = [
            Difference(item=item, baseline=b, candidate=c)
            for item, b, c in (
                ("runtime_executable", b_exe, c_exe),
                ("runtime_version", b_ver, c_ver),
            )
            if b != c
        ]
        if differences:
            problems.append("the trees run different runtimes")
    passed = f"both trees run {b_exe}, version {b_ver}"
    if (
        not unverified
        and b_native
        and c_native
        and b_exe == c_exe
        and b_exe is not None
    ):
        native_problems, native_unverified, native_differences, passed = (
            _compare_native(
                b_exe,
                spec,
                {"baseline": baseline, "candidate": candidate},
                {"baseline": baseline_root, "candidate": candidate_root},
            )
        )
        problems.extend(native_problems)
        unverified.extend(native_unverified)
        differences.extend(native_differences)
    if (problems or unverified) and spec.allow_runtime_change:
        return GateResult(
            gate="runtime",
            status="waived",
            detail="allow_runtime_change: " + "; ".join(problems + unverified),
            differences=tuple(differences),
        )
    if problems:
        return _gate("runtime", problems + unverified, differences)
    if unverified:
        return GateResult(
            gate="runtime",
            status="not_available",
            detail=(
                "the runtime was not verified, so a KEEP is withheld: "
                + "; ".join(unverified)
            ),
        )
    return _gate("runtime", [], [], passed_detail=passed)


def _python_module(args: Sequence[str]) -> str | None:
    """Return the module a Python command line runs with ``-m``.

    Options are read as CPython reads them, including combined short
    flags (``-Im tox``) and the value-taking ``-X`` and ``-W``. A
    script, ``-c`` or ``-`` ends the options with no module.
    """
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--" or not arg.startswith("-") or arg == "-":
            return None
        if arg.startswith("--"):
            if arg == "--check-hash-based-pycs":
                i += 1
            i += 1
            continue
        for j, flag in enumerate(arg[1:], start=1):
            rest = arg[j + 1 :]
            if flag == "m":
                if rest:
                    return rest
                return args[i + 1] if i + 1 < len(args) else None
            if flag == "c":
                return None
            if flag in "XW":
                if not rest:
                    i += 1
                break
        i += 1
    return None


def _canonical(name: str) -> str:
    """Spell a name for matching: ``Pip_Run`` becomes ``pip-run``."""
    return name.lower().replace("_", "-")


def _is_launcher_name(name: str) -> bool:
    """Whether a program name is one of `_LAUNCHER_NAMES`."""
    return _canonical(name) in _LAUNCHER_NAMES


def _launcher_module(args: Sequence[str]) -> str | None:
    """Return the launcher a Python command line runs with ``-m``.

    This is the one check every Python launch goes through. The
    module's top-level package is matched against `_LAUNCHER_NAMES`
    and ``uv``, whose ``-m`` form is not followed.
    """
    module = _python_module(args)
    if module is None:
        return None
    top = _canonical(module.split(".")[0])
    return module if top in _LAUNCHER_NAMES or top == "uv" else None


def _has_inline_metadata(script: Path) -> bool:
    """Whether a script has a PEP 723 block, or cannot be read."""
    try:
        with script.open(encoding="utf-8", errors="replace") as handle:
            return any(_INLINE_METADATA.match(line) for line in handle)
    except OSError:
        return True


def _is_executable_file(path: Path) -> bool:
    """Whether ``path`` is a file this process may execute."""
    return path.is_file() and os.access(path, os.X_OK)


def _which(program: str, env: Mapping[str, str], cwd: Path) -> Path | None:
    """Resolve a program name as ``execvpe`` would in ``cwd``.

    A name with a slash is taken relative to ``cwd``. Anything else is
    looked up on the ``PATH`` of ``env``, whose relative entries (and
    empty ones, meaning the current directory) are relative to ``cwd``,
    the directory the child starts in.
    """
    if "/" in program:
        path = Path(os.path.normpath(cwd / program))
        return path if _is_executable_file(path) else None
    for entry in env.get("PATH", os.defpath).split(os.pathsep):
        directory = Path(entry or ".")
        if not directory.is_absolute():
            directory = cwd / directory
        path = Path(os.path.normpath(directory / program))
        if _is_executable_file(path):
            return path
    return None


_COREPACK_DOWNLOADS: Final = frozenset(
    {"prepare", "install", "up", "use", "pack"}
)
_FETCH_ALWAYS: Final = frozenset({"bunx", "pnpx", "pnx", "uvx"})
_FETCH_SUBCOMMANDS: Final = frozenset(
    {"add", "ci", "create", "i", "install", "up", "update", "upgrade"}
)
_FETCH_TOOLS: Final = frozenset(
    {
        "bun",
        "corepack",
        "deno",
        "npm",
        "npx",
        "pipx",
        "pnpm",
        "uv",
        "yarn",
        "yarnpkg",
    }
)
_ENV_VALUE_OPTIONS: Final = frozenset(
    {"-C", "-S", "-u", "--chdir", "--split-string", "--unset"}
)
_REMOTE_SPECIFIER: Final = re.compile(r"(?:https?|npm|jsr):", re.IGNORECASE)
_VALUE_OPTIONS: Final = frozenset(
    {
        "-C",
        "-F",
        "-w",
        "--cwd",
        "--dir",
        "--filter",
        "--prefix",
        "--registry",
        "--workspace",
        "--userconfig",
        "-c",
        "--call",
    }
)
_PATH_PREFIXES: Final = ("./", "../", "/")


def fetching_launch(
    argv: Sequence[str],
    env: Mapping[str, str],
    cwd: Path,
    *,
    tree: Path | None = None,
) -> str | None:
    """Say why starting ``argv`` would fetch code from the network.

    "Integrity" in ``docs/method.md`` makes these an unsafe run, not an
    unknown one: ``npx`` and ``npm exec`` with no command installed in
    a ``node_modules/.bin`` and no ``--no-install`` (or with ``-p``),
    ``pnpm dlx``, ``pnpx``, ``pnx``, ``yarn dlx``, ``bunx``, ``bun x``,
    ``uvx``, ``pipx run``, ``uv tool run``, ``deno run`` on a URL or an
    ``npm:`` or ``jsr:`` specifier, ``corepack``, and the install forms
    of ``npm``, ``pnpm``, ``yarn`` and ``bun``. Nothing is run: the
    answer comes from the argument list and the files in ``cwd``.
    ``env`` wrappers are followed.

    Parameters
    ----------
    argv : sequence of str
        The command, program first.
    env : Mapping of str to str
        Environment whose ``PATH`` resolves the program.
    cwd : Path
        Working directory the command starts in.
    tree : Path, optional
        Root of the tree the command runs in; a wrapper that lives in
        it is not peeled (see `unwrap_launch`).

    Returns
    -------
    str or None
        The reason, or ``None`` when the form is not one of those.
    """
    args = unwrap_launch(argv, env, cwd, tree)
    for _ in range(_MAX_LAUNCH_DEPTH):
        if not args:
            return None
        found = _which(args[0], env, cwd)
        names = {_canonical(Path(args[0]).name)}
        if found is not None:
            names |= {_canonical(found.name), _canonical(found.resolve().name)}
        if "env" not in names:
            invoked = _canonical(Path(args[0]).name)
            return _fetch_reason(names, args[1:], cwd, env, invoked)
        args = args[1:]
        while args and (args[0].startswith("-") or "=" in args[0]):
            if args[0] in ("-S", "--split-string") and len(args) > 1:
                args = [*args[1].split(), *args[2:]]
                break
            args = args[2 if args[0] in _ENV_VALUE_OPTIONS else 1 :]
    return None


def _fetch_reason(
    names: set[str],
    rest: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    invoked: str,
) -> str | None:
    """Return why one program with ``rest`` fetches, or ``None``."""
    always = sorted(names & _FETCH_ALWAYS)
    if always:
        return f"{always[0]} downloads a package and runs it"
    if "go" in names:
        return _go_fetch_reason(rest)
    if "dotnet" in names:
        return _dotnet_fetch_reason(rest)
    if names & _BUNDLE_NAMES:
        return _bundle_fetch_reason(rest)
    if "gem" in names:
        return _gem_fetch_reason(rest)
    known = sorted(names & _FETCH_TOOLS)
    if not known:
        return None
    if "corepack" in names and invoked != "corepack":
        # A package manager that is a corepack shim.
        return _corepack_reason(rest, env, shim=invoked)
    tool = invoked if invoked in known else known[0]
    raw = rest
    rest = _without_options(rest)
    sub = rest[0] if rest else ""
    if tool == "corepack":
        return _corepack_reason(rest, env)
    if tool == "npx" or (tool == "npm" and sub in {"exec", "x"}):
        return _npx_reason(tool, raw if tool == "npx" else rest[1:], cwd)
    if tool == "deno":
        remote = any(_REMOTE_SPECIFIER.match(a) for a in rest[1:])
        if sub in {"install", "add"} or (sub == "run" and remote):
            return f"deno {sub} fetches from a URL or a registry"
        return None
    if tool == "pipx":
        return "pipx run downloads a package" if sub == "run" else None
    if tool == "uv":
        if sub == "tool" and rest[1:2] == ["run"]:
            return "uv tool run downloads a tool and runs it"
        return None
    if sub == "dlx" or (tool == "bun" and sub == "x"):
        return f"{tool} {sub} downloads a package and runs it"
    if sub in _FETCH_SUBCOMMANDS or (tool in {"yarn", "yarnpkg"} and not raw):
        return f"{tool} {sub or 'with no command'} installs from a registry"
    return None


def _go_fetch_reason(rest: Sequence[str]) -> str | None:
    """Return why a ``go`` command fetches and runs code, or ``None``.

    ``go run`` and ``go install`` on ``module/path@version`` download
    that module and build it, and ``go get`` and ``go mod download``
    download modules. ``go run``, ``test`` and ``build`` on the
    project's own packages fetch dependencies only when the module cache
    lacks them, which this does not tell; declare that in ``setup``.
    """
    sub = rest[0] if rest else ""
    if sub == "get":
        return "go get downloads modules"
    if sub == "mod" and rest[1:2] == ["download"]:
        return "go mod download downloads modules"
    if sub in {"install", "run"} and any(
        _GO_MODULE_FETCH.fullmatch(a) for a in rest[1:]
    ):
        return f"go {sub} on a module@version downloads and builds it"
    return None


def _ruby_searches_path(args: Sequence[str]) -> bool:
    """Whether a ``ruby`` command line may have ``-S`` as a flag.

    ``-S`` makes Ruby run the program of that name found on ``PATH``,
    so the script named is not the one that runs. Ruby's short flags
    cluster (``-wS``, ``-W2S``, ``-00S``) and some flags take the next
    argument, even ``--`` (``-I -- -S``), so this does not parse them:
    it fails closed. Any argument that starts with one ``-`` and
    contains ``S`` counts. A false positive (``-FS``, ``-- -S``, a
    script's own ``-S`` argument) only makes the runtime unknown,
    which withholds a KEEP.
    """
    return any(
        arg.startswith("-") and not arg.startswith("--") and "S" in arg
        for arg in args
    )


def _dotnet_fetch_reason(rest: Sequence[str]) -> str | None:
    """Return why a ``dotnet`` command fetches code, or ``None``.

    ``dotnet tool install``, ``update`` and ``exec``, ``dnx``,
    ``add package``, ``restore`` and ``workload install`` download
    packages or tools. ``build``, ``run`` and ``test`` restore the
    project's own packages only when the NuGet cache lacks them, which
    this does not tell; declare ``dotnet restore`` in ``setup``.
    """
    plain = [a for a in rest if not a.startswith("-")]
    known = {"add", "build", "dnx", "exec", "package", "restore", "run"}
    known |= {"test", "tool", "workload"}
    at = next((i for i, a in enumerate(plain) if a in known), 0)
    sub = plain[at] if plain else ""
    after = plain[at + 1] if len(plain) > at + 1 else ""
    if sub == "run" and any(a.endswith(".cs") for a in plain[1:]):
        return "dotnet run on a .cs file restores its #:package lines"
    if sub == "dnx":
        return "dotnet dnx downloads a tool and runs it"
    if sub == "tool" and after in {"install", "update", "exec"}:
        return f"dotnet tool {after} downloads a tool"
    if sub == "workload" and after in {"install", "update", "restore"}:
        return f"dotnet workload {after} downloads workloads"
    if sub in {"add", "package", "restore"}:
        return f"dotnet {sub} downloads packages"
    return None


def _bundle_fetch_reason(rest: Sequence[str]) -> str | None:
    """Return why a ``bundle`` command downloads gems, or ``None``."""
    sub = next((a for a in rest if not a.startswith("-")), "")
    if not sub:
        return "bundle with no command runs bundle install"
    if sub == "exec":
        return None
    named = {"add", "cache", "install", "lock", "outdated", "remove", "update"}
    if sub in named or (len(sub) > 0 and "install".startswith(sub)):
        return f"bundle {sub} downloads gems"
    return None


def _gem_fetch_reason(rest: Sequence[str]) -> str | None:
    """Return why a ``gem`` command downloads gems, or ``None``.

    Options before the subcommand are skipped, and RubyGems' unique
    prefixes (``gem inst``) count as the subcommand.
    """
    sub = next((a for a in rest if not a.startswith("-")), "")
    named = ("exec", "fetch", "install", "update")
    if sub and any(n.startswith(sub) for n in named):
        return f"gem {sub} downloads gems"
    return None


def _corepack_reason(
    rest: Sequence[str], env: Mapping[str, str], shim: str | None = None
) -> str | None:
    """Return why a ``corepack`` command may download, or ``None``.

    ``corepack enable`` and ``disable`` only write shims. A command
    that runs a package manager (``corepack pnpm ...``) downloads it
    only when it is not in corepack's cache, and never with
    ``COREPACK_ENABLE_NETWORK=0``. ``prepare``, ``install``, ``up``,
    ``use`` and ``pack`` download.
    """
    if env.get("COREPACK_ENABLE_NETWORK") == "0":
        return None
    words = [a for a in rest if not a.startswith("-")]
    sub = shim or (words[0] if words else "")
    if shim is None and sub in {"enable", "disable", ""}:
        return None
    if shim is not None or sub not in _COREPACK_DOWNLOADS:
        home = env.get("COREPACK_HOME") or str(
            Path(env.get("HOME", "~")).expanduser() / ".cache/node/corepack"
        )
        name = "yarn" if sub == "yarnpkg" else sub
        try:
            cache = Path(home) / "v1" / name
            if cache.is_dir() and any(cache.iterdir()):
                return None
        except OSError:
            pass
    return (
        f"corepack {sub} may download the package manager, which is not "
        "in corepack's cache"
    )


def _without_options(args: Sequence[str]) -> Sequence[str]:
    """Drop the options in front of a subcommand and their values."""
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] != "--":
        i += 2 if args[i] in _VALUE_OPTIONS else 1
    return args[i:]


def _npx_reason(tool: str, rest: Sequence[str], cwd: Path) -> str | None:
    """Return why ``npx`` or ``npm exec`` would fetch, or ``None``."""
    no_fetch = False
    command: list[str] = []
    skip = False
    for i, arg in enumerate(rest):
        if skip:
            skip = False
            continue
        if arg == "--":
            command = list(rest[i + 1 :])
            break
        if arg in {"--no", "--no-install", "--offline"}:
            no_fetch = True
        elif arg in {"-p", "--package"} or arg.startswith("--package="):
            return f"{tool} -p names a package to download"
        elif arg in _VALUE_OPTIONS:
            skip = True
        elif not arg.startswith("-"):
            command = list(rest[i:])
            break
    if no_fetch or not command or command[0].startswith(_PATH_PREFIXES):
        return None
    bins = [
        str(directory / "node_modules" / ".bin")
        for directory in (cwd, *cwd.parents)
    ]
    if _which(command[0], {"PATH": os.pathsep.join(bins)}, cwd) is not None:
        return None
    return (
        f"{tool} {command[0]} is not installed in any node_modules/.bin, so "
        "it would be downloaded and run"
    )


_WRAP_VALUE_OPTIONS: Final[Mapping[str, frozenset[str]]] = {
    "exec": frozenset({"-a"}),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
}
_PASS_THROUGH: Final = frozenset(
    {"command", "exec", "nice", "nohup", "setsid", "stdbuf", "time", "timeout"}
)


def _in_tree(path: Path, tree: Path | None) -> bool:
    """Whether a file, as spelled or once resolved, is in the tree."""
    if tree is None:
        return False
    root = tree.resolve()
    return path.absolute().is_relative_to(
        root
    ) or path.resolve().is_relative_to(root)


def unwrap_launch(
    argv: Sequence[str],
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    tree: Path | None = None,
) -> list[str]:
    """Strip simple wrappers in front of the command that really runs.

    ``timeout``, ``nice``, ``nohup``, ``command``, ``exec``, ``time``,
    ``stdbuf`` and ``setsid`` start another program with their own
    options in front of it; ``timeout`` also takes a duration. They are
    peeled off, outermost first, so the runtime gate identifies the
    program that runs, not the wrapper. ``env`` is followed by the
    launch chain itself. A wrapper is peeled only when it resolves, on
    ``env``'s ``PATH`` from ``cwd``, to a file outside ``tree``: a file
    in the tree is code under test, so it is identified as the program
    it is (a script, hence unknown), never skipped. ``command -v``,
    ``-V`` and ``-p`` are not peeled. A wrapper whose options are not
    understood is left as it is.

    Parameters
    ----------
    argv : sequence of str
        The launch argv.
    env : Mapping of str to str, optional
        Environment whose ``PATH`` resolves each wrapper. With no
        ``env`` and ``cwd`` nothing is resolved and wrappers are
        peeled by name.
    cwd : Path, optional
        Working directory the command starts in.
    tree : Path, optional
        Root of the tree the command runs in.

    Returns
    -------
    list of str
        The inner argv, or a copy of ``argv`` when nothing was peeled.
    """
    args = list(argv)
    for _ in range(_MAX_LAUNCH_DEPTH):
        name = _canonical(Path(args[0]).name) if args else ""
        if name not in _PASS_THROUGH:
            break
        if env is not None and cwd is not None:
            found = _which(args[0], env, cwd)
            if found is None or _in_tree(found, tree):
                break
        if name == "command" and any(
            a in ("-v", "-V", "-p") for a in args[1:3]
        ):
            break
        values = _WRAP_VALUE_OPTIONS.get(name, frozenset())
        i = 1
        while i < len(args) and args[i].startswith("-") and args[i] != "--":
            i += 2 if args[i] in values else 1
        if i < len(args) and args[i] == "--":
            i += 1
        if name == "timeout":
            i += 1
        if i >= len(args):
            break
        args = args[i:]
    return args


def resolve_executable(
    spec: BenchmarkSpec, tree_root: Path, env: Mapping[str, str]
) -> Path | None:
    """Find the executable a tree would launch for the spec's command.

    Resolution follows ``execvpe``: a name with a slash is taken
    relative to the working directory, anything else is looked up on
    ``PATH``, with relative ``PATH`` entries taken relative to the
    working directory.

    Parameters
    ----------
    spec : BenchmarkSpec
        Supplies the command and working directory.
    tree_root : Path
        Root of the tree.
    env : mapping of str to str
        Environment the benchmark inherits; the spec's declared
        variables are applied on top, so a declared ``PATH`` wins.

    Returns
    -------
    Path or None
        Absolute path of the executable, symlinks not followed; ``None``
        if no executable file is found.
    """
    cwd = (tree_root / spec.cwd).absolute()
    return _which(spec.command[0], {**env, **spec.env}, cwd)


class _Launch:
    """Context for finding the interpreter behind one launch."""

    def __init__(self, tree_root: Path, cwd: Path, timeout: float) -> None:
        self.tree = tree_root.resolve()
        self.cwd = cwd
        self.timeout = timeout

    def identify(
        self, argv: Sequence[str], env: Mapping[str, str], depth: int = 0
    ) -> _Runtime:
        """Follow ``argv`` through launchers to the runtime that runs.

        Python, Node and Java interpreters are asked for their version
        (Node and Java also hashed); ``npx`` and ``npm exec`` are
        followed to their command. Scripts are followed through their
        shebang, ``env`` through its arguments, and ``uv run`` into
        uv's environment. A native executable built
        in the tree is a native build; every other case, including
        launchers this does not follow, is unknown.
        """
        argv = unwrap_launch(argv, env, self.cwd, self.tree)
        exe = _which(argv[0], env, self.cwd)
        if exe is None:
            return _Runtime.unknown(
                None, f"{argv[0]!r} is not an executable file"
            )
        real = exe.resolve()
        fetch = fetching_launch(argv, env, self.cwd, tree=self.tree)
        if fetch is not None:
            return _Runtime.unknown(
                real,
                f"this launcher may download code at run time: {fetch}",
            )
        names = {exe.name, real.name}
        if any(_is_python_name(name) for name in names):
            return self.python([str(exe)], argv[1:], env, label=str(exe))
        canon = {_canonical(name) for name in names}
        if canon & _NODE_NAMES:
            return self.node([str(exe)], env, str(exe))
        if "java" in canon:
            return self.java(exe, real, env)
        if "go" in canon and _native_kind(real) is not None:
            return self.go(exe, real, argv[1:], env)
        if "dotnet" in canon and _native_kind(real) is not None:
            return self.dotnet(exe, real, argv[1:], env)
        if any(_RUBY_NAME.fullmatch(name) for name in canon):
            if _ruby_searches_path(argv[1:]):
                return _Runtime.unknown(
                    real,
                    f"{exe} -S runs a program found on the PATH, not the "
                    "script named; it is not followed",
                )
            return self.ruby(exe, real, env)
        if canon & _BUNDLE_NAMES:
            return self.bundle(exe, real, argv[1:], env, depth)
        if (
            any(_COMPILER_NAME.fullmatch(c) for c in canon)
            and _native_kind(real) is not None
        ):
            return self.compiler(exe, real, env)
        if canon & _SHELL_NAMES:
            return _Runtime.unknown(
                real,
                f"{exe} is a shell; the program it runs is not the one "
                "launched",
            )
        if "uv" in names:
            return self.uv(exe, argv, env, depth)
        if "env" in names:
            return self.env(argv[1:], env, depth, label=str(exe))
        launcher = _canonical(exe.name)
        if launcher in _NODE_LAUNCHERS:
            return self.node_launcher(exe, launcher, argv[1:], env, depth)
        if canon & _RUST_TOOLCHAIN_NAMES:
            return _Runtime.unknown(
                real,
                f"{exe} picks a toolchain (rust-toolchain.toml, rustup) and "
                "may build before it starts the program, so the program is "
                "not the one launched and the build would be timed; build "
                "in setup and run the binary itself, such as "
                "target/release/<name>",
            )
        if any(_is_launcher_name(name) for name in names):
            return _Runtime.unknown(
                real,
                f"{exe} is a launcher that picks its own runtime, which is "
                "not followed",
            )
        try:
            with exe.open("rb") as handle:
                head = handle.read(_SHEBANG_BYTES)
        except OSError as exc:
            return _Runtime.unknown(real, f"{exe} cannot be read: {exc}")
        if head.startswith(b"#!"):
            return self.shebang(exe, head, argv, env, depth)
        kind = _native_kind(real)
        if kind is None:
            return _Runtime.unknown(
                real,
                f"{exe} is not a Python interpreter, a script with a "
                "shebang, uv run or a native executable; its runtime is "
                "unknown",
            )
        if not real.is_relative_to(self.tree):
            return _Runtime.unknown(
                real,
                f"{exe} is a native {kind} executable outside the tree and "
                "not a Python interpreter; its version is not probed",
            )
        installed = self.installed_in(real)
        if installed is not None:
            return _Runtime.unknown(
                real,
                f"{exe} is a native {kind} executable installed into the "
                f"tree ({installed}), not built from it",
            )
        return _Runtime("native_build", real)

    def report(
        self, argv: Sequence[str], env: Mapping[str, str]
    ) -> tuple[str, str, str | None]:
        """Run a version probe and return its stdout, stderr, failure.

        The probe runs in the benchmark's directory and environment,
        with stdin closed and a timeout, so a program that reads input
        or never answers fails instead of hanging. Output over
        `_PROBE_OUTPUT_CHARS` is a failure.
        """
        try:
            done = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=dict(env),
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return "", "", f"{' '.join(argv[:1])} could not run: {exc}"
        if max(len(done.stdout), len(done.stderr)) > _PROBE_OUTPUT_CHARS:
            return "", "", f"{argv[0]} printed more than a version report"
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip()[-200:]
            return "", "", f"{argv[0]} exited {done.returncode}: {tail}"
        return done.stdout, done.stderr, None

    def node(
        self, prefix: Sequence[str], env: Mapping[str, str], label: str
    ) -> _Runtime:
        """Ask the launch chain which Node it runs.

        ``prefix`` is the launcher (just ``node``, or ``npx
        --no-install`` and the like) up to and including the point
        where a command goes; ``node -e`` is appended, so whatever
        shim, version manager or launcher sits in front is the one
        asked. Node prints the real path of its own executable and its
        version; the version is followed by the SHA-256 of that file.
        The runtime is the executable that answered, not the one named.
        ``NODE_OPTIONS`` is removed from the probe's environment: a
        ``--require`` in it could print a forged report, or a
        harmless one could print an extra line. It is recorded
        separately as environment.

        Returns
        -------
        _Runtime
            An ``interpreter`` with family ``node``, or unknown with
            the reason: the probe could not run, timed out, failed,
            printed more than a report, or its executable cannot be
            read.
        """
        clean = {k: v for k, v in env.items() if k != "NODE_OPTIONS"}
        out, _, failure = self.report([*prefix, "-e", _NODE_PROBE], clean)
        if failure is not None:
            return _Runtime.unknown(
                None, f"{label} could not report its node version: {failure}"
            )
        lines = [line for line in out.split("\n") if line.strip()]
        if len(lines) != 2 or not Path(lines[0]).is_absolute():
            return _Runtime.unknown(
                None,
                f"{label} did not report an executable and a version: "
                f"{out.strip()[:200]!r}",
            )
        real = Path(lines[0])
        try:
            digest, _ = hash_file(real)
        except OSError as exc:
            return _Runtime.unknown(real, f"{real} cannot be hashed: {exc}")
        return _Runtime(
            "interpreter",
            real,
            f"{lines[1].strip()}; sha256 {digest}",
            None,
            "node",
        )

    def java(self, exe: Path, real: Path, env: Mapping[str, str]) -> _Runtime:
        """Identify a Java runtime by ``java -version`` and a hash.

        ``java -version`` reports on stderr; its ``Picked up ...``
        lines (from ``JAVA_TOOL_OPTIONS``) are dropped. The version is
        followed by the SHA-256 of the resolved launcher. The JDK's
        ``libjvm`` is not hashed.
        """
        out, err, failure = self.report([str(exe), "-version"], env)
        if failure is not None:
            return _Runtime.unknown(
                real, f"{exe} could not report its java version: {failure}"
            )
        lines = [
            line.strip()
            for line in (err + out).split("\n")
            if line.strip() and not line.startswith("Picked up ")
        ]
        if not lines:
            return _Runtime.unknown(
                real, f"{exe} -version exited 0 without reporting a version"
            )
        try:
            digest, _ = hash_file(real)
        except OSError as exc:
            return _Runtime.unknown(real, f"{real} cannot be hashed: {exc}")
        version = f"{' / '.join(lines)}; sha256 {digest}"
        return _Runtime("interpreter", real, version, None, "java")

    def ruby(self, exe: Path, real: Path, env: Mapping[str, str]) -> _Runtime:
        """Identify a Ruby by what it says it is, and a hash.

        The interpreter is asked for the real path of its own
        executable (``RbConfig.ruby``) and for ``RUBY_DESCRIPTION``,
        the line ``ruby -v`` prints, so a version manager's shim
        (rbenv, asdf, mise) reports the Ruby it starts, not itself. The
        version is that line followed by the SHA-256 of the executable
        that answered. ``RUBYOPT`` and ``RUBYLIB`` are removed from the
        probe's environment, as ``NODE_OPTIONS`` is for Node: a ``-r``
        or a forged ``rbconfig.rb`` could print a forged report. They
        are recorded separately as environment.

        Returns
        -------
        _Runtime
            An ``interpreter`` with family ``ruby``, or unknown with the
            reason: the probe could not run, failed, or did not print a
            path and a description.
        """
        clean = {
            k: v for k, v in env.items() if k not in ("RUBYOPT", "RUBYLIB")
        }
        out, _, failure = self.report([str(exe), "-e", _RUBY_PROBE], clean)
        if failure is not None:
            return _Runtime.unknown(
                real, f"{exe} could not report its ruby version: {failure}"
            )
        lines = [line for line in out.split("\n") if line.strip()]
        if len(lines) != 2 or not Path(lines[0]).is_absolute():
            return _Runtime.unknown(
                real,
                f"{exe} did not report an executable and a version: "
                f"{out.strip()[:200]!r}",
            )
        found = Path(lines[0])
        try:
            digest, _ = hash_file(found)
        except OSError as exc:
            return _Runtime.unknown(found, f"{found} cannot be hashed: {exc}")
        return _Runtime(
            "interpreter",
            found,
            f"{lines[1].strip()}; sha256 {digest}",
            None,
            "ruby",
        )

    def bundle(
        self,
        exe: Path,
        real: Path,
        args: Sequence[str],
        env: Mapping[str, str],
        depth: int,
    ) -> _Runtime:
        """Follow ``bundle exec COMMAND`` to its command.

        Bundler starts the command through ``PATH`` in the Ruby
        environment it sets up, so the command is identified as it
        would be launched directly. Every other subcommand (``install``,
        ``update``, ``add``, ``config``) is unknown: it fetches gems or
        starts nothing that is the code under test. A shim that is not
        followed to a Ruby stays unknown through the command's own
        identification.
        """
        if not args or args[0] != "exec":
            sub = args[0] if args else "with no command"
            return _Runtime.unknown(
                real, f"{exe} {sub}: only bundle exec is identified"
            )
        command = list(args[1:])
        if not command or command[0].startswith("-"):
            return _Runtime.unknown(
                real, f"{exe} exec names no command, or starts with an option"
            )
        if depth >= _MAX_LAUNCH_DEPTH:
            return _Runtime.unknown(real, f"{exe}: too many nested launchers")
        return self.identify(command, env, depth + 1).explained(f"{exe} exec")

    def dotnet(
        self,
        exe: Path,
        real: Path,
        args: Sequence[str],
        env: Mapping[str, str],
    ) -> _Runtime:
        """Identify the .NET SDK and runtimes a ``dotnet`` command uses.

        Only ``build``, ``run``, ``test`` and ``exec``, and a path to a
        ``.dll``, are followed (`_DOTNET_SUBCOMMANDS`). The host is
        asked as the benchmark would run it, in its working directory
        and environment: ``dotnet --version`` names the SDK that
        ``global.json`` selects (and fails when it asks for one that is
        not installed), and ``dotnet --list-runtimes`` the runtimes
        installed, which is what a project's target framework rolls
        forward to. The version is both, followed by the SHA-256 of the
        ``dotnet`` host. A ``.dll`` or ``exec`` needs no SDK, so only
        the runtimes are asked. A ``dotnet`` that is a script is never
        asked.

        Returns
        -------
        _Runtime
            An ``interpreter`` with family ``dotnet``, or unknown with
            the reason.
        """
        sub = args[0] if args else ""
        is_app = sub == "exec" or sub.endswith(".dll")
        if sub not in _DOTNET_SUBCOMMANDS and not is_app:
            return _Runtime.unknown(
                real,
                f"{exe} {sub}: only dotnet build, run, test and exec and a "
                "path to a .dll are identified; other subcommands start "
                "other programs or fetch code",
            )
        parts: list[str] = []
        if not is_app:
            sdk, _, failure = self.report([str(exe), "--version"], env)
            if failure is not None:
                return _Runtime.unknown(
                    real,
                    f"{exe} could not report its sdk version: {failure}",
                )
            parts.append(f"sdk {sdk.strip()}")
        listed, _, failure = self.report([str(exe), "--list-runtimes"], env)
        if failure is not None:
            return _Runtime.unknown(
                real, f"{exe} could not list its runtimes: {failure}"
            )
        runtimes = sorted(
            _RUNTIME_PATH.sub("", line)
            for line in listed.split("\n")
            if line.strip()
        )
        if not runtimes or any(len(line.split()) != 2 for line in runtimes):
            return _Runtime.unknown(
                real,
                f"{exe} --list-runtimes did not print name and version "
                f"lines: {listed.strip()[:200]!r}",
            )
        parts.append("runtimes " + ", ".join(runtimes))
        try:
            digest, _ = hash_file(real)
        except OSError as exc:
            return _Runtime.unknown(real, f"{real} cannot be hashed: {exc}")
        parts.append(f"sha256 {digest}")
        return _Runtime("interpreter", real, "; ".join(parts), None, "dotnet")

    def compiler(
        self, exe: Path, real: Path, env: Mapping[str, str]
    ) -> _Runtime:
        """Identify the C or C++ compiler a driver command runs.

        The driver is asked for ``--version`` and ``-dumpmachine`` as
        the benchmark would run it. A banner with an ``InstalledDir``
        line (clang prints one; Apple's ``/usr/bin/clang`` is a shim
        that runs the compiler in the Command Line Tools) is hashed at
        that directory's file of the resolved name when it is a native
        file, else the resolved driver is. A driver that is a script
        (ccache's wrapper, a distribution's alternatives script) is
        never asked: the compiler it runs is not the one launched.

        Returns
        -------
        _Runtime
            An ``interpreter`` with family ``cc``, whose version is the
            banner's first line, the target and the SHA-256 of the
            compiler file; or unknown with the reason.
        """
        out, _, failure = self.report([str(exe), "--version"], env)
        if failure is not None:
            return _Runtime.unknown(
                real, f"{exe} could not report its version: {failure}"
            )
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if not lines:
            return _Runtime.unknown(real, f"{exe} --version printed nothing")
        machine, _, failure = self.report([str(exe), "-dumpmachine"], env)
        if failure is not None:
            return _Runtime.unknown(
                real, f"{exe} could not report its target: {failure}"
            )
        target = machine.strip()
        hashed = real
        banner = _COMPILER_BANNER.search(out)
        if banner is not None:
            candidate = Path(banner.group("dir").strip()) / real.name
            if _native_kind(candidate.resolve()) is not None:
                hashed = candidate.resolve()
        try:
            digest, _ = hash_file(hashed)
        except OSError as exc:
            return _Runtime.unknown(
                hashed, f"{hashed} cannot be hashed: {exc}"
            )
        text = f"{lines[0]}; target {target}; sha256 {digest}"
        return _Runtime("interpreter", hashed, text, None, "cc")

    def go(
        self,
        exe: Path,
        real: Path,
        args: Sequence[str],
        env: Mapping[str, str],
    ) -> _Runtime:
        """Identify the Go toolchain a ``go`` command runs.

        Only ``go run``, ``test``, ``build`` and ``vet`` are followed
        (`_GO_SUBCOMMANDS`). The ``go`` command is asked, as the
        benchmark would run it, in its working directory and
        environment. ``go list -f '{{.Root}}' runtime`` names the root
        of the toolchain that would run: unlike ``go env`` and ``go
        version``, ``go list`` is a command that switches toolchain when
        ``go.mod``'s ``go`` or ``toolchain`` line asks for another one
        (``GOTOOLCHAIN``), and that fails when it cannot. A switch to a
        toolchain that is not yet in the module cache is downloaded by
        the probe, as the first run of the benchmark would. That
        toolchain's own ``bin/go`` is then asked for its settings
        (``go env -json``) and hashed; it is the runtime. A ``go`` that
        is a script (a version manager's shim, or a launcher that runs
        Go in a container) is never asked, and a root whose ``bin/go``
        is not a native file on this machine is unknown.

        Returns
        -------
        _Runtime
            An ``interpreter`` with family ``go``, whose version is
            ``go1.25.14 linux/arm64`` followed by the settings that
            change generated code and the SHA-256 of the toolchain's
            ``go``; or unknown with the reason.
        """
        sub = args[0] if args else ""
        if sub not in _GO_SUBCOMMANDS:
            return _Runtime.unknown(
                real,
                f"{exe} {sub}: only go run, test, build and vet are "
                "identified; other subcommands start other programs or "
                "fetch code",
            )
        listed, _, failure = self.report(
            [str(exe), "list", "-f", "{{.Root}}", "runtime"], env
        )
        if failure is not None:
            return _Runtime.unknown(
                real, f"{exe} could not report its go root: {failure}"
            )
        toolchain = Path(listed.strip()) / "bin" / "go"
        found = toolchain.resolve()
        if (
            "\n" in listed.strip()
            or not toolchain.is_absolute()
            or _native_kind(found) is None
        ):
            return _Runtime.unknown(
                real,
                f"{exe} reports the go root {listed.strip()[:200]!r}, which "
                "has no native bin/go on this machine, so the toolchain "
                "that runs is not the one found here",
            )
        out, _, failure = self.report(
            [str(toolchain), "env", "-json", *_GO_ENV_NAMES], env
        )
        if failure is not None:
            return _Runtime.unknown(
                found,
                f"{toolchain} could not report its go version: {failure}",
            )
        try:
            loaded: object = json.loads(out)
        except ValueError:
            loaded = None
        info: dict[str, str] = (
            {
                k: v
                for k, v in cast("dict[object, object]", loaded).items()
                if isinstance(k, str) and isinstance(v, str)
            }
            if isinstance(loaded, dict)
            else {}
        )
        if not info:
            return _Runtime.unknown(
                real,
                f"{exe} env -json did not print a JSON object of strings: "
                f"{out.strip()[:200]!r}",
            )
        version = info.get("GOVERSION", "")
        if not version:
            return _Runtime.unknown(
                found, f"{toolchain} env did not report GOVERSION"
            )
        try:
            digest, _ = hash_file(found)
        except OSError as exc:
            return _Runtime.unknown(found, f"{found} cannot be hashed: {exc}")
        settings = [
            f"{name}={info[name]}"
            for name in (
                "CGO_ENABLED",
                "GOAMD64",
                "GOARM64",
                "GOEXPERIMENT",
                "GOFLAGS",
            )
            if info.get(name)
        ]
        platform = f"{info.get('GOOS', '?')}/{info.get('GOARCH', '?')}"
        text = "; ".join(
            [f"{version} {platform}", *settings, f"sha256 {digest}"]
        )
        return _Runtime("interpreter", found, text, None, "go")

    def node_launcher(
        self,
        exe: Path,
        tool: str,
        args: Sequence[str],
        env: Mapping[str, str],
        depth: int,
    ) -> _Runtime:
        """Follow ``npx`` and ``npm exec`` to the Node they run.

        The command is first identified on a ``PATH`` with every
        ``node_modules/.bin`` from the working directory upward in
        front, to learn what it is (a Node script, a Python script,
        a native program). When it is Node, the Node is not derived
        from that: the launcher itself is asked, with ``--no-install``
        forced, to run ``node -e`` in the benchmark's directory and
        environment, so ``.npmrc`` settings, ``script-shell`` and
        local ``node_modules/.bin/node`` apply exactly as they do to
        the benchmark. ``npx`` and ``npm exec`` fetch a package that is
        not installed, so they are followed only when the command is
        present locally or ``--no-install`` forbids fetching. Every
        other launcher, and every other subcommand, option or
        form, is refused.
        """
        label = tool
        rest = list(args)
        if tool != "npx":
            if not rest or rest[0] != "exec":
                sub = rest[0] if rest else ""
                return _Runtime.unknown(
                    exe.resolve(),
                    f"{exe} {sub}: package scripts and other "
                    f"subcommands are not followed; only '{tool} exec' is",
                )
            label = f"{tool} exec"
            rest = rest[1:]
        no_fetch = False
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg == "--":
                i += 1
                break
            if arg in _NO_FETCH_FLAGS:
                no_fetch = True
            elif arg.startswith("-"):
                return _Runtime.unknown(
                    exe.resolve(),
                    f"{label}: option {arg!r} is not understood",
                )
            else:
                break
            i += 1
        command = rest[i:]
        if not command:
            return _Runtime.unknown(
                exe.resolve(), f"{label} names no command to run"
            )
        if depth >= _MAX_LAUNCH_DEPTH:
            return _Runtime.unknown(
                exe.resolve(), f"{label}: too many nested launchers"
            )
        bins = [
            str(directory / "node_modules" / ".bin")
            for directory in (self.cwd, *self.cwd.parents)
        ]
        local = _which(command[0], {"PATH": os.pathsep.join(bins)}, self.cwd)
        if not no_fetch and "/" not in command[0] and local is None:
            return _Runtime.unknown(
                exe.resolve(),
                f"{label} {command[0]}: not installed in any "
                "node_modules/.bin, so it would be fetched and run; add "
                "--no-install to refuse the fetch",
            )
        path = os.pathsep.join([*bins, env.get("PATH", os.defpath)])
        found = self.identify(command, {**env, "PATH": path}, depth + 1)
        if found.family != "node":
            return found.explained(label)
        probe = (
            [str(exe), "--no-install", "node"]
            if tool == "npx"
            else [str(exe), "exec", "--no", "--", "node"]
        )
        probed = self.node(probe, env, label)
        if probed.kind == "interpreter" and (
            found.executable,
            found.version,
        ) != (probed.executable, probed.version):
            return _Runtime.unknown(
                exe.resolve(),
                f"{label}: the command runs {found.executable} "
                f"({found.version}), but the launcher's node is "
                f"{probed.executable}",
            )
        return probed

    def installed_in(self, path: Path) -> str | None:
        """Name the virtualenv or package directory ``path`` is in."""
        for parent in path.parents:
            if parent == self.tree or not parent.is_relative_to(self.tree):
                break
            if parent.name in _INSTALL_DIRS:
                return f"{parent.name}/"
            if (parent / "pyvenv.cfg").is_file():
                return f"virtualenv {parent.relative_to(self.tree)}"
        return None

    def shebang(
        self,
        exe: Path,
        head: bytes,
        argv: Sequence[str],
        env: Mapping[str, str],
        depth: int,
    ) -> _Runtime:
        """Follow a script's ``#!`` line to its interpreter.

        The kernel does not search ``PATH`` for an interpreter: a name
        without a slash, as in ``#!python3``, is a path relative to the
        working directory, and is resolved there or not at all.
        """
        line = head[2:].split(b"\n", 1)[0]
        words = line.decode("utf-8", errors="surrogateescape").split()
        if not words:
            return _Runtime.unknown(exe.resolve(), f"{exe} has an empty #!")
        if depth >= _MAX_LAUNCH_DEPTH:
            return _Runtime.unknown(
                exe.resolve(), f"{exe}: too many nested #! launchers"
            )
        if "/" not in words[0]:
            words[0] = f"./{words[0]}"
        found = self.identify([*words, str(exe), *argv[1:]], env, depth + 1)
        return found.explained(str(exe))

    def env(
        self,
        args: Sequence[str],
        env: Mapping[str, str],
        depth: int,
        *,
        label: str,
    ) -> _Runtime:
        """Follow ``env [-i] [-u NAME] [-S] [NAME=VALUE]... PROG``."""
        current = dict(env)
        rest = list(args)
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg in ("-i", "--ignore-environment", "-"):
                current = {}
            elif arg in ("-S", "--split-string") and i + 1 < len(rest):
                rest[i + 1 : i + 2] = rest[i + 1].split()
            elif arg in ("-u", "--unset") and i + 1 < len(rest):
                current.pop(rest[i + 1], None)
                i += 1
            elif arg.startswith("--unset="):
                current.pop(arg.removeprefix("--unset="), None)
            elif arg.startswith("-"):
                return _Runtime.unknown(
                    None, f"{label}: option {arg!r} is not understood"
                )
            elif "=" in arg:
                name, value = arg.split("=", 1)
                current[name] = value
            else:
                return self.identify(rest[i:], current, depth).explained(label)
            i += 1
        return _Runtime.unknown(None, f"{label} names no program to run")

    def uv(
        self,
        exe: Path,
        argv: Sequence[str],
        env: Mapping[str, str],
        depth: int,
    ) -> _Runtime:
        """Follow ``uv run [OPTIONS] COMMAND`` inside uv's environment.

        Only ``run`` as uv's first argument is followed: global options
        before it, ``uv tool run`` and every other subcommand are
        unknown. ``-m MODULE`` and a ``.py`` command run uv's Python,
        and a ``python*`` command is run by uv itself; each is asked
        for its version through ``uv run`` with the same options. Any
        other command is resolved on the ``PATH`` uv gives its child and
        then followed exactly like a direct launch.
        """
        if len(argv) < 2 or argv[1] != "run":
            sub = " ".join(argv[1:3])
            return _Runtime.unknown(
                exe.resolve(),
                f"{exe} {sub}: only 'uv run' as uv's first argument is "
                "followed to an interpreter",
            )
        options: list[str] = []
        module = False
        i = 2
        while i < len(argv):
            arg = argv[i]
            if arg == "--":
                i += 1
                break
            if not arg.startswith("-") or arg == "-":
                break
            if arg in ("-m", "--module"):
                module = True
            elif arg in _UV_SCRIPT_OPTIONS:
                return _Runtime.unknown(
                    None,
                    f"uv run {arg}: the script's own environment is not "
                    "probed",
                )
            else:
                options.append(arg)
            if "=" not in arg and arg in _UV_VALUE_OPTIONS:
                if i + 1 >= len(argv):
                    return _Runtime.unknown(
                        None, f"uv run: {arg} has no value"
                    )
                options.append(argv[i + 1])
                i += 1
            i += 1
        if i >= len(argv):
            return _Runtime.unknown(None, "uv run names no command")
        command = argv[i]
        uv_run = [str(exe), "run", *options]
        if module:
            return self.python(
                [*uv_run, "python"], ["-m", command], env, label="uv run"
            )
        if _is_python_name(Path(command).name):
            return self.python(
                [*uv_run, command], argv[i + 1 :], env, label="uv run"
            )
        if command.endswith((".py", ".pyw")):
            if _has_inline_metadata(self.cwd / command):
                return _Runtime.unknown(
                    None,
                    f"uv run {command}: a script with inline metadata runs "
                    "in its own environment, which is not probed",
                )
            return self.python(
                [*uv_run, "python"], argv[i:], env, label="uv run"
            )
        lines, failure = self.run_probe(
            [*uv_run, "python"], env, _PATH_PROBE, label="uv run"
        )
        if failure is not None:
            return _Runtime.unknown(None, failure)
        found = self.identify(argv[i:], {**env, "PATH": lines[0]}, depth + 1)
        return found.explained("uv run")

    def run_probe(
        self,
        prefix: Sequence[str],
        env: Mapping[str, str],
        code: str,
        *,
        label: str,
    ) -> tuple[list[str], str | None]:
        """Run ``prefix -I -c code`` and return its non-empty lines.

        Returns
        -------
        tuple of (list of str, str or None)
            The output's lines, or an empty list and why it failed.
        """
        argv = [*prefix, "-I", "-c", code]
        try:
            done = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=dict(env),
                cwd=self.cwd,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [], f"{label} could not report its interpreter: {exc}"
        lines = [line for line in done.stdout.split("\n") if line.strip()]
        if done.returncode != 0 or not lines:
            return [], (
                f"{label} exited {done.returncode} without reporting its "
                f"interpreter: {done.stderr.strip()[-200:]}"
            )
        return lines, None

    def python(
        self,
        prefix: Sequence[str],
        args: Sequence[str],
        env: Mapping[str, str],
        *,
        label: str,
    ) -> _Runtime:
        """Identify the Python that ``prefix args`` would run.

        Every Python launch comes through here, so a ``-m`` launcher is
        refused in one place whatever led to it: a direct interpreter,
        a shebang, ``env``, ``uv run -m`` or ``uv run python -m``. Then
        ``prefix -I -c ...`` reports the interpreter.
        """
        launcher = _launcher_module(args)
        if launcher is not None:
            return _Runtime.unknown(
                None,
                f"{label} -m {launcher} runs a launcher that picks its own "
                "runtime, which is not followed",
            )
        lines, failure = self.run_probe(
            prefix, env, _INTERPRETER_PROBE, label=label
        )
        if failure is not None:
            return _Runtime.unknown(None, failure)
        if len(lines) < 2:
            return _Runtime.unknown(
                None, f"{label} did not report both executable and version"
            )
        version = "\n".join(lines[1:]).strip()
        return _Runtime("interpreter", Path(lines[0]).resolve(), version)


def probe_environment(
    version: Arm,
    spec: BenchmarkSpec,
    tree_root: Path,
    env: Mapping[str, str],
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> TreeEnvironment:
    """Record what one tree's benchmark runs with.

    The runtime is found by following the command through its
    launchers: a Python interpreter is asked directly; a script is
    followed through its ``#!`` line (including ``/usr/bin/env
    python3`` and a virtualenv's console scripts); ``env`` through its
    arguments; ``uv run`` by running ``uv run <same options> python -I
    -c ...`` in the tree, which may let uv create or sync the tree's
    environment exactly as the benchmark's own first run would. A
    native executable built in the tree is a ``native_build``, with no
    version; its outside-tree libraries are left to the runtime gate,
    which lists them from both trees. Anything else is unknown.

    Parameters
    ----------
    version : Arm
        Which tree this is.
    spec : BenchmarkSpec
        The spec in force.
    tree_root : Path
        Root of the tree.
    env : mapping of str to str
        Environment the benchmark inherits, before the spec's declared
        variables are applied.
    timeout : float, optional
        Time limit for each interpreter probe, in seconds.

    Returns
    -------
    TreeEnvironment
        The interpreter (or native executable) with symlinks followed
        and its ``sys.version``, every variable name, the values of
        `PERFORMANCE_ENV_VARS`, and the hashes of the
        `DEPENDENCY_LOCKFILES` at the tree root. What cannot be found
        is ``None`` with a reason in ``missing`` naming the launcher.
    """
    launch_env = {**env, **spec.env}
    cwd = (tree_root / spec.cwd).absolute()
    runtime = _Launch(tree_root, cwd, timeout).identify(
        spec.command, launch_env
    )
    missing: dict[str, str] = {}
    kind: RuntimeKind | None = None
    if runtime.kind == "native_build" and runtime.executable is not None:
        kind = "native_build"
        missing["linked_libraries"] = _LIBRARIES_AT_GATE
    elif (
        runtime.kind == "interpreter"
        and runtime.executable is not None
        and runtime.version is not None
    ):
        kind = "interpreter"
    else:
        reason = runtime.reason or "not identified"
        missing["runtime_kind"] = reason
        missing["linked_libraries"] = reason
        missing["runtime_version"] = reason
        if runtime.executable is None:
            missing["runtime_executable"] = reason
    locks: tuple[DependencyLock, ...] | None
    try:
        locks = tuple(
            DependencyLock(path=name, sha256=hash_file(tree_root / name)[0])
            for name in DEPENDENCY_LOCKFILES
            if (tree_root / name).is_file()
        )
    except OSError as exc:
        locks = None
        missing["dependency_locks"] = f"a lockfile cannot be read: {exc}"
    return TreeEnvironment(
        version=version,
        runtime_kind=kind,
        runtime_executable=(
            None if runtime.executable is None else str(runtime.executable)
        ),
        runtime_version=runtime.version if kind == "interpreter" else None,
        runtime_family=runtime.family if kind == "interpreter" else None,
        env_names=tuple(launch_env),
        env_values={
            k: v for k, v in launch_env.items() if k in PERFORMANCE_ENV_VARS
        },
        dependency_locks=locks,
        missing=missing,
    )


def environment_warnings(
    baseline: TreeEnvironment, candidate: TreeEnvironment
) -> tuple[RunWarning, ...]:
    """Warn about recorded-not-gated differences between the arms.

    Parameters
    ----------
    baseline, candidate : TreeEnvironment
        What each tree ran with.

    Returns
    -------
    tuple of RunWarning
        ``environment_differs`` for each variable set in only one arm or
        whose recorded value differs, ``dependency_lock_differs`` for
        each lockfile that differs or exists in only one tree, and
        ``environment_unknown`` when either side was not recorded. Empty
        when nothing differs.

    Raises
    ------
    ValueError
        If ``baseline`` and ``candidate`` are not the baseline and
        candidate environments.
    """
    if (baseline.version, candidate.version) != _ARMS:
        msg = (
            "expected the baseline and the candidate environment, got "
            f"{baseline.version} and {candidate.version}"
        )
        raise ValueError(msg)
    warnings = [
        RunWarning(
            code="environment_unknown",
            message=(
                f"{field} of the {env.version} was not recorded: "
                f"{env.missing.get(field, 'no reason given')}"
            ),
        )
        for field in ("env_names", "dependency_locks")
        for env in (baseline, candidate)
        if getattr(env, field) is None
    ]
    if baseline.env_names is not None and candidate.env_names is not None:
        b_names, c_names = set(baseline.env_names), set(candidate.env_names)
        for name in sorted(b_names ^ c_names):
            arm = "baseline" if name in b_names else "candidate"
            warnings.append(
                RunWarning(
                    code="environment_differs",
                    message=f"{name} is set only in the {arm}",
                )
            )
        for name in sorted(b_names & c_names):
            b = baseline.env_values.get(name)
            c = candidate.env_values.get(name)
            if b != c:
                warnings.append(
                    RunWarning(
                        code="environment_differs",
                        message=(
                            f"{name} is {b!r} in the baseline and {c!r} in "
                            "the candidate"
                        ),
                    )
                )
    if baseline.dependency_locks is not None and (
        candidate.dependency_locks is not None
    ):
        b_locks = {
            lock.path: lock.sha256 for lock in baseline.dependency_locks
        }
        c_locks = {
            lock.path: lock.sha256 for lock in candidate.dependency_locks
        }
        for path in sorted(b_locks.keys() | c_locks.keys()):
            b, c = b_locks.get(path), c_locks.get(path)
            if b != c:
                warnings.append(
                    RunWarning(
                        code="dependency_lock_differs",
                        message=(
                            f"{path} is {b or 'absent'} in the baseline and "
                            f"{c or 'absent'} in the candidate"
                        ),
                    )
                )
    return tuple(warnings)
