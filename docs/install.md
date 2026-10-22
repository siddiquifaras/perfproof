# Install and first run

perfproof is a `perfproof` command plus the `/perfproof` skill. One
line installs both. Nothing is added to your project's dependencies,
and the only thing a run writes in your repository is `.perfproof/`,
which it asks git to ignore.

## Install

```bash
uv tool install --python 3.12 git+https://github.com/siddiquifaras/perfproof && perfproof install-skills
```

The git-URL line works once the repository is public. Until then,
install from a local clone: `git clone <repo> perfproof && uv tool
install --python 3.12 ./perfproof && perfproof install-skills`.

`uv tool install` puts `perfproof` on your PATH in its own
environment. `perfproof install-skills` copies the skill to where each
agent looks:

| Agent | Skill location | Source of the location |
|---|---|---|
| Claude Code | `~/.claude/skills/perfproof/` | Claude Code skills documentation |
| Codex | `~/.agents/skills/perfproof/` | Codex skills documentation |

Both are the agents' personal skill directories, so `/perfproof` works
in every repository. Use `--client claude` or `--client codex` for one.
Running it again updates the copy after you upgrade perfproof
(`uv tool upgrade perfproof && perfproof install-skills`).

`uv tool install` needs [uv](https://docs.astral.sh/uv/) (it fetches
Python 3.12 itself). `--python 3.12` pins the version perfproof is
tested on; `perfproof doctor` warns on any other. Once perfproof is on PyPI the first half of the line
becomes `uv tool install --python 3.12 perfproof`.

perfproof's own files (the `perfproof` directory it created) carry a
marker file. A directory of that name that perfproof did not create is
never replaced or removed unless you pass `--force`.

## Check it

```bash
perfproof doctor
```

Its last section, **Install**, says whether `perfproof` is on PATH,
whether Claude Code and Codex are found (with versions), and whether the
skill is installed and current for each. Every gap is listed with its fix
(paths shortened here),
for example:

```
Install
  perfproof on PATH
  claude found (2.1.291 (Claude Code)); skill missing: ~/.claude/skills/perfproof
  Missing: the /perfproof skill is missing for claude (~/.claude/skills/perfproof)
    Fix: perfproof install-skills --client claude
```

A client that is not on PATH is shown, not reported as a problem: it
may run on another machine.

## First run

Start your agent in the repository and say `/perfproof`, or in plain
words "make this faster". With no arguments perfproof works out the
project from the repository and asks only what it cannot find out.

If `perfproof` is not on PATH the skill falls back to
`uvx --from git+https://github.com/siddiquifaras/perfproof perfproof`.

## Uninstall

```bash
perfproof install-skills --uninstall && uv tool uninstall perfproof
```

Delete `.perfproof/` in a repository to forget what perfproof learned
there.
