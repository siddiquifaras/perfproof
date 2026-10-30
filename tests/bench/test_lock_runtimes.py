"""The runtime gate for Node and Java, against the real runtimes.

Node comes from nvm (or the ``PATH``); Java from whatever ``java`` the
machine has. Each test skips with its reason where the runtime is
absent. ``npx`` and ``npm`` are the real ones next to the node
under test; ``pnpm``, ``yarn`` and the rest are refused by name
and never run, so a file of that name stands in for them. Some
tests use a stub named ``npx`` or ``npm`` that records its argv
and then runs the real node. Nothing here uses the network.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from perfproof.bench.lock import (
    fetching_launch,
    probe_environment,
    runtime_gate,
    unwrap_launch,
)
from perfproof.schema import Arm, BenchmarkSpec, GateResult, TreeEnvironment


def make_spec(command: tuple[str, ...], **overrides: object) -> BenchmarkSpec:
    fields: dict[str, object] = {
        "name": "bench-node",
        "command": command,
        "metric": {
            "kind": "wall",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": {"command": ("true",), "timeout_seconds": 60.0},
        "warmup": 1,
        "budget": {"max_rounds": 20, "max_seconds": 600.0},
        "looks": {"n0": 10, "k": 10, "max_looks": 3},
        "timeout_seconds": 30.0,
        "harness": ("bench.js",),
        "oracle": ("oracle.js",),
        "inputs": ("input.js",),
        "config": ("config.js",),
    }
    fields.update(overrides)
    return BenchmarkSpec.model_validate(fields)


def node_binaries() -> list[Path]:
    """Real node executables, newest version directory last."""
    found = sorted(
        Path.home().glob(".nvm/versions/node/*/bin/node"),
        key=lambda p: [int(x) for x in p.parts[-3].lstrip("v").split(".")],
    )
    on_path = shutil.which("node")
    if on_path is not None:
        found.append(Path(on_path))
    unique: dict[Path, None] = {}
    for path in found:
        unique[path.resolve()] = None
    return list(unique)


@pytest.fixture(scope="session")
def node() -> Path:
    found = node_binaries()
    if not found:
        pytest.skip("no node executable under ~/.nvm or on the PATH")
    return found[-1]


@pytest.fixture(scope="session")
def other_node(node: Path) -> Path:
    versions = {node_version(p): p for p in node_binaries()}
    others = [p for v, p in versions.items() if p != node and v is not None]
    if not others:
        pytest.skip("only one node version is installed")
    return others[0]


def node_version(path: Path) -> str | None:
    try:
        done = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def java_report() -> tuple[Path, int, str] | None:
    """The machine's ``java``, its ``-version`` status and output."""
    found = shutil.which("java")
    if found is None:
        return None
    done = subprocess.run(
        [found, "-version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return Path(found), done.returncode, done.stderr + done.stdout


def tree(root: Path, name: str) -> Path:
    path = root / name
    (path / "bin").mkdir(parents=True)
    (path / "bench.js").write_text("console.log(1);\n")
    return path


def place(root: Path, runtime: Path, name: str, *, copy: bool) -> None:
    """Put a runtime at ``bin/<name>``, as a symlink or a copy."""
    target = root / "bin" / name
    if copy:
        shutil.copy2(runtime, target)
    else:
        target.symlink_to(runtime)


def perturb(path: Path) -> None:
    """Change an executable's bytes so that it still runs.

    On macOS the copy is re-signed ad hoc, which rewrites its code
    signature; elsewhere one trailing zero byte is appended, which an
    ELF loader ignores.
    """
    if sys.platform == "darwin":
        subprocess.run(
            ["codesign", "--force", "-s", "-", str(path)],
            check=True,
            capture_output=True,
            timeout=60,
        )
    else:
        with path.open("ab") as handle:
            handle.write(b"\0")


def probe(
    arm: Arm,
    spec: BenchmarkSpec,
    root: Path,
    *,
    path: str | None = None,
    timeout: float = 30.0,
    extra: dict[str, str] | None = None,
) -> TreeEnvironment:
    env = {
        "PATH": path if path is not None else os.environ["PATH"],
        "HOME": os.environ["HOME"],
        **(extra or {}),
    }
    return probe_environment(arm, spec, root, env, timeout=timeout)


def gate(
    spec: BenchmarkSpec,
    base: Path,
    cand: Path,
    *,
    path: str | None = None,
    cand_path: str | None = None,
    timeout: float = 30.0,
    base_extra: dict[str, str] | None = None,
    cand_extra: dict[str, str] | None = None,
) -> GateResult:
    return runtime_gate(
        spec,
        probe(
            "baseline",
            spec,
            base,
            path=path,
            timeout=timeout,
            extra=base_extra,
        ),
        probe(
            "candidate",
            spec,
            cand,
            path=cand_path if cand_path is not None else path,
            timeout=timeout,
            extra=cand_extra,
        ),
        baseline_root=base,
        candidate_root=cand,
    )


NODE_COMMAND = ("bin/node", "bench.js")


def test_node_script_passes_with_the_same_node(
    tmp_path: Path, node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    place(base, node, "node", copy=False)
    place(cand, node, "node", copy=False)
    spec = make_spec(NODE_COMMAND)

    env = probe("baseline", spec, base)
    result = gate(spec, base, cand)

    assert env.runtime_kind == "interpreter"
    assert env.runtime_executable == str(node)
    assert env.runtime_version is not None
    assert env.runtime_version.startswith(f"{node_version(node)}; sha256 ")
    assert env.runtime_family == "node"
    assert result.status == "passed", result.detail


def test_node_found_on_the_path_passes(tmp_path: Path, node: Path) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(("node", "bench.js"))
    path = f"{node.parent}{os.pathsep}{os.environ['PATH']}"

    assert gate(spec, base, cand, path=path).status == "passed"


def test_node_version_differing_between_arms_fails(
    tmp_path: Path, node: Path, other_node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    place(base, node, "node", copy=False)
    place(cand, other_node, "node", copy=False)

    result = gate(make_spec(NODE_COMMAND), base, cand)

    assert result.status == "failed"
    items = {d.item: d for d in result.differences}
    assert items["runtime_executable"].baseline == str(node)
    assert items["runtime_executable"].candidate == str(other_node)
    assert (items["runtime_version"].baseline or "").startswith(
        str(node_version(node))
    )
    assert (items["runtime_version"].candidate or "").startswith(
        str(node_version(other_node))
    )


def test_node_binary_differing_only_in_bytes_fails(
    tmp_path: Path, node: Path
) -> None:
    """Same relative path, same reported version, different bytes.

    Both trees hold a real copy of the same node at ``bin/node``; one
    copy is then re-signed (macOS) or has a byte appended (elsewhere).
    Both still report the same version, so only the hash can tell them
    apart.
    """
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    place(base, node, "node", copy=True)
    place(cand, node, "node", copy=True)
    spec = make_spec(NODE_COMMAND)
    assert gate(spec, base, cand).status == "passed"

    perturb(cand / "bin" / "node")
    result = gate(spec, base, cand)

    assert result.status == "failed"
    items = {d.item: d for d in result.differences}
    assert set(items) == {"runtime_version"}
    base_version = items["runtime_version"].baseline or ""
    cand_version = items["runtime_version"].candidate or ""
    assert base_version.split(";")[0] == cand_version.split(";")[0]
    assert base_version != cand_version


def test_node_binary_difference_is_waived_with_the_opt_in(
    tmp_path: Path, node: Path, other_node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    place(base, node, "node", copy=False)
    place(cand, other_node, "node", copy=False)

    result = gate(
        make_spec(NODE_COMMAND, allow_runtime_change=True), base, cand
    )

    assert result.status == "waived"


def write_shim(root: Path, name: str, shebang: str) -> None:
    shim = root / "node_modules" / ".bin" / name
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(f"#!{shebang}\nconsole.log(1);\n")
    shim.chmod(0o755)


def test_node_modules_shim_is_followed_to_node(
    tmp_path: Path, node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        write_shim(root, "tsx", "/usr/bin/env node")
    spec = make_spec(("node_modules/.bin/tsx", "bench.js"))
    path = f"{node.parent}{os.pathsep}{os.environ['PATH']}"

    env = probe("baseline", spec, base, path=path)
    result = gate(spec, base, cand, path=path)

    assert env.runtime_executable == str(node)
    assert result.status == "passed", result.detail


def test_shim_runs_the_node_of_each_arms_path(
    tmp_path: Path, node: Path, other_node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        write_shim(root, "tsx", "/usr/bin/env node")
    spec = make_spec(("node_modules/.bin/tsx", "bench.js"))
    rest = os.environ["PATH"]

    result = runtime_gate(
        spec,
        probe("baseline", spec, base, path=f"{node.parent}:{rest}"),
        probe("candidate", spec, cand, path=f"{other_node.parent}:{rest}"),
        baseline_root=base,
        candidate_root=cand,
    )

    assert result.status == "failed"
    assert {d.item for d in result.differences} >= {"runtime_version"}


def test_shim_written_in_sh_stays_unknown(tmp_path: Path, node: Path) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        write_shim(root, "tsx", "/bin/sh")
    spec = make_spec(("node_modules/.bin/tsx", "bench.js"))

    result = gate(spec, base, cand)

    assert result.status == "not_available"
    assert "is a shell" in result.detail


def with_node(node_dir: Path) -> str:
    return f"{node_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"


def npm_tool(node: Path, name: str) -> Path:
    tool = node.parent / name
    if not tool.exists():
        pytest.skip(f"no {name} next to {node}")
    return tool


@pytest.mark.parametrize(
    "form",
    [
        ("npx", "tsx"),
        ("npx", "--no-install", "tsx"),
        ("npx", "--no", "tsx"),
        ("npx", "--", "tsx"),
        ("npm", "exec", "--", "tsx"),
        ("npm", "exec", "--no", "--", "tsx"),
    ],
)
def test_npx_and_npm_exec_are_followed_to_the_node_they_run(
    tmp_path: Path, node: Path, form: tuple[str, ...]
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        write_shim(root, "tsx", "/usr/bin/env node")
    launcher = str(npm_tool(node, form[0]))
    spec = make_spec((launcher, *form[1:], "bench.js"))
    path = with_node(node.parent)

    env = probe("baseline", spec, base, path=path)
    result = gate(spec, base, cand, path=path)

    assert env.runtime_executable == str(node)
    assert (env.runtime_version or "").startswith(f"{node_version(node)};")
    assert result.status == "passed", result.detail


def test_launcher_runtime_differing_between_arms_fails(
    tmp_path: Path, node: Path, other_node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        write_shim(root, "tsx", "/usr/bin/env node")
    spec = make_spec((str(npm_tool(node, "npx")), "tsx", "bench.js"))

    result = gate(
        spec,
        base,
        cand,
        path=with_node(node.parent),
        cand_path=with_node(other_node.parent),
    )

    assert result.status == "failed"
    assert "runtime_version" in {d.item for d in result.differences}


def test_launcher_uses_the_local_node_in_node_modules_bin(
    tmp_path: Path, node: Path, other_node: Path
) -> None:
    """A local ``node`` ahead of the ``PATH``'s is what npx runs."""
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        write_shim(root, "tsx", "/usr/bin/env node")
    (cand / "node_modules" / ".bin" / "node").symlink_to(other_node)
    spec = make_spec((str(npm_tool(node, "npx")), "tsx", "bench.js"))

    result = gate(spec, base, cand, path=with_node(node.parent))

    assert result.status == "failed"
    items = {d.item: d for d in result.differences}
    assert items["runtime_executable"].candidate == str(other_node)


def test_command_is_found_in_a_parent_node_modules_bin_first(
    tmp_path: Path, node: Path
) -> None:
    """Ancestors' ``.bin`` come before the ``PATH`` for the command."""
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        write_shim(root, "tsx", "/usr/bin/env node")
        (root / "sub").mkdir()
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "tsx").write_text("#!/usr/bin/env python3\nprint(1)\n")
    (decoy / "tsx").chmod(0o755)
    spec = make_spec(
        (str(npm_tool(node, "npx")), "tsx", "bench.js"), cwd="sub"
    )
    path = f"{decoy}{os.pathsep}{with_node(node.parent)}"

    env = probe("baseline", spec, base, path=path)

    assert env.runtime_executable == str(node), env.missing
    assert gate(spec, base, cand, path=path).status == "passed"


def test_npx_that_would_fetch_is_refused_unless_told_not_to(
    tmp_path: Path, node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    npx = str(npm_tool(node, "npx"))
    path = with_node(node.parent)

    fetch = gate(make_spec((npx, "left-pad")), base, cand, path=path)
    no_fetch = gate(
        make_spec((npx, "--no-install", "left-pad")), base, cand, path=path
    )

    assert "may download code at run time" in fetch.detail
    assert "may download code" not in no_fetch.detail
    assert "is not an executable file" in no_fetch.detail


def test_script_shell_in_npmrc_cannot_swap_the_node_unseen(
    tmp_path: Path, node: Path, other_node: Path
) -> None:
    """A ``script-shell`` that puts another node first is seen.

    The launcher itself is asked for its node, so a setting this gate
    has no list for still shows up.
    """
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        write_shim(root, "tsx", "/usr/bin/env node")
    shell = tmp_path / "swap-shell"
    shell.write_text(
        f'#!/bin/sh\nPATH="{other_node.parent}:$PATH" exec /bin/sh "$@"\n'
    )
    shell.chmod(0o755)
    (cand / ".npmrc").write_text(f"script-shell={shell}\n")
    spec = make_spec((str(npm_tool(node, "npx")), "tsx", "bench.js"))
    result = gate(spec, base, cand, path=with_node(node.parent))
    assert result.status == "not_available"


@pytest.mark.parametrize(
    "form",
    [
        ("pnpm", "exec", "tsx"),
        ("pnpx", "tsx"),
        ("pn", "exec", "tsx"),
        ("pnx", "tsx"),
        ("yarn", "exec", "tsx"),
        ("yarn", "tsx"),
        ("yarnpkg", "tsx"),
        ("corepack", "pnpm", "exec", "tsx"),
    ],
)
def test_launchers_that_pick_their_own_node_are_refused(
    tmp_path: Path, node: Path, form: tuple[str, ...]
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    launchers = tmp_path / "launchers"
    launchers.mkdir()
    for name in {form[0]}:
        stub = launchers / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    spec = make_spec(form)
    path = f"{launchers}{os.pathsep}{with_node(node.parent)}"

    result = gate(spec, base, cand, path=path)

    assert result.status == "not_available"
    assert (
        "launcher that picks its own runtime" in result.detail
        or "may download code at run time" in result.detail
    )


@pytest.mark.parametrize(
    ("form", "reason"),
    [
        (("npx", "-y", "tsx"), "option '-y' is not understood"),
        (("npm", "run", "bench"), "only 'npm exec' is"),
        (("npm", "exec"), "names no command"),
        (("npm", "exec", "--foo", "tsx"), "option '--foo' is not"),
    ],
)
def test_npm_forms_that_are_not_followed_are_refused(
    tmp_path: Path, node: Path, form: tuple[str, ...], reason: str
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        write_shim(root, "tsx", "/usr/bin/env node")
    spec = make_spec((str(npm_tool(node, form[0])), *form[1:]))

    result = gate(spec, base, cand, path=with_node(node.parent))

    assert result.status == "not_available"
    assert reason in result.detail


def pick_shim(directory: Path) -> Path:
    """A version-manager style ``node`` that picks a binary at run time.

    The file named by ``$NODE_PICK``, or ``.pick`` in the working
    directory, holds the real node's path.
    """
    shim = directory / "node"
    directory.mkdir(parents=True, exist_ok=True)
    shim.write_text('#!/bin/sh\nexec "$(cat "${NODE_PICK:-.pick}")" "$@"\n')
    shim.chmod(0o755)
    return shim


def test_shim_is_asked_in_the_benchmarks_directory(
    tmp_path: Path, node: Path, other_node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    shim = pick_shim(tmp_path / "shims")
    spec = make_spec((str(shim), "bench.js"))
    (base / ".pick").write_text(str(node))
    (cand / ".pick").write_text(str(node))
    assert gate(spec, base, cand).status == "passed"
    env = probe("baseline", spec, base)
    assert env.runtime_executable == str(node)

    (cand / ".pick").write_text(str(other_node))
    result = gate(spec, base, cand)

    assert result.status == "failed"
    items = {d.item: d for d in result.differences}
    assert items["runtime_executable"].candidate == str(other_node)


def test_shim_is_asked_in_the_benchmarks_environment(
    tmp_path: Path, node: Path, other_node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    shim = pick_shim(tmp_path / "shims")
    picks = tmp_path / "picks"
    picks.mkdir()
    (picks / "node").write_text(str(node))
    (picks / "other").write_text(str(other_node))
    spec = make_spec((str(shim), "bench.js"))
    same = {"NODE_PICK": str(picks / "node")}
    assert gate(spec, base, cand, base_extra=same, cand_extra=same).status == (
        "passed"
    )

    result = gate(
        spec,
        base,
        cand,
        base_extra=same,
        cand_extra={"NODE_PICK": str(picks / "other")},
    )

    assert result.status == "failed"


def test_shim_choosing_binaries_with_one_version_is_told_apart_by_hash(
    tmp_path: Path, node: Path
) -> None:
    """Same version, same shim, different real binaries behind it."""
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    shim = pick_shim(tmp_path / "shims")
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
        shutil.copy2(node, tmp_path / name / "node")
    perturb(tmp_path / "two" / "node")
    (base / ".pick").write_text(str(tmp_path / "one" / "node"))
    (cand / ".pick").write_text(str(tmp_path / "two" / "node"))

    result = gate(make_spec((str(shim), "bench.js")), base, cand)

    assert result.status == "failed"
    items = {d.item: d for d in result.differences}
    base_version = items["runtime_version"].baseline or ""
    cand_version = items["runtime_version"].candidate or ""
    assert base_version.split(";")[0] == cand_version.split(";")[0]
    assert base_version != cand_version


def test_a_shell_stays_unknown(tmp_path: Path) -> None:
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no sh on the PATH")
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(("sh", "-c", "node bench.js"))

    env = probe("baseline", spec, base)
    result = gate(spec, base, cand)

    assert env.runtime_kind is None
    assert result.status == "not_available"
    assert "is a shell" in result.detail


def test_node_that_does_not_answer_in_time_is_unknown(
    tmp_path: Path,
) -> None:
    """A real executable named ``node`` that sleeps past the timeout."""
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        slow = root / "bin" / "node"
        slow.write_text("#!/bin/sh\nsleep 30\n")
        slow.chmod(0o755)

    result = gate(make_spec(NODE_COMMAND), base, cand, timeout=0.5)

    assert result.status == "not_available"
    assert "could not report its node version" in result.detail


def test_node_that_exits_non_zero_is_unknown(tmp_path: Path) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        bad = root / "bin" / "node"
        bad.write_text("#!/bin/sh\necho broken >&2\nexit 3\n")
        bad.chmod(0o755)

    result = gate(make_spec(NODE_COMMAND), base, cand)

    assert result.status == "not_available"
    assert "could not report its node version" in result.detail
    assert "exited 3: broken" in result.detail


JAVA_COMMAND = ("bin/java", "-version")


def working_java() -> tuple[Path, str]:
    """The real java executable and the version its banner reports."""
    report = java_report()
    if report is None:
        pytest.skip("no java on the PATH")
    path, status, output = report
    if status != 0:
        pytest.skip(f"java is not usable here: {output.strip()[:100]!r}")
    found = re.search(r'version "([^"]+)"', output)
    assert found is not None, output
    return path.resolve(), found.group(1)


def test_java_passes_with_the_same_jdk(tmp_path: Path) -> None:
    java, banner_version = working_java()
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    place(base, java, "java", copy=False)
    place(cand, java, "java", copy=False)
    spec = make_spec(JAVA_COMMAND)

    env = probe("baseline", spec, base)
    result = gate(spec, base, cand)

    assert result.status == "passed", result.detail
    assert env.runtime_executable == str(java)
    assert env.runtime_version is not None
    assert banner_version in env.runtime_version
    assert "; sha256 " in env.runtime_version


def test_java_binary_differing_only_in_bytes_fails(tmp_path: Path) -> None:
    """Each tree holds a whole copy of the JDK home.

    A lone copied ``java`` cannot find its ``libjli``, so the home (the
    parent of ``bin/``) is copied; the candidate's launcher is then
    perturbed. Both report the same version.
    """
    java, banner_version = working_java()
    home = java.parent.parent
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        shutil.copytree(home, root / "jdk", symlinks=True)
    spec = make_spec(("jdk/bin/java", "-version"))
    assert gate(spec, base, cand).status == "passed"
    perturb(cand / "jdk" / "bin" / "java")

    result = gate(spec, base, cand)

    assert result.status == "failed", result.detail
    items = {d.item: d for d in result.differences}
    assert set(items) == {"runtime_version"}
    assert banner_version in (items["runtime_version"].baseline or "")
    assert banner_version in (items["runtime_version"].candidate or "")


def test_java_stub_without_a_jdk_is_unknown(tmp_path: Path) -> None:
    report = java_report()
    if report is None:
        pytest.skip("no java on the PATH")
    _, status, _ = report
    if status == 0:
        pytest.skip("java works here, so there is no stub to refuse")
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(("java", "-version"))

    result = gate(spec, base, cand)

    assert result.status == "not_available"
    assert "could not report its java version" in result.detail
    assert f"exited {status}" in result.detail


def test_node_that_reads_stdin_does_not_hang(tmp_path: Path) -> None:
    """The probe gets closed stdin though ours is an idle open pipe."""
    base = tree(tmp_path, "a")
    reader = base / "bin" / "node"
    reader.write_text("#!/bin/sh\ncat\n")
    reader.chmod(0o755)
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from test_lock_runtimes import make_spec, probe, NODE_COMMAND\n"
        "env = probe('baseline', make_spec(NODE_COMMAND), "
        "Path(sys.argv[1]), timeout=20.0)\n"
        "print(env.missing.get('runtime_kind', ''))\n"
    )
    read_end, write_end = os.pipe()
    started = time.monotonic()
    try:
        done = subprocess.run(
            [sys.executable, "-c", code, str(base)],
            stdin=read_end,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).parent),
            },
        )
    finally:
        os.close(read_end)
        os.close(write_end)

    assert time.monotonic() - started < 15.0, done.stderr
    assert "did not report an executable and a version" in done.stdout


def test_node_that_prints_too_much_is_unknown(tmp_path: Path) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        loud = root / "bin" / "node"
        loud.write_text("#!/bin/sh\nhead -c 100000 /dev/zero | tr '\\0' a\n")
        loud.chmod(0o755)

    result = gate(make_spec(NODE_COMMAND), base, cand)

    assert result.status == "not_available"
    assert "more than a version report" in result.detail


def test_java_picked_up_lines_are_not_part_of_the_version(
    tmp_path: Path,
) -> None:
    java, banner_version = working_java()
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    place(base, java, "java", copy=False)
    place(cand, java, "java", copy=False)
    spec = make_spec(JAVA_COMMAND)
    options = {"JAVA_TOOL_OPTIONS": "-Dperfproof.probe=1"}

    env = probe("baseline", spec, base, extra=options)
    result = gate(spec, base, cand, base_extra=options, cand_extra={})

    assert banner_version in (env.runtime_version or "")
    assert "Picked up" not in (env.runtime_version or "")
    assert result.status == "passed", result.detail


def test_bin_script_with_its_own_node_shebang_is_refused_under_npx(
    tmp_path: Path, node: Path, other_node: Path
) -> None:
    """A ``#!/abs/node`` script does not run the launcher's node."""
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    write_shim(base, "who", "/usr/bin/env node")
    write_shim(cand, "who", str(other_node))
    spec = make_spec(
        (str(npm_tool(node, "npx")), "--no-install", "who", "bench.js")
    )

    result = gate(spec, base, cand, path=with_node(node.parent))

    assert result.status == "not_available"
    assert "the command runs" in result.detail
    assert str(other_node) in result.detail


def recording_launcher(directory: Path, name: str, node: Path) -> Path:
    """A launcher named ``name`` that records its argv, then runs node.

    It drops everything up to the ``node`` word and runs the real node
    with the rest, which is what npx and npm exec do with the probe.
    """
    directory.mkdir(exist_ok=True)
    stub = directory / name
    stub.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{directory}/{name}.log"\n'
        'while [ "$1" != node ]; do shift; done; shift\n'
        f'exec "{node}" "$@"\n'
    )
    stub.chmod(0o755)
    return stub


@pytest.mark.parametrize(
    ("name", "form", "expect"),
    [
        ("npx", ("tsx",), "--no-install node -e "),
        ("npx", ("--no", "tsx"), "--no-install node -e "),
        ("npm", ("exec", "--", "tsx"), "exec --no -- node -e "),
    ],
)
def test_the_probe_through_a_launcher_forbids_installs(
    tmp_path: Path,
    node: Path,
    name: str,
    form: tuple[str, ...],
    expect: str,
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        write_shim(root, "tsx", "/usr/bin/env node")
    launcher = recording_launcher(tmp_path / "launch", name, node)
    spec = make_spec((str(launcher), *form, "bench.js"))

    result = gate(spec, base, cand, path=with_node(node.parent))

    assert result.status == "passed", result.detail
    lines = (tmp_path / "launch" / f"{name}.log").read_text().splitlines()
    assert lines
    assert all(line.startswith(expect) for line in lines), lines


@pytest.mark.parametrize(
    "body",
    ["{real}\\nv1.0.0\\nextra", "relative/node\\nv1.0.0", "{real}"],
)
def test_malformed_node_reports_are_refused(
    tmp_path: Path, node: Path, body: str
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    printed = body.format(real=node)
    for root in (base, cand):
        fake = root / "bin" / "node"
        fake.write_text(f"#!/bin/sh\nprintf '{printed}\\n'\n")
        fake.chmod(0o755)

    result = gate(make_spec(NODE_COMMAND), base, cand)

    assert result.status == "not_available"
    assert "did not report an executable and a version" in result.detail


def test_node_report_with_stderr_overflow_is_refused(
    tmp_path: Path, node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        fake = root / "bin" / "node"
        fake.write_text(
            f"#!/bin/sh\nprintf '{node}\\nv1.0.0\\n'\n"
            "head -c 100000 /dev/zero | tr '\\0' a >&2\n"
        )
        fake.chmod(0o755)

    result = gate(make_spec(NODE_COMMAND), base, cand)

    assert result.status == "not_available"
    assert "more than a version report" in result.detail


def test_a_node_preload_cannot_forge_the_report(
    tmp_path: Path, node: Path
) -> None:
    """``NODE_OPTIONS --require`` is not seen by the identity probe."""
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    place(base, node, "node", copy=False)
    place(cand, node, "node", copy=False)
    forged = tmp_path / "forged.js"
    forged.write_text(
        "console.log('/forged/node');console.log('v0.0.0');process.exit(0);\n"
    )
    spec = make_spec(NODE_COMMAND)
    options = {"NODE_OPTIONS": f"--require {forged}"}

    env = probe("candidate", spec, cand, extra=options)
    result = gate(spec, base, cand, cand_extra=options)

    assert env.runtime_executable == str(node)
    assert (env.runtime_version or "").startswith(f"{node_version(node)};")
    assert result.status == "passed", result.detail


def test_a_node_preload_that_prints_does_not_spoil_the_report(
    tmp_path: Path, node: Path
) -> None:
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    place(base, node, "node", copy=False)
    place(cand, node, "node", copy=False)
    chatty = tmp_path / "chatty.js"
    chatty.write_text("console.log('preload says hello');\n")
    spec = make_spec(NODE_COMMAND)
    options = {"NODE_OPTIONS": f"--require {chatty}"}

    result = gate(spec, base, cand, base_extra=options, cand_extra=options)

    assert result.status == "passed", result.detail


# Launchers that would fetch code are an unsafe run, not an unknown one.
# Nothing here runs a launcher: fetching_launch reads argv and files.


@pytest.fixture
def launchers(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A working directory and a PATH of inert programs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in (
        "bun",
        "bunx",
        "corepack",
        "deno",
        "env",
        "npm",
        "npx",
        "pipx",
        "pnpm",
        "pnpx",
        "uv",
        "uvx",
        "yarn",
        "tsc",
    ):
        exe = bin_dir / name
        exe.write_text("#!/bin/sh\nexit 1\n")
        exe.chmod(0o755)
    cwd = tmp_path / "work"
    cwd.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    return cwd, {"PATH": str(bin_dir), "HOME": str(home)}


@pytest.mark.parametrize(
    "argv",
    [
        ["npx", "some-tool"],
        ["npx", "@angular/cli", "build"],
        ["npx", "user/repo"],
        ["npx", "https://example.com/x.tgz"],
        ["npx", "--yes", "@scope/pkg"],
        ["npm", "exec", "--", "@scope/pkg"],
        ["npm", "--silent", "install"],
        ["npm", "-g", "install", "x"],
        ["npm", "--prefix", "x", "install"],
        ["pnpm", "--dir", "x", "install"],
        ["pnpm", "--silent", "dlx", "x"],
        ["yarn", "--silent", "add", "x"],
        ["uv", "--quiet", "tool", "run", "x"],
        ["npx", "-y", "some-tool", "--flag"],
        ["npx", "-p", "typescript", "tsc"],
        ["npm", "exec", "some-tool"],
        ["npm", "x", "some-tool"],
        ["npm", "install"],
        ["npm", "ci"],
        ["pnpm", "dlx", "some-tool"],
        ["pnpm", "add", "left-pad"],
        ["pnpx", "some-tool"],
        ["yarn", "dlx", "some-tool"],
        ["yarn"],
        ["yarn", "install"],
        ["bunx", "some-tool"],
        ["bun", "x", "some-tool"],
        ["bun", "install"],
        ["deno", "run", "https://example.com/x.ts"],
        ["deno", "run", "npm:some-tool"],
        ["corepack", "pnpm", "--version"],
        ["uvx", "ruff"],
        ["uv", "tool", "run", "ruff"],
        ["pipx", "run", "ruff"],
        ["env", "FOO=1", "npx", "some-tool"],
        ["env", "-u", "BAR", "pnpx", "some-tool"],
    ],
)
def test_forms_that_fetch_code_are_named(
    launchers: tuple[Path, dict[str, str]], argv: list[str]
) -> None:
    cwd, env = launchers
    assert fetching_launch(argv, env, cwd) is not None


@pytest.mark.parametrize(
    "argv",
    [
        ["npx", "--no-install", "some-tool"],
        ["npx", "--no", "some-tool"],
        ["npx", "tsc"],
        ["npm", "exec", "--no", "--", "some-tool"],
        ["npm", "run", "bench"],
        ["npm", "test"],
        ["pnpm", "exec", "tsc"],
        ["pnpm", "run", "bench"],
        ["yarn", "run", "bench"],
        ["bun", "run", "bench.ts"],
        ["deno", "run", "bench.ts"],
        ["deno", "task", "bench"],
        ["uv", "run", "bench.py"],
        ["pipx", "list"],
        ["env", "FOO=1", "tsc"],
        ["cargo", "run"],
        ["go", "run", "."],
        ["make", "run"],
        ["python", "bench.py"],
    ],
)
def test_forms_that_run_what_is_installed_are_not_named(
    launchers: tuple[Path, dict[str, str]], argv: list[str]
) -> None:
    cwd, env = launchers
    bins = cwd / "node_modules" / ".bin"
    bins.mkdir(parents=True)
    for name in ("tsc",):
        (bins / name).write_text("#!/usr/bin/env node\n")
        (bins / name).chmod(0o755)
    assert fetching_launch(argv, env, cwd) is None


def test_a_command_installed_in_node_modules_is_local_for_npx(
    launchers: tuple[Path, dict[str, str]],
) -> None:
    cwd, env = launchers
    assert fetching_launch(["npx", "mytool"], env, cwd) is not None
    bins = cwd.parent / "node_modules" / ".bin"
    bins.mkdir(parents=True)
    (bins / "mytool").write_text("#!/usr/bin/env node\n")
    (bins / "mytool").chmod(0o755)
    assert fetching_launch(["npx", "mytool"], env, cwd) is None
    assert fetching_launch(["npm", "exec", "mytool"], env, cwd) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["timeout", "10", "npx", "some-tool"],
        ["timeout", "-s", "KILL", "--foreground", "10", "npx", "some-tool"],
        ["nice", "-n", "5", "pnpx", "some-tool"],
        ["nice", "-5", "pnpx", "some-tool"],
        ["nohup", "pnpx", "some-tool"],
        ["command", "npx", "some-tool"],
        ["exec", "npx", "some-tool"],
        ["time", "-p", "npx", "some-tool"],
        ["stdbuf", "-oL", "npx", "some-tool"],
        ["stdbuf", "-o", "L", "npx", "some-tool"],
        ["setsid", "-w", "npx", "some-tool"],
        ["env", "-S", "npx some-tool"],
        ["nice", "-n", "5", "timeout", "10", "env", "A=1", "npx", "some-tool"],
    ],
)
def test_wrappers_do_not_hide_a_launcher_that_fetches(
    launchers: tuple[Path, dict[str, str]], argv: list[str]
) -> None:
    cwd, env = launchers
    bin_dir = Path(env["PATH"])
    for name in (
        "timeout",
        "nice",
        "nohup",
        "command",
        "exec",
        "time",
        "stdbuf",
        "setsid",
    ):
        exe = bin_dir / name
        exe.write_text("#!/bin/sh\nexit 1\n")
        exe.chmod(0o755)
    assert fetching_launch(argv, env, cwd) is not None


def test_unwrap_launch_peels_wrappers_and_leaves_the_rest() -> None:
    assert unwrap_launch(["timeout", "10", "node", "x.js"]) == ["node", "x.js"]
    assert unwrap_launch(["nice", "-n", "5", "node", "x.js"]) == [
        "node",
        "x.js",
    ]
    assert unwrap_launch(["nohup", "node", "x.js"]) == ["node", "x.js"]
    assert unwrap_launch(["stdbuf", "-oL", "-eL", "node"]) == ["node"]
    assert unwrap_launch(["time", "-p", "nice", "node"]) == ["node"]
    assert unwrap_launch(["node", "x.js"]) == ["node", "x.js"]
    assert unwrap_launch(["timeout"]) == ["timeout"]
    assert unwrap_launch([]) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["yarn", "--version"],
        ["yarn", "-v"],
        ["yarn", "--help"],
        ["yarn", "-h"],
        ["npx", "--offline", "some-tool"],
        ["npm", "exec", "--offline", "some-tool"],
        ["corepack", "enable"],
        ["corepack", "disable"],
        ["corepack", "--version"],
    ],
)
def test_forms_that_cannot_fetch_are_not_named(
    launchers: tuple[Path, dict[str, str]], argv: list[str]
) -> None:
    cwd, env = launchers
    assert fetching_launch(argv, env, cwd) is None


def test_corepack_shim_for_a_cached_package_manager_does_not_fetch(
    launchers: tuple[Path, dict[str, str]],
) -> None:
    cwd, env = launchers
    assert fetching_launch(["corepack", "pnpm", "run", "x"], env, cwd)
    cached = Path(env["HOME"]) / ".cache/node/corepack/v1/pnpm/9.0.0"
    cached.mkdir(parents=True)
    (cached / "bin").write_text("x")
    assert fetching_launch(["corepack", "pnpm", "run", "x"], env, cwd) is None
    assert fetching_launch(["corepack", "yarn", "run", "x"], env, cwd)
    assert fetching_launch(["corepack", "prepare", "pnpm@9"], env, cwd)
    offline = {**env, "COREPACK_ENABLE_NETWORK": "0"}
    assert (
        fetching_launch(["corepack", "yarn", "run", "x"], offline, cwd) is None
    )
    home = {**env, "COREPACK_HOME": str(Path(env["HOME"]) / "elsewhere")}
    assert fetching_launch(["corepack", "pnpm", "run", "x"], home, cwd)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a directory without permission"
)
def test_an_unreadable_corepack_cache_is_may_download_not_a_crash(
    launchers: tuple[Path, dict[str, str]],
) -> None:
    cwd, env = launchers
    cache = Path(env["HOME"]) / ".cache/node/corepack/v1/pnpm"
    (cache / "9.0.0").mkdir(parents=True)
    (cache / "9.0.0" / "bin").write_text("x")
    cache.chmod(0)
    try:
        reason = fetching_launch(["corepack", "pnpm", "run", "x"], env, cwd)
    finally:
        cache.chmod(0o755)
    assert reason is not None
    assert "may download" in reason


def test_a_corepack_shim_is_looked_up_by_the_tool_it_stands_for(
    launchers: tuple[Path, dict[str, str]],
) -> None:
    cwd, env = launchers
    bin_dir = Path(env["PATH"])
    (bin_dir / "pnpm").unlink()
    (bin_dir / "pnpm").symlink_to(bin_dir / "corepack")
    root = Path(env["HOME"]) / ".cache/node/corepack/v1"
    # A cache entry named for the subcommand is not the package manager.
    (root / "run").mkdir(parents=True)
    (root / "run" / "x").write_text("x")
    assert fetching_launch(["pnpm", "run", "bench"], env, cwd)
    (root / "pnpm" / "9.0.0").mkdir(parents=True)
    (root / "pnpm" / "9.0.0" / "bin").write_text("x")
    assert fetching_launch(["pnpm", "run", "bench"], env, cwd) is None
    assert fetching_launch(["pnpm", "test"], env, cwd) is None
