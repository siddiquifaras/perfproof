"""The Ruby profiler wrapper: stackprof output to folded stacks.

The fixture ``hot.folded`` is what the hook wrote inside the profiled
Ruby 4.0.7 process of ``tests/fixtures/ruby/capture.sh`` (see
``tests/fixtures/ruby/SOURCE.md``). The integration tests run the real
``ruby`` with the stackprof gem, and skip with their reason where it is
missing (this Mac's system Ruby has no stackprof; they run in the Linux
check in the official image).
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from perfproof.profilers import ruby
from perfproof.profilers.folded import summarise, summarise_file

FIXTURES = Path(__file__).parent.parent / "fixtures" / "ruby"
PROG = FIXTURES / "prog"
CAPTURED = FIXTURES / "hot.folded"


def stackprof_ruby() -> str:
    found = shutil.which("ruby")
    if found is None:
        pytest.skip("no ruby on the PATH")
    done = subprocess.run(
        [found, "-e", "require 'stackprof'"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip("the stackprof gem is not installed for this ruby")
    return found


def test_the_captured_profile_ranks_the_known_hot_function() -> None:
    top = summarise_file(CAPTURED, project_root=Path("/src"), top_n=12)

    named = {h.function: h for h in top}
    # One frame per sampled line of the method's body.
    slow = [h for h in top if h.function == "Work.join_slow"]
    assert slow
    assert {h.file for h in slow} == {"lib/work.rb"}
    assert all(h.line in range(9, 13) for h in slow)
    assert not any(h.is_library for h in slow)
    assert sum(h.self_fraction for h in slow) > 0.05
    assert top[0].self_fraction >= top[-1].self_fraction
    assert named["String#+"].is_library is True


def test_every_captured_stack_is_distinct_and_counted() -> None:
    lines = CAPTURED.read_text().splitlines()
    stacks = [line.rpartition(" ")[0] for line in lines]
    assert len(stacks) == len(set(stacks))
    assert all(int(line.rpartition(" ")[2]) > 0 for line in lines)
    assert summarise(lines, project_root=Path("/src"))


def test_the_hook_path_is_printed_and_exists(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert ruby.main(["hook"]) == 0
    printed = Path(capsys.readouterr().out.strip())
    assert printed == ruby.HOOK
    assert printed.is_file()


def test_fragments_of_every_process_are_concatenated(tmp_path: Path) -> None:
    output = tmp_path / "out.folded"
    script = (
        'printf "a (x.rb:1) 3\\n" > '
        '"$PERFPROOF_RUBY_PROFILE_DIR/ruby.1.folded"; '
        'printf "b (x.rb:2) 4\\n" > '
        '"$PERFPROOF_RUBY_PROFILE_DIR/ruby.2.folded"'
    )
    assert ruby.record(["sh", "-c", script], output) == 0
    assert output.read_text().splitlines() == [
        "a (x.rb:1) 3",
        "b (x.rb:2) 4",
    ]


def test_a_command_that_wrote_nothing_is_an_error(tmp_path: Path) -> None:
    assert ruby.record(["true"], tmp_path / "out.folded") == 5
    assert not (tmp_path / "out.folded").exists()


def test_an_empty_profile_is_an_error(tmp_path: Path) -> None:
    script = ': > "$PERFPROOF_RUBY_PROFILE_DIR/ruby.1.folded"'
    assert ruby.record(["sh", "-c", script], tmp_path / "out.folded") == 6


def test_a_failing_command_keeps_its_status(tmp_path: Path) -> None:
    assert ruby.record(["sh", "-c", "exit 3"], tmp_path / "o.folded") == 3


def test_files_of_an_earlier_run_are_refused(tmp_path: Path) -> None:
    output = tmp_path / "out.folded"
    leftover = Path(f"{output}.stackprof")
    leftover.mkdir()
    (leftover / "ruby.1.folded").write_text("a 1\n")
    assert ruby.record(["true"], output) == 7


def test_the_hook_and_settings_reach_the_command(tmp_path: Path) -> None:
    probe = tmp_path / "env.txt"
    script = (
        f'echo "$RUBYOPT|$PERFPROOF_RUBY_INTERVAL_US|'
        f'$PERFPROOF_RUBY_MODE" > "{probe}"'
    )
    ruby.record(
        ["sh", "-c", script],
        tmp_path / "o.folded",
        interval_us=500,
        mode="wall",
    )
    rubyopt, interval, mode = probe.read_text().strip().split("|")
    assert rubyopt.startswith("-rruby_hook")
    assert (interval, mode) == ("500", "wall")


def test_existing_rubyopt_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUBYOPT", "--yjit")
    probe = tmp_path / "env.txt"
    ruby.record(
        ["sh", "-c", f'echo "$RUBYOPT" > "{probe}"'], tmp_path / "o.folded"
    )
    assert probe.read_text().strip() == "-rruby_hook --yjit"


@pytest.mark.parametrize(("interval", "mode"), [(0, "cpu"), (1000, "gpu")])
def test_bad_settings_are_refused(
    tmp_path: Path, interval: int, mode: str
) -> None:
    with pytest.raises(ValueError, match="interval_us"):
        ruby.record(["true"], tmp_path / "o", interval_us=interval, mode=mode)


def running(pid: int) -> bool:
    """Whether a process is there and not a zombie nobody reaped."""
    done = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    state = done.stdout.strip()
    return bool(state) and not state.startswith("Z")


def test_a_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    script = f'sleep 300 & echo $! > "{pidfile}"; wait'
    started = time.monotonic()

    status = ruby.record(
        ["sh", "-c", script], tmp_path / "o.folded", timeout_seconds=1.0
    )

    assert status == 124
    assert time.monotonic() - started < 30
    time.sleep(0.2)
    assert not running(int(pidfile.read_text()))


def test_a_ruby_without_stackprof_says_so(tmp_path: Path) -> None:
    found = shutil.which("ruby")
    if found is None:
        pytest.skip("no ruby on the PATH")
    has = subprocess.run(
        [found, "-e", "require 'stackprof'"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if has.returncode == 0:
        pytest.skip("stackprof is installed for this ruby")

    status = ruby.record([found, "-e", "1"], tmp_path / "o.folded")

    assert status == 4
    assert not (tmp_path / "o.folded").exists()


@pytest.mark.integration
def test_the_real_stackprof_profiles_a_ruby_program(tmp_path: Path) -> None:
    found = stackprof_ruby()
    output = tmp_path / "hot.folded"

    status = ruby.record(
        [found, str(PROG / "bin" / "hot"), "1.5"], output, timeout_seconds=120
    )

    assert status == 0
    top = summarise_file(output, project_root=PROG, top_n=8)
    names = {h.function: h for h in top}
    slow = names["Work.join_slow"]
    assert slow.file == "lib/work.rb"
    assert slow.is_library is False
    assert slow.self_fraction > 0
    assert any(Path(f"{output}.stackprof").glob("ruby.*.folded"))


@pytest.mark.integration
def test_the_real_profile_follows_a_child_ruby(tmp_path: Path) -> None:
    found = stackprof_ruby()
    output = tmp_path / "child.folded"
    program = f'system("{found}", "{PROG / "bin" / "hot"}", "1")'

    status = ruby.record([found, "-e", program], output, timeout_seconds=120)

    assert status == 0
    fragments = list(Path(f"{output}.stackprof").glob("ruby.*.folded"))
    assert len(fragments) == 2


@pytest.mark.integration
def test_the_real_profile_survives_an_exec(tmp_path: Path) -> None:
    """``bundle exec`` and binstubs exec; the timer outlives the exec.

    Without the hook stopping the profiler before the exec, the new
    program is killed by SIGPROF before it installs its handler.
    """
    found = stackprof_ruby()
    output = tmp_path / "exec.folded"
    program = f'exec("{found}", "{PROG / "bin" / "hot"}", "1")'

    status = ruby.record([found, "-e", program], output, timeout_seconds=120)

    assert status == 0
    top = summarise_file(output, project_root=PROG, top_n=8)
    assert "Work.join_slow" in {h.function for h in top}


@pytest.mark.integration
def test_the_real_profile_works_from_a_path_with_a_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = stackprof_ruby()
    spaced = tmp_path / "hook dir"
    spaced.mkdir()
    shutil.copy(ruby.HOOK, spaced / "ruby_hook.rb")
    monkeypatch.setattr(ruby, "HOOK", spaced / "ruby_hook.rb")
    output = tmp_path / "hot.folded"

    status = ruby.record(
        [found, str(PROG / "bin" / "hot"), "1"],
        output,
        timeout_seconds=120,
    )

    assert status == 0
    assert output.stat().st_size > 0
    top = summarise_file(output, project_root=PROG, top_n=8)
    assert "Work.join_slow" in {h.function for h in top}


@pytest.mark.integration
def test_a_forked_child_does_not_repeat_the_parents_samples(
    tmp_path: Path,
) -> None:
    found = stackprof_ruby()
    output = tmp_path / "fork.folded"
    lib = PROG / "lib"
    program = (
        f'$LOAD_PATH.unshift("{lib}"); require "work"; '
        "Work.histogram(300_000); fork { }; Process.wait; "
        "Work.histogram(300_000)"
    )

    assert ruby.record([found, "-e", program], output) == 0

    parent = max(
        Path(f"{output}.stackprof").glob("ruby.*.folded"),
        key=lambda p: p.stat().st_size,
    )
    total = sum(
        int(x.rpartition(" ")[2]) for x in output.read_text().split("\n") if x
    )
    only = sum(
        int(x.rpartition(" ")[2]) for x in parent.read_text().split("\n") if x
    )
    assert total - only < only * 0.2
