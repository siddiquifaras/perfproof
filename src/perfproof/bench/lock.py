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
  the command it runs); a runtime it cannot identify fails the gate,
  never passes it. A native executable built in the tree is judged by
  the libraries it links outside the tree and, when git tracks it, by
  its bytes (see `runtime_gate` for the rule and its stated limit).
- `environment_warnings`: everything recorded but not gated (the
  environment variable list, values of variables known to affect
  performance, dependency lockfiles) becomes a warning when the arms
  differ.

Each gate returns a `perfproof.schema.GateResult` and never raises for
a property of the trees it checks; it raises only for a malformed call.

Locked paths may name files or directories. A directory locks every
regular file below it except the byproducts in `IGNORED_NAMES`. Files
are hashed in fixed-size chunks, so memory stays bounded at
`LOCK_CHUNK_BYTES` whatever the size of the input data.
"""

import hashlib
import os
import posixpath
import re
import stat
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, get_args

from pydantic import ValidationError

from perfproof.bench.spec import normalize_launch_path, spec_command_hash
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
    "IGNORED_NAMES",
    "LOCK_CHUNK_BYTES",
    "MAX_DIFFERENCES",
    "PERFORMANCE_ENV_VARS",
    "PROBE_TIMEOUT_SECONDS",
    "LockError",
    "build_manifest",
    "environment_warnings",
    "hash_file",
    "lock_gate",
    "probe_environment",
    "procedure_gate",
    "resolve_executable",
    "runtime_gate",
]

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
_PT_DYNAMIC: Final = 2
_PT_INTERP: Final = 3
_LIBRARIES_AT_GATE: Final = (
    "not listed by the probe; the runtime gate lists them from both "
    "trees when it compares them"
)
"""Why a native build's `TreeEnvironment.linked_libraries` is unset."""
_LAUNCHER_NAMES: Final = frozenset(
    {
        "bun",
        "conda",
        "deno",
        "hatch",
        "invoke",
        "mamba",
        "micromamba",
        "npm",
        "nox",
        "npx",
        "pdm",
        "pip-run",
        "pipenv",
        "pipx",
        "pixi",
        "pnpm",
        "poetry",
        "rye",
        "tox",
        "uvx",
        "yarn",
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


def _compare_native(
    rel: str,
    spec: BenchmarkSpec,
    envs: Mapping[Arm, TreeEnvironment],
    roots: Mapping[Arm, Path],
) -> tuple[list[str], list[Difference], str]:
    """Compare a native executable built at ``rel`` in each tree.

    Returns
    -------
    tuple of (list of str, list of Difference, str)
        Problems, differences, and the detail to report if none.
    """
    problems: list[str] = []
    differences: list[Difference] = []
    paths = {arm: Path(str(envs[arm].runtime_executable)) for arm in _ARMS}
    status = {arm: _is_tracked(paths[arm]) for arm in _ARMS}
    for arm, state in status.items():
        if isinstance(state, str):
            problems.append(
                f"whether git tracks the {arm} {rel} is unknown: {state}"
            )
    tracked = any(state is True for state in status.values())
    if tracked:
        try:
            shas = {arm: hash_file(paths[arm])[0] for arm in _ARMS}
        except OSError as exc:
            shas = {}
            problems.append(f"{rel} cannot be read: {exc}")
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
            problems.append(
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
    how = (
        "tracked by git and identical in both trees"
        if tracked
        else "untracked, so treated as a build of the code under test and "
        "not compared itself"
    )
    shared = "; ".join(
        f"{name} = {value}"
        for name, value in sorted(libraries.get("baseline", {}).items())
    )
    detail = (
        f"{rel} is a native executable built in each tree ({how}); it has "
        "no interpreter to compare; libraries outside the tree match: "
        f"{shared or 'none'}"
    )
    return problems, differences, detail


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
        interpreter at the same version. Failed when they differ, with
        differences ``runtime_executable`` and ``runtime_version``, and
        failed closed when either tree's executable or version is
        unknown, with the launcher and reason in the detail.

        Only positively identified runtimes pass. The kind comes from
        the probe's record, never from where the file sits: a known
        launcher (``uv`` other than ``uv run``, ``uvx``, ``poetry``
        and the like), or a native executable installed into the tree
        (inside a virtualenv, ``site-packages`` or ``node_modules``),
        is unknown and fails closed.

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
          are compared by path. A closure that cannot be listed fails
          closed.
        - any loader variable (``DYLD_*``, ``LD_*``) in the benchmark's
          environment, or an environment whose names were not recorded,
          fails closed: their effect on resolution is not followed.
        - if git tracks the executable in either tree (asked from the
          file's own directory, so submodules count), its bytes must be
          identical: a tracked binary that differs is a swap, not a
          build of the code under test. Any git answer other than a
          clear tracked or untracked fails closed.
        - an untracked executable is taken to be a build of the code
          under test and its own bytes are not compared. This is a
          stated limit: a different untracked interpreter copied into
          the tree under another name passes if it links the same
          outside libraries, and the detail says the binary was not
          compared.

        Under the spec's ``allow_runtime_change`` every failure is
        waived instead, with the same detail.

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
    differences = [
        Difference(item=item, baseline=b, candidate=c)
        for item, b, c in (
            ("runtime_executable", b_exe, c_exe),
            ("runtime_version", b_ver, c_ver),
        )
        if b != c
    ]
    problems: list[str] = []
    if differences:
        problems.append("the trees run different runtimes")
    for arm, unknown in (("baseline", b_unknown), ("candidate", c_unknown)):
        if unknown is not None:
            problems.append(f"the {arm} runtime is unknown: {unknown}")
    passed = f"both trees run {b_exe}, version {b_ver}"
    if b_native and c_native and b_exe == c_exe and b_exe is not None:
        native_problems, native_differences, passed = _compare_native(
            b_exe,
            spec,
            {"baseline": baseline, "candidate": candidate},
            {"baseline": baseline_root, "candidate": candidate_root},
        )
        problems.extend(native_problems)
        differences.extend(native_differences)
    if problems and spec.allow_runtime_change:
        return GateResult(
            gate="runtime",
            status="waived",
            detail="allow_runtime_change: " + "; ".join(problems),
            differences=tuple(differences),
        )
    return _gate("runtime", problems, differences, passed_detail=passed)


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

        Python interpreters are asked for their version. Scripts are
        followed through their shebang, ``env`` through its arguments,
        and ``uv run`` into uv's environment. A native executable built
        in the tree is a native build; every other case, including
        launchers this does not follow, is unknown.
        """
        exe = _which(argv[0], env, self.cwd)
        if exe is None:
            return _Runtime.unknown(
                None, f"{argv[0]!r} is not an executable file"
            )
        real = exe.resolve()
        names = {exe.name, real.name}
        if any(_is_python_name(name) for name in names):
            return self.python([str(exe)], argv[1:], env, label=str(exe))
        if "uv" in names:
            return self.uv(exe, argv, env, depth)
        if "env" in names:
            return self.env(argv[1:], env, depth, label=str(exe))
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
