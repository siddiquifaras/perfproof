# Install and first run

perfproof is a `perfproof` command plus the `/perfproof` skill. One
line per agent installs both. Nothing is added to your project's
dependencies. A run keeps its working files in `.perfproof/`, which
perfproof asks git to ignore (it adds the line to `.git/info/exclude`,
so no tracked file changes). Git's own metadata gets that exclude line
and entries under `.git/worktrees/` for the baseline snapshots. The only
ref it ever adds is a `perfproof/<name>` branch for kept changes, and
only after you agree.

## Requirements

- [uv](https://docs.astral.sh/uv/). It fetches Python 3.12 itself, so
  no Python install is needed. perfproof is tested on 3.12 and
  `perfproof doctor` warns on any other.
- git, and the repository you want faster must have at least one
  commit.
- Claude Code or Codex.

## Install

### Claude Code (plugin)

```bash
claude plugin marketplace add siddiquifaras/perfproof && claude plugin install perfproof@perfproof
```

Inside a session the same is `/plugin marketplace add
siddiquifaras/perfproof` then `/plugin install perfproof@perfproof`.
Then say `/perfproof`, or in plain words "make this faster".

What it writes: the plugin goes to Claude Code's own plugin
directory (`~/.claude/plugins/`) and two entries in
`~/.claude/settings.json` (`extraKnownMarketplaces` and
`enabledPlugins`). The plugin ships the skill
(`skills/perfproof/`) and `bin/perfproof`, which Claude Code puts on
the PATH of the agent's shell. That wrapper runs the CLI through
`uvx --python 3.12 --from <plugin directory> perfproof`, so the CLI
lives in uv's cache, not in your project or your Python environment,
and it is always the version the plugin ships. The first call builds
it (12 s in a clean container that already had Python 3.12, with
numpy and scipy to download; more when uv must fetch Python too); later
calls reuse the cache (1 s).

### Codex

```bash
uv tool install --python 3.12 git+https://github.com/siddiquifaras/perfproof && perfproof install-skills --client codex
```

What it writes: `uv tool install` puts `perfproof` on your PATH in its
own environment; `install-skills` copies the skill to
`~/.agents/skills/perfproof/` (the Codex personal skills directory;
`codex debug prompt-input` lists it under its skill roots). Start
`codex` in the repository and ask it to make the code faster.
`perfproof install-skills` with no `--client` installs for both
agents. Running it again updates the copy after
`uv tool upgrade perfproof`.

perfproof's own skill directory carries a marker file. A directory of
that name that perfproof did not create is never replaced or removed
unless you pass `--force`.

### Any other agent that can run shell commands

Install the CLI as for Codex, then point the agent at
`skills/perfproof/SKILL.md`.

## Before the release (2026-11-02)

The repository is private and perfproof is not on PyPI, so the lines
above do not resolve yet. Until then install from a local clone:

```bash
git clone git@github.com:siddiquifaras/perfproof.git perfproof
claude plugin marketplace add ./perfproof && claude plugin install perfproof@perfproof
uv tool install --python 3.12 ./perfproof && perfproof install-skills --client codex
```

At the release (T6.3):

- The repository goes public, which makes the GitHub shorthand and the
  `git+https` URL above work. The skill's fallback
  (`uvx --from git+https://github.com/siddiquifaras/perfproof perfproof`,
  used when `perfproof` is not on PATH) starts working then too.
- perfproof is published to PyPI. Then the Codex line becomes `uv tool
  install --python 3.12 perfproof && perfproof install-skills --client
  codex`, and `bin/perfproof` must change from `--from "$root"` to
  `--from perfproof==<plugin version>` so the plugin pins the released
  CLI instead of building the copy inside it. The plugin version in
  `.claude-plugin/plugin.json` and the PyPI version must then agree.
- The plugin cache holds the whole repository (about 70 MB, mostly
  `reference/` and `studies/`) because the marketplace entry's source is
  the repository root. Once the wrapper installs from PyPI, a smaller
  source (a `git-subdir` or a slim release branch) would do.

## Check it

```bash
perfproof doctor
```

Its last section, **Install**, says whether `perfproof` is on PATH,
whether Claude Code and Codex are found (with versions), and whether the
skill is installed and current for each, with the fix for every gap. A
client that is not on PATH is shown, not reported as a problem: it may
run on another machine. For Claude Code, `doctor` reads the plugin
records under `~/.claude/plugins/` and reports "skill installed through
the plugin perfproof@perfproof 0.1.0"; it does not ask for `perfproof`
on PATH then, since the plugin's wrapper provides it to the agent. It
never suggests a second copy. If a plugin and an `install-skills` copy
both exist it says so (it cannot tell which one Claude Code uses) and
offers to remove the copy. A disabled plugin is reported with
`claude plugin enable perfproof@perfproof`. `claude plugin list` is the
other check.

## First run

Start your agent in the repository and say `/perfproof`. With no
arguments perfproof works out the project from the repository and asks
only what it cannot find out: usually how deep to go (`light`,
`medium` or `full`) and whether to use the workload it proposes. It
needs a clean tree to look for speedups, and never commits, stashes or
edits your branches; kept changes go on a `perfproof/<name>` branch you
choose to merge.

## Uninstall

Claude Code:

```bash
claude plugin uninstall perfproof@perfproof && claude plugin marketplace remove perfproof
```

Codex:

```bash
perfproof install-skills --client codex --uninstall && uv tool uninstall perfproof
```

Delete `.perfproof/` in a repository to forget what perfproof learned
there. uv's cache entry for the plugin's CLI goes with `uv cache
clean perfproof`.
