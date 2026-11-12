# perfproof

perfproof is a performance engineering system for coding agents. It
does two jobs: it **discovers** where a codebase can get faster, from
angles its developers haven't tried, and it **proves** which changes are
real improvements, keeping only those. Under it sits a model of the
project (**understand**): its workloads, goals and constraints. It is
a small exact core, the `perfproof` CLI, plus Markdown skills that
drive it. The CLI owns everything that must be exact; the skills own
judgment. It has to work equally well from Claude Code, Codex or any
agent that can run shell commands, on code it has never seen and on
code it has worked on for years. Target classes: Python, native, web
services, LLM serving, GPU kernels, agentic apps.

Every component exists to stop a failure listed in
`docs/failure-modes.md`. If a change adds a component that stops none,
it doesn't belong.

Discovery without proof is a list of suggestions someone still has to
check. Proof without discovery needs someone else's ideas. A verdict
that calls a change faster when it isn't is worse than no tool, so
every claim perfproof makes goes through prove.

## Commands

```bash
uv sync                          # Python 3.12+, uv only
uv run pytest                    # fast suite
uv run pytest -m calibration     # Monte Carlo calibration, slow
uv run ruff format . && uv run ruff check .
uv run pyright
```

`uv run` creates the virtualenv on first use, including in a fresh
worktree.

## Where to read before editing

| Before you touch | Read |
|---|---|
| anything | `.claude/plan.md` for your task |
| `src/perfproof/stats/` | `src/perfproof/stats/AGENTS.md` |
| `src/perfproof/profilers/` | `src/perfproof/profilers/AGENTS.md` |
| `skills/` | `skills/AGENTS.md` |
| any new component | `docs/failure-modes.md`: which failure it stops |
| a CLI command or its `--json` output | `docs/cli.md` (the interface) |
| project model, discovery angles, hypotheses | `docs/discovery.md` (the spec) |
| decision logic, gates, metrics | `docs/method.md` (the spec) |
| a target class's oracle, guardrails or boundary | `docs/targets.md` |
| `eval/` | `docs/evaluation.md` |
| module boundaries, on-disk layout, prior art | `docs/architecture.md` |

## Evidence

- Never write a benchmark number, speedup or profiler output that did
  not come from a run. If something wasn't run, say so.
- Keep "expected" apart from "measured" in code, docs and PRs.
- Test fixtures for profiler and tool output are captured from real runs
  of that tool, with the tool version recorded next to them. Never
  hand-write them.
- An unknown stays `unknown`. Probes that fail return `None` with a
  reason, never a plausible default.

## Writing

Everything a person reads (README, docs, commit messages, PR text,
comments, CLI messages) should sound like one engineer wrote it, not a
committee or a model. Owner's reference:
<https://siddiquifaras.github.io/blog.html>.

- Short paragraphs, 2 to 4 sentences. One-liners for emphasis are fine.
  Never a wall of paragraphs; reach for a list, table or diagram.
- First person singular in the README and posts ("I built this
  because..."), plain "you" for the reader. Contractions are fine.
- Sentence-case headings, ideally a claim ("KEEP has to mean
  something"), never Title Case.
- Lists: short, 3 or 4 items, a bold lead-in when it helps.
- Numbers with units and their source. A claim without a run behind it
  doesn't ship.
- Dry, brief humour where it lands. One line, never forced, never at
  the reader's expense.
- Diagrams (Mermaid, which GitHub renders) where they explain flow or
  state better than prose, each with a one-line caption.
- British spelling in prose. Code identifiers keep their existing
  spelling.
- No em dashes, no exclamation marks, no hype ("blazing",
  "revolutionary", "seamless"), no filler ("It's worth noting that",
  "In summary", "Let's dive in").
- Commit subjects: imperative, what changed, under 72 characters
  ("compare: withhold KEEP when no test ran"). Body only when the why
  isn't obvious. No co-author trailers.
- Identity: every commit, tag, package field and doc uses
  `Faras Siddiqui <siddiquifaras@gmail.com>`, from the git config. Never
  override `user.email`, never another address.

## Code standard

- PEP 8, enforced by ruff at 79 columns. Pyright strict must pass.
- NumPy-style docstrings on every public module, class and function.
  Document units, shapes, dtypes and what is raised. Private helpers get
  a docstring when the why isn't obvious from the name and signature.
- Comments explain why, never what. No commented-out code, TODOs or
  placeholders; ruff rejects them.
- Type hints everywhere. Data crossing a package boundary is a
  `perfproof.schema` model. Inside `stats`, it is NumPy arrays.
- Subprocesses take argv lists, never `shell=True`. Every external call
  has a timeout.

## Efficiency

This tool runs inside other people's benchmark loops and on machines
whose noise we are trying to measure. Overhead is a bug.

- Vectorize. Bootstrap resamples are one `(B, n)` index draw and one
  `axis=1` reduction, not a Python loop. Monte Carlo replications are a
  leading array axis.
- Bound memory. Chunk any `(R, B, n)` work to a fixed byte budget.
- Stream large inputs. Profiler outputs can be gigabytes; aggregate line
  by line and keep only the top N.
- State the complexity in the docstring when it isn't linear.
- Nothing perfproof does between rounds may perturb the next round:
  no allocation spikes, no disk flush bursts, no background threads.

## Tests

- Every behaviour has a test that would fail without it. Decision logic
  is tested against `docs/method.md` row by row.
- Statistical claims are tested by simulation with a fixed seed and a
  stated Monte Carlo error, under the `calibration` marker.
- Anything that crosses a process, network or GPU boundary gets an
  `integration` or `gpu` test against the real tool. Mocks don't count
  as proof there.

## Working in parallel

Several agents work on this repo at once, each in its own worktree and
branch.

- Take one task from `.claude/plan.md` whose dependencies and milestone
  gate are met. Edit only the paths it owns. If you need to change
  anything else, stop and report it.
- Never edit `.claude/plan.md` from a task branch. The lead ticks tasks
  off after merging.
- `perfproof.schema` is the shared contract. Changing it is its own
  task.
- Only tasks that list a dependency change touch `pyproject.toml` or
  `uv.lock`. On a lock conflict, rerun `uv lock`; never hand-merge it.
- Branch `task/<id>-<slug>`. Push and open a PR. Never merge, never
  push to `main`, never force-push.
- A PR description has: task ID, what changed, the commands you ran
  with their results, and what you could not verify.

## Paid compute

GPU runs on Modal or rented hosts cost real money from a small budget.
Before any paid run, state the GPU, expected duration and cost estimate,
and wait for approval. Fail fast: check the server is healthy before
benchmarking and print the log tail on failure.

## Gotchas

- `reference/` is frozen evidence from the hand-run vLLM study. It is
  excluded from lint and type checks. Borrow ideas from it, don't import
  it or restyle it.
- Comparing runs from different hosts or sessions moved one identical
  config by 16%. Never compare across hosts without flagging it.
- Interval bounds come out of `exp` of log-scale arithmetic, so a bound
  meant to sit exactly on the bar can land a few ulps to either side.
  The decision rule's boundary handling is deliberate and tested.
- macOS has no `perf`. Counter metrics are Linux-only in v1.
