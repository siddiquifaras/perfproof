# perfproof

perfproof is a `/perfproof` skill for coding agents. It looks for where
your code spends its time, and it checks whether a change really made
the code faster before keeping it.

Benchmarks are noisy: run one twice and the number moves, so a "20%
speedup" from a single before-and-after run is often just that
movement. perfproof runs old and new code in turns on your machine,
checks the answers still match, and keeps a change only when the
measurements back it. The agent does the thinking. A small command-line
tool, `perfproof`, does everything that has to be exact.

It is built for Claude Code. A Codex install exists, but whether a
Codex model drives the skill well has not been verified.

## Install

Status: the repository is private and perfproof is not on PyPI until
the 2026-11-02 launch, so the lines below do not resolve yet. Until
then, install from a clone (see
[docs/install.md](docs/install.md#before-the-release-2026-11-02)).

Claude Code, one line (not yet live):

```bash
claude plugin marketplace add siddiquifaras/perfproof && claude plugin install perfproof@perfproof
```

Then say `/perfproof` in any repository, or "make this faster". You
need [uv](https://docs.astral.sh/uv/) and git. Codex, other agents,
checking the install and uninstalling:
[docs/install.md](docs/install.md).

## What a run looks like

`/perfproof` takes a depth word. The tiers are defined in
[the skill](skills/perfproof/SKILL.md) (the table under "depth").

| Command | What it covers |
|---|---|
| `/perfproof light` | The 2 most-used workloads, ranks what the profiles show, tries the top candidate. The default when you give it a change to prove. |
| `/perfproof` (medium, the default) | Maps how the program is used, runs 3 to 4 workloads, names the files no workload reached, tries the top 2 to 3 candidates and proves the best. |
| `/perfproof deep` | Every entry point, larger sizes, every candidate with a meaningful ceiling, and a second fresh comparison of the best one. |

Each run ends with what was slow and why, what changed, the evidence,
and what was not checked. Kept changes go on a `perfproof/<name>`
branch you choose to merge. Besides that branch, perfproof writes `.perfproof/` (ignored through
`.git/info/exclude`, so no tracked file changes) and baseline
snapshots under `.git/worktrees/`
([docs/install.md](docs/install.md)).

One real run, recorded by the lead in a
[comment on PR #104](https://github.com/siddiquifaras/perfproof/pull/104#issuecomment-6479599232)
(its scripts are not committed; 2026-10-29, a fresh git copy of `examples/rolling`; fields quoted, not a
full transcript): `perfproof init` proposed `target-python_cpu`, then
`project confirm`, then `bench define` with `target_class: python_cpu`
and that class's own schedule. `compare` then gave:

```
KEEP, rule 2, reason performance, calibration: calibrated
target_class python_cpu
estimate 63.86x, interval 59.55 to 68.49 at confidence 0.9958
conditions: hac_lf; schedule (10,10,6,60); alpha 0.05; one host
warnings: high_load, differential_checks_not_run
```

`differential_checks_not_run` means the old-versus-new output
comparison was not run for this KEEP, so outputs were not checked that
way. `high_load` means the machine was busy during the run. This is
one example project with a planted bottleneck, not a typical result.

## What KEEP means

A comparison ends in KEEP, REJECT or INCONCLUSIVE. KEEP means the
interval of the measured speedup, at 1 - alpha/(2K) per look (99.58% on
the python_cpu schedule), lies above the bar (3% by default) and the
safety checks passed. The rule is in
[docs/method.md](docs/method.md); the checks include your tests,
changed-line coverage, and guardrails on memory and tail latency ([guardrails](docs/method.md#guardrails)).

KEEP is calibrated only inside a tested domain
([the calibrated domain](docs/method.md#the-calibrated-domain)):

- the project's class is confirmed and not stale, and the comparison uses that class's
  own run schedule at alpha 0.05;
- classes calibrated: python_cpu, native, web_service, agentic and
  llm_serving, each on its own schedule.

Otherwise KEEP is withheld, the comparison still runs and reports, and
the reason is named. Calibration was checked against real recorded
noise (run of record, seed 661371947387106328: 420 in-class cells, 0
failing; [method.md "Run of record"](docs/method.md#real-noise),
[results CSV](tests/calibration/results/real-seed-661371947387106328.csv),
[PR #104](.claude/worklog.md#commits-and-merges-on-main)). The run also
had node and java traces that belong to no class, so they are not
counted. Counting each class's traces under its own schedule was decided
by the owner after seeing results
([decision](.claude/worklog.md#decisions)):

| Class | Real traces used |
|---|---|
| python_cpu | 10 |
| native | 2 |
| web_service and agentic | 6 |
| llm_serving | 2 (CPU Ollama runs) |

Limits of that evidence:

- **GPU kernels are not calibrated** (no real GPU traces yet), so KEEP
  is withheld for them ([decision](.claude/worklog.md#decisions)).
- **Off-class combinations are a named limit.** Running a class's
  traces under another class's schedule failed in 6 cells, and those
  are published as limits, not hidden
  ([decision](.claude/worklog.md#decisions)).
- **Absolute limits are scoped "in these runs".** A cap such as "memory
  stays under X" holds for the machine as it was during the session; a
  loaded machine can move it
  (["Scope of the claim" in docs/method.md](docs/method.md)).

## Results so far

**Calibrated KEEPs have not yet been exercised by the eval
rounds.** r10 and the earlier rounds ran before calibration was
switched on (PR #104), so no calibrated KEEP was possible in them and
"no wrong KEEP" there proves nothing. The held-out runs came after it,
but reached no verdict (0 of 0 accepted in both reports). No round yet
shows how often a calibrated KEEP is wrong. The evidence for KEEP is the
calibration above.
Eval method: [docs/evaluation.md](docs/evaluation.md).

**Discovery on repositories held out from tuning: the planted
bottleneck was found and named in 2 of 8 (25%, 95% interval about
7% to 59%).** Full passes as scored: 0 of 8
([reports: heldout-1](eval/skill/runs/heldout-1/report.md), [heldout-2](eval/skill/runs/heldout-2/report.md); [narrative](.claude/worklog.md#decisions)). The 8 tasks came from two runs: 4 failed setup in the first.
Three were fixed and run with the same frozen skill; rolldown could not
build in the eval sandbox, so the draw rule passed it over and drew uuid
in its place. The skill was
frozen first; the 8 tasks were drawn by a pre-registered rule
([PR #102](.claude/worklog.md#commits-and-merges-on-main)).

The main failure: on unseen code, the workloads the agent picked mostly
did not reach the slow path (badger, fasthttp, numpy, arrow-rs and
datafusion). In dask it was in the profile but not chosen. To make
this visible, `perfproof` reports files no workload ever sampled
("never sampled"), and the skill says "not covered by these workloads"
rather than "fine"
([PR #105](.claude/worklog.md#commits-and-merges-on-main)).

**Development-set round r10** (55 tasks counted against the bar, $24.38, plus 2 failures excluded
from the count: `real-django-hidden-fix` and `ruby-rails-find`). This is
the tuning set, so it says the skill works there, not that discovery
generalises ([narrative](.claude/worklog.md#decisions)). Reports: [examples](eval/skill/runs/r10-ex/report.md), [real](eval/skill/runs/r10-real/report.md), [Rust](eval/skill/runs/r10-rust/report.md), [.NET](eval/skill/runs/r10-dotnet/report.md), [Java](eval/skill/runs/r10-java/report.md), [Ruby](eval/skill/runs/r10-ruby/report.md), [Go](eval/skill/runs/r10-go/report.md), [native](eval/skill/runs/r10-native/report.md), [Node](eval/skill/runs/r10-node/report.md):

| Family | Tasks passed |
|---|---|
| Examples | 23 of 24 |
| Real repos | 5 of 11 |
| Rust | 3 of 3 |
| .NET, Java, Ruby | 2 of 3, 2 of 3, 2 of 2 |
| Go, native, Node | 2 of 3, 2 of 3, 1 of 3 |
| Total | 42 of 55 |

## Limits and what is not verified

- Discovery is weak on unseen code: 2 of 8 held out, above.
- The held-out set is now spent for tuning claims; its hidden names
  have been seen ([worklog](.claude/worklog.md#decisions)).
- Codex model use is unverified; GPU kernels have no KEEP.
- Comparing runs from different machines or sessions is not supported:
  one identical config moved 16% across two sessions
  ([vLLM case study](docs/case-studies/vllm-prefix-caching.md)).
- Counter metrics need Linux; macOS has no `perf`
  ([AGENTS.md](AGENTS.md)).
- A task set is a sample of real performance work, not all of it
  ([evaluation limits](docs/evaluation.md#limits)).
- No claim here is a speedup you should expect on your code. Only a
  KEEP from your own run is a measurement.

## Docs

[Who it is for](docs/product.md), [the CLI](docs/cli.md),
[discovery](docs/discovery.md), [method](docs/method.md),
[target classes](docs/targets.md),
[failure modes](docs/failure-modes.md),
[architecture](docs/architecture.md).

## License

[Apache-2.0](LICENSE)
