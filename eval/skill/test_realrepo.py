"""Real-repository seeding, against a real local git "remote"."""

import importlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import realrepo
import runner
import sandbox
import taskset

REAL = taskset.load(runner.REPO_ROOT / "eval" / "skill" / "tasks-real.json")
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_ENV,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> tuple[str, str, str]:
    """A repo of three commits served from ``<host>/o/r.git``.

    Returns the host URL and the shas of the second and third commit.
    """
    host = tmp_path / "host"
    src = host / "o" / "r.git"
    src.mkdir(parents=True)
    _git(src, "init", "-q", "-b", "main")
    shas: list[str] = []
    for n in range(3):
        (src / "f.txt").write_text(f"v{n}\n")
        _git(src, "add", "-A")
        _git(src, "commit", "-q", "-m", f"c{n}")
        shas.append(_git(src, "rev-parse", "HEAD"))
    return f"file://{host}", shas[1], shas[2]


def _spec(task: str, reference: str) -> realrepo.RepoSpec:
    return realrepo.RepoSpec(
        repository="o/r",
        task_commit=task,
        reference_commit=reference,
        reference_number="7",
        reference_title="t",
        setup=[],
        env={},
        oracle=None,
    )


def test_find_layout_has_one_commit_and_no_remote(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(second, third)
    dest = tmp_path / "run"
    tips = realrepo.seed_repo(
        spec,
        taskset.RealRepo(manifest="x"),
        dest,
        tmp_path / "cache",
        host,
    )
    assert tips == {"main": second}
    assert _git(dest, "rev-list", "--count", "HEAD") == "1"
    assert (dest / "f.txt").read_text() == "v1\n"
    assert _git(dest, "remote") == ""
    assert _git(dest, "status", "--porcelain") == ""
    # The change after the task commit is not in the clone.
    probe = subprocess.run(
        ["git", "cat-file", "-e", third],
        cwd=dest,
        capture_output=True,
        check=False,
    )
    assert probe.returncode != 0


def test_prove_layout_cuts_the_branch_at_the_reference_change(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(second, third)
    dest = tmp_path / "run"
    tips = realrepo.seed_repo(
        spec,
        taskset.RealRepo(manifest="x", branch="pr-7"),
        dest,
        tmp_path / "cache",
        host,
    )
    assert tips == {"main": second, "pr-7": third}
    assert _git(dest, "rev-parse", "pr-7^") == second
    assert _git(dest, "merge-base", "main", "pr-7") == second
    assert _git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_a_cache_hit_needs_no_remote(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(second, third)
    real = taskset.RealRepo(manifest="x")
    cache = tmp_path / "cache"
    realrepo.seed_repo(spec, real, tmp_path / "a", cache, host)
    tips = realrepo.seed_repo(
        spec, real, tmp_path / "b", cache, "file:///does/not/exist"
    )
    assert tips == {"main": second}


def test_a_parent_that_is_not_the_task_commit_is_refused(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(third, third)
    with pytest.raises(realrepo.SetupError, match="not the task commit"):
        realrepo.seed_repo(
            spec,
            taskset.RealRepo(manifest="x", branch="pr-7"),
            tmp_path / "run",
            tmp_path / "cache",
            host,
        )
    assert second


def test_setup_hides_what_the_build_made_from_the_status(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(second, third).model_copy(
        update={"setup": ["python3 -c \"open('built.bin','w').write('x')\""]}
    )
    dest = tmp_path / "run"
    realrepo.seed_repo(
        spec, taskset.RealRepo(manifest="x"), dest, tmp_path / "c", host
    )
    realrepo.run_setup(spec, dest, [])
    assert (dest / "built.bin").exists()
    assert _git(dest, "status", "--porcelain") == ""


def test_a_failing_setup_step_names_itself(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(second, third).model_copy(
        update={"setup": ["python3 -c 'raise SystemExit(3)'"]}
    )
    dest = tmp_path / "run"
    realrepo.seed_repo(
        spec, taskset.RealRepo(manifest="x"), dest, tmp_path / "c", host
    )
    with pytest.raises(realrepo.SetupError, match="exited 3"):
        realrepo.run_setup(spec, dest, [])


@pytest.mark.skipif(
    not sandbox.confinement_available(), reason="needs macOS sandbox-exec"
)
def test_the_oracle_reports_its_exit_status(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    base = _spec(second, third)
    dest = tmp_path / "run"
    realrepo.seed_repo(
        base, taskset.RealRepo(manifest="x"), dest, tmp_path / "c", host
    )
    ok = base.model_copy(update={"oracle": "python3 -c 'print(1)'"})
    bad = base.model_copy(
        update={"oracle": "python3 -c 'raise SystemExit(1)'"}
    )
    assert realrepo.run_oracle(ok, dest, [])[0] is True
    assert realrepo.run_oracle(bad, dest, [])[0] is False
    assert realrepo.run_oracle(base, dest, []) == (
        False,
        "the manifest has no oracle command",
    )


def test_a_missing_toolchain_is_named() -> None:
    real = taskset.RealRepo(manifest="x", requires=["no-such-tool-xyz"])
    assert realrepo.missing_requirements(real) == ["no-such-tool-xyz"]


def test_shims_mount_the_enclosing_work_tree(tmp_path: Path) -> None:
    realrepo.write_shims(tmp_path, "go")
    body = (tmp_path / "go").read_text()
    assert os.access(tmp_path / "go", os.X_OK)
    assert "rev-parse --show-toplevel" in body
    assert "golang:1.25" in body
    realrepo.write_shims(tmp_path, "ruby")
    assert {p.name for p in tmp_path.iterdir()} == {
        "go",
        "ruby",
        "bundle",
        "gem",
        "rake",
    }


@pytest.mark.parametrize("task", REAL.tasks, ids=lambda t: t.id)
def test_every_real_task_has_a_development_manifest(
    task: taskset.Task,
) -> None:
    assert task.repo is not None
    found = sorted(
        (runner.REPO_ROOT / "eval" / "tasks").glob(
            f"*/{task.repo.manifest}.json"
        )
    )
    assert len(found) == 1
    manifest = json.loads(found[0].read_text())
    assert manifest["split"] == "development", "held-out stays unseen"
    assert manifest["status"] == "runs"
    family = {"prove": "known-change", "find": None}[task.kind]
    if family is not None:
        assert manifest["family"] == family
        assert task.repo.branch is not None
    else:
        assert manifest["family"] in {"hidden-fix", "nothing-to-keep"}
        assert task.repo.branch is None
    spec = realrepo.load_spec(task.repo.manifest)
    assert spec.setup
    assert spec.oracle


def test_batch_one_covers_the_languages_and_families() -> None:
    by_family: dict[str, int] = {}
    for task in REAL.tasks:
        assert task.repo is not None
        manifest = realrepo.load_spec(task.repo.manifest)
        assert manifest.repository
        found = next(
            (runner.REPO_ROOT / "eval" / "tasks").glob(
                f"*/{task.repo.manifest}.json"
            )
        )
        family = json.loads(found.read_text())["family"]
        by_family[family] = by_family.get(family, 0) + 1
    assert by_family == {
        "hidden-fix": 3,
        "known-change": 3,
        "nothing-to-keep": 2,
    }
    assert len(REAL.tasks) == 8


def test_a_held_out_manifest_cannot_be_loaded() -> None:
    with pytest.raises(ValueError, match="development"):
        realrepo.load_spec("psf-black-pr3467")
    assert realrepo.load_spec("sharkdp-fd-pr853").setup


_SKILL = runner.REPO_ROOT / "eval" / "skill"
_CANDIDATE_TASKS = [
    pytest.param(task, id=task.id)
    for name in ("tasks-go.json", "tasks-native.json")
    for task in taskset.load(_SKILL / name).tasks
    if task.repo is not None
]


@pytest.mark.parametrize("task", _CANDIDATE_TASKS)
def test_a_built_candidate_of_a_drawn_class_loads(task: taskset.Task) -> None:
    assert task.repo is not None
    assert realrepo.load_spec(task.repo.manifest).setup


def _manifest(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "split": "development-candidate",
        "status": "runs",
        "target_class": "web",
        "workload": {"verified": True},
        "oracle": {"verified": True},
    }
    return {**base, **fields}


def test_what_makes_a_candidate_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        realrepo,
        "draw_state",
        lambda: ({"java": "its held-out draw stopped"}, frozenset({"h"})),
    )
    assert realrepo.unusable_reason("t", _manifest()) is None
    assert (
        realrepo.unusable_reason("t", _manifest(split="development")) is None
    )
    refused = {
        "held out": _manifest(split="held-out"),
        "not built": _manifest(status="builds"),
        "workload unverified": _manifest(workload={"verified": False}),
        "oracle missing": _manifest(oracle=None),
        "draw stopped": _manifest(target_class="java"),
    }
    for why, manifest in refused.items():
        assert realrepo.unusable_reason("t", manifest), why
    assert "not safe" in str(
        realrepo.unusable_reason("t", _manifest(target_class="java"))
    )
    # The draw's own held-out picks are refused whatever the field says.
    assert realrepo.unusable_reason("h", _manifest())


@pytest.mark.parametrize("klass", ["serving", "gpu"])
def test_a_candidate_of_an_unbuilt_class_is_refused(klass: str) -> None:
    """The review marks serving and gpu as needing a GPU host; their
    held-out draw holds unbuilt picks, so they count as unfinished."""
    unsafe, _ = realrepo.draw_state()
    assert klass in unsafe
    reason = realrepo.unusable_reason("t", _manifest(target_class=klass))
    assert reason
    assert "not safe" in reason


def test_a_draw_ending_with_unfilled_slots_is_safe() -> None:
    unsafe, _ = realrepo.draw_state()  # puts eval/mine on sys.path
    draw = importlib.import_module("draw")
    mined = importlib.import_module("task")
    sel = draw.load_selection()
    held = draw.draw_splits(mined.load_tasks(), sel)["held-out"]
    short = [
        k
        for k, d in held.items()
        if d.unfilled and d.stopped is None and k not in sel.unbuilt_classes
    ]
    assert short
    for klass in short:
        assert klass not in unsafe, klass


def test_a_drawn_held_out_task_is_never_a_candidate() -> None:
    _, held_out = realrepo.draw_state()
    assert "gin-gonic-gin-pr4466" in held_out
    for name in held_out:
        with pytest.raises(ValueError):  # noqa: PT011
            realrepo.load_spec(name)


def test_every_held_out_manifest_is_refused() -> None:
    held = [
        p
        for p in realrepo.TASKS_DIR.glob("*/*.json")
        if p.parent.name != "workloads"
        and json.loads(p.read_text()).get("split") == "held-out"
    ]
    assert held
    for path in held:
        with pytest.raises(ValueError, match="development"):
            realrepo.load_spec(path.stem)


def _fake_go(directory: Path, body: str = "exit 0") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    launcher = directory / "go"
    launcher.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def _binary(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xcf\xfa\xed\xfe not a script")
    path.chmod(0o755)
    return path


def test_native_go_is_a_binary_never_a_container_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    shims = tmp_path / "shims"
    _fake_go(shims)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(shims))
    assert realrepo.native_go() != shims, "a script is not native Go"
    native = _binary(home / ".local" / "go" / "bin" / "go")
    assert realrepo.native_go() == native.parent
    # A native go on PATH is found first.
    on_path = _binary(tmp_path / "bin" / "go")
    monkeypatch.setenv("PATH", f"{shims}{os.pathsep}{on_path.parent}")
    assert realrepo.native_go() == on_path.parent


def test_native_go_comes_first_on_the_agent_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    native = _binary(home / ".local" / "go" / "bin" / "go")
    shims = tmp_path / "work" / "shims" / "go"
    shim = _fake_go(shims)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    real = taskset.RealRepo(manifest="x", requires=["docker", "go"], shim="go")
    dirs = [*realrepo.toolchain_dirs(real), tmp_path / "bin", shims]
    assert dirs[0] == native.parent
    repo = tmp_path / "work" / "repos" / "t"
    repo.mkdir(parents=True)
    env = realrepo.task_env(_spec("a", "b"), repo, dirs)
    assert shutil.which("go", path=env["PATH"]) == str(native)
    assert shutil.which("go", path=str(shims)) == str(shim)
    other = taskset.RealRepo(manifest="x", requires=["uv"])
    assert realrepo.toolchain_dirs(other) == []


def test_a_native_go_satisfies_the_go_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    real = taskset.RealRepo(manifest="x", requires=["go"])
    assert realrepo.missing_requirements(real) == ["go"]
    _binary(home / ".local" / "go" / "bin" / "go")
    assert realrepo.missing_requirements(real) == []


def test_native_ruby_and_dotnet_are_found_in_the_eval_users_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert realrepo.native_ruby() is None
    assert realrepo.native_dotnet() is None
    ruby = _binary(home / ".local" / "ruby" / "bin" / "ruby")
    assert realrepo.native_ruby() is None, "bundle is missing"
    (ruby.parent / "bundle").write_text("#!/usr/bin/env ruby\n")
    assert realrepo.native_ruby() == ruby.parent
    dotnet = _binary(home / ".local" / "dotnet" / "dotnet")
    assert realrepo.native_dotnet() == dotnet.parent


def test_the_system_ruby_is_not_a_native_ruby(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert realrepo.native_ruby() is None


def test_ruby_and_dotnet_tasks_put_their_toolchains_first_on_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    ruby = _binary(home / ".local" / "ruby" / "bin" / "ruby")
    (ruby.parent / "bundle").write_text("#!/usr/bin/env ruby\n")
    dotnet = _binary(home / ".local" / "dotnet" / "dotnet")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    rails = taskset.RealRepo(manifest="x", requires=["ruby", "bundle"])
    assert realrepo.toolchain_dirs(rails) == [ruby.parent]
    assert realrepo.missing_requirements(rails) == []
    net = taskset.RealRepo(manifest="x", requires=["dotnet"])
    assert realrepo.toolchain_dirs(net) == [dotnet.parent]
    assert realrepo.missing_requirements(net) == []
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "other")
    assert realrepo.missing_requirements(rails) == ["ruby", "bundle"]
    assert realrepo.missing_requirements(net) == ["dotnet"]


def test_the_module_cache_is_filled_in_the_private_home_before_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    repo = work / "repos" / "t"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "go.mod").write_text("module m\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a")
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / "go.mod").write_text("module m\nrequire x v1\n")
    (repo / "go.sum").write_text("x v1 h1:y\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "b")
    _git(repo, "checkout", "-q", "main")
    log = tmp_path / "go.log"
    native = _fake_go(
        tmp_path / "native",
        'echo "$HOME|$GOFLAGS|$(pwd)|$*|$GOTOOLCHAIN|$GOPATH|$GOPROXY"'
        f" >> {log}",
    )
    monkeypatch.setenv("PATH", f"{native.parent}{os.pathsep}/usr/bin:/bin")
    realrepo.prefill_go_modcache(
        _spec("a", "b"), repo, [native.parent], refs=["pr"]
    )
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    home, flags, cwd, args, toolchain, gopath, proxy = lines[0].split("|")
    assert toolchain == "local"
    assert gopath == f"{home}/go", "a private GOPATH, not the owner's"
    # The agent's HOME is work/sandbox/<task>/private/home, and GOPATH
    # defaults to $HOME/go, so the cache lands where the agent looks.
    assert home == str(work / "sandbox" / "t" / "private" / "home")
    assert flags == "-modcacherw -mod=readonly"
    assert proxy == "https://proxy.golang.org"
    assert Path(cwd).resolve() == repo.resolve()
    assert args == "mod download"
    copy = work / "sandbox" / "t" / "modfiles" / "pr"
    assert lines[1].split("|")[3] == f"mod download -modfile {copy}/go.mod"
    assert lines[1].split("|")[4] == "local"
    assert "require x v1" in (copy / "go.mod").read_text()
    assert (copy / "go.sum").is_file()
    assert (repo / "go.mod").read_text() == "module m\n", "tree untouched"


def test_a_failed_module_download_is_a_setup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "work" / "repos" / "t"
    repo.mkdir(parents=True)
    (repo / "go.mod").write_text("module m\n")
    native = _fake_go(tmp_path / "native", "echo no network >&2; exit 1")
    monkeypatch.setenv("PATH", f"{native.parent}{os.pathsep}/usr/bin:/bin")
    with pytest.raises(realrepo.SetupError, match="no network"):
        realrepo.prefill_go_modcache(_spec("a", "b"), repo, [native.parent])


def test_a_repo_without_go_mod_needs_no_download(tmp_path: Path) -> None:
    repo = tmp_path / "work" / "repos" / "t"
    repo.mkdir(parents=True)
    realrepo.prefill_go_modcache(_spec("a", "b"), repo, [])


def test_the_django_task_names_the_area_and_never_the_function_or_fix() -> (
    None
):
    """Round 7: the agent profiled template rendering, so the cost of
    the login check never showed. A user would say which views are slow;
    they would not say which function, or how to fix it."""
    task = next(t for t in REAL.tasks if t.id == "real-django-hidden-fix")
    assert task.expect.hotspot is not None
    prompt = task.prompt.lower()
    assert prompt.startswith("/perfproof light ")
    assert "login" in prompt
    for forbidden in (
        task.expect.hotspot.function.lower(),
        "user_passes_test",
        "decorator",
        "decoration",
        "async",
        "coroutine",
        "iscoroutine",
        "21460",
    ):
        assert forbidden not in prompt, forbidden


def _manifest_dirs(
    tmp_path: Path, split: str, status: str
) -> tuple[Path, Path]:
    tasks = tmp_path / "tasks"
    (tasks / "x").mkdir(parents=True)
    (tasks / "x" / "t.json").write_text(
        json.dumps(
            {
                "split": split,
                "status": status,
                "repository": "o/r",
                "task_commit": "a",
                "reference_change": {"commit": "b", "url": "u", "title": ""},
            }
        ),
        encoding="utf-8",
    )
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps({"t": {"setup": [], "env": {}}}))
    return tasks, checks


@pytest.mark.parametrize(
    ("split", "status", "loads"),
    [
        ("development", "runs", True),
        # a candidate needs verified workload and oracle and a safe
        # class (see test_what_makes_a_candidate_usable)
        ("development-candidate", "runs", False),
        ("development-candidate", "builds", False),
        ("held-out", "runs", False),
        ("held-out", "builds", False),
        ("development-candidate", "candidate", False),
        ("development-candidate", "failed", False),
    ],
)
def test_which_splits_and_statuses_may_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    split: str,
    status: str,
    loads: bool,
) -> None:
    tasks, checks = _manifest_dirs(tmp_path, split, status)
    monkeypatch.setattr(realrepo, "TASKS_DIR", tasks)
    monkeypatch.setattr(realrepo, "CHECKS_FILE", checks)
    if loads:
        assert realrepo.load_spec("t").repository == "o/r"
    else:
        with pytest.raises(ValueError, match="development"):
            realrepo.load_spec("t")


# Task files whose manifests the selection rule's draw lets in. With
# unfilled slots counting as a finished draw, every task file loads:
# each names only drawn development tasks or built development
# candidates of a
# class whose held-out draw is finished. A file naming a task the draw
# refuses leaves this set, and its tasks that load individually go into
# _ALSO_LOADS.
_LOADS = {
    "tasks.json",
    "tasks-real.json",
    "tasks-go.json",
    "tasks-native.json",
    "tasks-ruby.json",
    "tasks-java.json",
    "tasks-node.json",
    "tasks-dotnet.json",
    "tasks-rust.json",
}
_ALSO_LOADS: set[str] = set()


@pytest.mark.parametrize(
    "path",
    sorted((Path(__file__).parent).glob("tasks*.json")),
    ids=lambda p: p.name,
)
def test_task_files_load_exactly_when_the_draw_allows(path: Path) -> None:
    found = taskset.load(path)
    assert found.tasks
    for task in found.tasks:
        if task.repo is None:
            continue
        if path.name in _LOADS or task.repo.manifest in _ALSO_LOADS:
            assert realrepo.load_spec(task.repo.manifest).repository
        else:
            with pytest.raises(ValueError, match="development"):
                realrepo.load_spec(task.repo.manifest)
