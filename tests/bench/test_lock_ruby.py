"""The runtime gate for Ruby: ``ruby -v``, a hash, and Bundler.

The tests that need Ruby run the real ``ruby`` on the ``PATH`` (this
Mac's system Ruby 2.6.10, or the official image's Ruby 4.0 in the
Linux check, see ``tests/fixtures/ruby/SOURCE.md``) and skip with their
reason where there is none. ``bundle`` and the version managers are
never run: an empty executable of that name stands in for them, because
the gate does not start them.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from test_lock_runtimes import gate, make_spec, probe, tree

from perfproof.bench.lock import fetching_launch, hash_file
from perfproof.schema import GateResult

RUBY_COMMAND = ("ruby", "bench.rb")


def real_ruby() -> Path:
    found = shutil.which("ruby")
    if found is None:
        pytest.skip("no ruby on the PATH")
    return Path(found)


def ruby_says(extra: str) -> str:
    done = subprocess.run(
        [str(real_ruby()), "-e", extra],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return done.stdout.strip()


def stand_in(directory: Path, name: str, text: str = "") -> Path:
    """An executable file the gate must identify without running it."""
    path = directory / name
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def path_with(directory: Path) -> str:
    return f"{directory}{os.pathsep}{os.environ['PATH']}"


@pytest.mark.integration
def test_ruby_is_identified_by_its_description_and_a_hash(
    tmp_path: Path,
) -> None:
    real_ruby()
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(RUBY_COMMAND)

    env = probe("baseline", spec, base)
    result = gate(spec, base, cand)

    assert result.status == "passed", result.detail
    assert env.runtime_kind == "interpreter"
    description = ruby_says("puts RUBY_DESCRIPTION")
    ruby = Path(ruby_says("require 'rbconfig'; puts RbConfig.ruby"))
    assert env.runtime_version is not None
    assert env.runtime_version.startswith(description)
    digest, _ = hash_file(ruby)
    assert env.runtime_version.endswith(f"; sha256 {digest}")
    assert env.runtime_executable == str(ruby)


@pytest.mark.integration
def test_a_shim_reports_the_ruby_it_starts_not_itself(tmp_path: Path) -> None:
    ruby = real_ruby()
    shims = tmp_path / "shims"
    shims.mkdir()
    stand_in(shims, "ruby", f'#!/bin/sh\nexec "{ruby}" "$@"\n')
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(RUBY_COMMAND)

    env = probe("baseline", spec, base, path=path_with(shims))
    plain = probe("baseline", spec, base)
    result = gate(spec, base, cand, path=path_with(shims), cand_path=None)

    assert env.runtime_kind == "interpreter"
    assert env.runtime_version == plain.runtime_version
    assert env.runtime_executable == plain.runtime_executable
    assert result.status == "passed", result.detail


@pytest.mark.integration
def test_rubyopt_cannot_forge_the_report(tmp_path: Path) -> None:
    real_ruby()
    base = tree(tmp_path, "a")
    (base / "forge.rb").write_text(
        "puts '/forged'\nputs 'ruby 0.0.0'\nexit! 0\n"
    )
    spec = make_spec(RUBY_COMMAND)

    plain = probe("baseline", spec, base)
    forged = probe(
        "baseline", spec, base, extra={"RUBYOPT": f"-r{base}/forge.rb"}
    )

    assert forged.runtime_version == plain.runtime_version
    assert forged.runtime_executable == plain.runtime_executable


@pytest.mark.integration
def test_bundle_exec_is_followed_to_its_command(tmp_path: Path) -> None:
    real_ruby()
    tools = tmp_path / "tools"
    tools.mkdir()
    stand_in(tools, "bundle")
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(("bundle", "exec", "ruby", "bench.js"))

    env = probe("baseline", spec, base, path=path_with(tools))
    direct = probe("baseline", make_spec(RUBY_COMMAND), base)
    result = gate(spec, base, cand, path=path_with(tools))

    assert env.runtime_version == direct.runtime_version
    assert result.status == "passed", result.detail


def unknown_reason(spec_command: tuple[str, ...], tmp_path: Path) -> str:
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    for name in ("bundle", "rbenv", "gem"):
        stand_in(tools, name)
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(spec_command)
    result: GateResult = gate(spec, base, cand, path=path_with(tools))
    assert result.status == "not_available", result.detail
    return result.detail


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (("bundle", "config"), "only bundle exec is identified"),
        (("bundle", "install"), "bundle install downloads gems"),
        (("bundle", "exec"), "names no command"),
        (("bundle", "exec", "--gemfile=x", "ruby"), "starts with an option"),
        (("rbenv", "exec", "ruby", "bench.js"), "picks its own runtime"),
    ],
)
def test_forms_that_are_not_followed_stay_unknown(
    tmp_path: Path, command: tuple[str, ...], reason: str
) -> None:
    assert reason in unknown_reason(command, tmp_path)


def test_a_bundle_exec_of_an_unknown_program_stays_unknown(
    tmp_path: Path,
) -> None:
    detail = unknown_reason(("bundle", "exec", "no-such-tool"), tmp_path)
    assert "no-such-tool" in detail


@pytest.mark.parametrize(
    ("argv", "word"),
    [
        (("bundle", "install"), "bundle install"),
        (("bundle", "update", "rails"), "bundle update"),
        (("gem", "install", "stackprof"), "gem install"),
        (("gem", "exec", "x"), "gem exec"),
        (("gem", "inst", "x"), "gem inst"),
        (("gem", "--verbose", "install", "x"), "gem install"),
        (("bundle",), "no command"),
        (("bundle", "i"), "bundle i"),
        (("bundle", "remove", "x"), "bundle remove"),
    ],
)
def test_gem_downloads_are_fetching_launches(
    tmp_path: Path, argv: tuple[str, ...], word: str
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bundle", "gem"):
        stand_in(tools, name)
    env = {"PATH": str(tools)}
    reason = fetching_launch(argv, env, tmp_path)
    assert reason is not None
    assert word in reason


def test_bundle_exec_and_ruby_are_not_fetching_launches(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bundle", "ruby"):
        stand_in(tools, name)
    env = {"PATH": str(tools)}
    assert fetching_launch(("bundle", "exec", "rake"), env, tmp_path) is None
    assert fetching_launch(("ruby", "bench.rb"), env, tmp_path) is None


@pytest.mark.integration
def test_a_binstub_is_followed_through_its_shebang(tmp_path: Path) -> None:
    real_ruby()
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        stand_in(root / "bin", "rake", "#!/usr/bin/env ruby\nputs 1\n")
    spec = make_spec(("bin/rake",))

    env = probe("baseline", spec, base)
    direct = probe("baseline", make_spec(RUBY_COMMAND), base)

    assert env.runtime_kind == "interpreter"
    assert env.runtime_version == direct.runtime_version
    assert gate(spec, base, cand).status == "passed"


DASH_S = [
    ("-S",),
    ("-wS",),
    ("-vS",),
    ("-W2S",),
    ("-WS",),
    ("-lS",),
    ("-0S",),
    ("-00S",),
    ("-l0S",),
    ("-KuS",),
    ("-w", "-S"),
    ("-I", "lib", "-S"),
    ("--encoding", "utf-8", "-S"),
    ("--external-encoding", "utf-8", "-S"),
    ("--internal-encoding", "utf-8", "-S"),
    ("--enable", "gems", "-S"),
    ("--disable", "gems", "-S"),
    ("-I", "--", "-S"),
    ("--", "-S"),
]


@pytest.mark.integration
@pytest.mark.parametrize("flags", DASH_S, ids=" ".join)
def test_ruby_dash_s_is_not_followed(
    tmp_path: Path, flags: tuple[str, ...]
) -> None:
    """A command line that may run a PATH program is unknown."""
    real_ruby()
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec(("ruby", *flags, "rake"))
    result = gate(spec, base, cand)
    assert result.status == "not_available", result.detail
    assert "-S" in result.detail


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        ("ruby", "bench.rb"),
        ("ruby", "-w", "bench.rb"),
        ("ruby", "--yjit", "bench.rb"),
        ("ruby", "-rset", "bench.rb"),
    ],
    ids=" ".join,
)
def test_commands_without_a_dash_s_flag_pass(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    real_ruby()
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    assert gate(make_spec(command), base, cand).status == "passed"


@pytest.mark.integration
def test_rubylib_cannot_forge_the_report(tmp_path: Path) -> None:
    real_ruby()
    base = tree(tmp_path, "a")
    lib = base / "lib"
    lib.mkdir()
    (lib / "rbconfig.rb").write_text(
        "module RbConfig; def self.ruby; '/forged'; end; end\n"
    )
    spec = make_spec(RUBY_COMMAND)
    plain = probe("baseline", spec, base)
    forged = probe("baseline", spec, base, extra={"RUBYLIB": str(lib)})
    assert forged.runtime_executable == plain.runtime_executable
    assert forged.runtime_version == plain.runtime_version
