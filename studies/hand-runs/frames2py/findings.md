# frames2py hand-run findings

Transcripts: `1-bare.md` (T1), `2-free-text.md` (T2),
`3-named-function.md` (T3), `4-existing-change.md` (T4). "T2 Step 10"
means transcript 2, the step headed **Step 10**.

Severity:

- **block**: the skill can't do the step without a workaround;
- **high**: the result is wrong or misleading unless the skill
  catches it;
- **med**: costs time or needs a judgment call;
- **low**: friction.

Nothing here was patched. Where a finding is an inference, not
something measured, it says so.

## What happened, in one table

| Entry | Workload | Candidate | perfproof said | Interval | Load |
|---|---|---|---|---|---|
| T1 bare | exp_decay 640x480 100k @16ms, engine | contiguous `t` before max | INCONCLUSIVE 0.82x | 0.56x to 1.21x | 40.5 |
| T1 rerun | same | same | INCONCLUSIVE 1.00x | 0.94x to 1.06x | 2.7 |
| T2 free text | decode `active_marker.raw` (EVT3, 108.7 MB) | 2^20-word slices | REJECT integrity (runtime gate), then INCONCLUSIVE 0.73x | 0.70x to 0.77x | 13.8 |
| T3 named fn | time_surface, kernel level | packed x/y index | INCONCLUSIVE 0.90x | 0.87x to 0.94x | 7.1 |
| T4 agent PR 1 | exp_decay engine | `bincount` in ExpDecay | INCONCLUSIVE 0.42x | 0.40x to 0.45x | 7.5 |
| T4 agent PR 2 | EVT3 decode | 2^17-word slices | INCONCLUSIVE 0.91x | 0.88x to 0.93x | 8.8 |

None of the six estimates is above 1. Four have intervals
entirely below 1 (uncalibrated), and every comparison reads
INCONCLUSIVE.

## Findings

| # | Affects | Severity | Finding | Shown at |
|---|---|---|---|---|
| 1 | `compare` verdict wording (until calibration) | high | Open question for T3.12. Estimates of 0.42x (interval 0.40 to 0.45), 0.73x, 0.90x and 0.91x all printed `INCONCLUSIVE insufficient_evidence`, because the procedure is uncalibrated, and the first line doesn't show that the whole interval lies below 1. Should the skill, or the report, say anything beyond "INCONCLUSIVE"? Reporting a direction from an INCONCLUSIVE result may conflict with how the method treats it. Not decided here. | T2 Step 11, T3 Step 7, T4 Step 4, 5 |
| 2 | `compare` guardrails (T2.15) | high | `peak_memory` is "unmeasurable", yet `samples.npz` has `max_rss_bytes` for every execution. By hand: +7.2% for bincount, +55% (69.2 to 106.9 MiB) for 2^17 slices, and about 3.1x (67.7 to 209.6 MiB) for 2^20 slices. The last two would be refused at a 10% allowance. Neither shows up in the verdict or report. | T4 Step 4, 5 |
| 3 | `compare` runtime gate; worktree reuse | high | The same spec gave REJECT `integrity` because a reused baseline worktree still had a `.venv` from an earlier run and the fresh candidate worktree didn't. The result depends on leftover state, and a setup fault is labelled REJECT. | T2 Step 10 |
| 4 | `profile` (pyinstrument) | block | It needs an interpreter followed by a script or `-m`. `uv run python …`, the usual uv form, and `python -c` are refused. `--profiler-command` can't wrap it, because `{command}` is the whole argv. Meanwhile compare needs `uv run` (finding 3). Every benchmark therefore needed two specs, one for each command. | T1 Step 7, T2 Step 6, 10, T3 Step 4 |
| 5 | `profile` focus | high | It profiles the whole process, with no way to restrict it to the timed region or a named function. The named function was **1.0%** of the profile, and the harness's untimed reference check was 90.8%. I filtered the folded stacks by hand every time. | T1 Step 7, T3 Step 5 |
| 6 | `profile` line attribution | med | For NumPy-heavy code, pyinstrument charges control-flow lines with the cost of the vectorised statements before them: `if not is_vector.any()` got 12.9% and `return out` 3.7%. The skill must read the block, not trust the line. | T2 Step 7 |
| 7 | `profile` waiting line | med (partly fixed on main) | In T1 the "43% waiting" line was CPU contention at load 9 to 12. `main` now adds "The load exceeds the CPU count…", but only above 1.0 per CPU. | T1 Step 7, T2 Step 7 |
| 8 | `profile` side effects | low | It runs in the user's tree and leaves the benchmark's outputs there (`perfproof-out/`, untracked). | T1 Step 7 |
| 9 | `init` (missing) | block | The skill hand-reads CONTRIBUTING, finds 150 gate cells and two levels, and must ask which cell and statistic. For free text it had to find the code with `grep`, find the harness, and discover that the real recordings were already cached outside the repo. | T1 Step 2, T2 Step 1, 2 |
| 10 | spec: wrapper scripts | high | frames2py's harness writes JSON and prints no `PERFPROOF` line, so the skill wrote the measuring script every time, as inline `-c` or a file outside the repo. That script decides the number, yet it is never locked and never shown to the developer for review. | T1 Step 4, T2 Step 4, 6 |
| 11 | spec: locked paths are repo-relative only | high | The real input (a 108.7 MB recording in `~/.cache`) and the harness script lived outside the repo, so the lock couldn't cover either. `harness` needs at least one entry, which forced a stand-in path that locks nothing that matters. | T2 Step 6 |
| 12 | spec: no target class | med | Every spec must declare `peak_gpu_memory` and `p99_latency` not applicable for a CPU library, or compare refuses with `undeclared_guardrails`. | T1 Step 6 |
| 13 | correctness gate scope | high | It runs only the spec's test command. A candidate that broke frames2py's mypy CI passed it. The skill should add the project's CI static checks (mypy here) or say they weren't run. | T3 Step 6 |
| 14 | correctness gate cost | high | The oracle runs 3 + 1 times, and the full suite took 524 s at load 23. The skill had to choose subsets (604 or 91 tests) and get the user's agreement. The subset passed `bincount`, and so did 1,443 tests. | T1 Step 2, 5; T4 Step 1, 2 |
| 15 | oracle design (skill) | high | `bincount*w` changes float64 state pixels (162,007 after 60 calls in T1's cheap test; 71,681 after 2,000 calls in T4's side-by-side run) while every float32 frame is identical over 2,000 calls. A frame-bytes check can't see it. Whether that counts as "bit-identical" is the developer's ruling, and the skill must ask. | T1 Step 8, T4 Step 3 |
| 16 | skill: load before measuring | high | At load 40, CV was 0.54 to 0.66 and the run took 425 s. At load 2.7, the same compare had CV 0.08 to 0.11 and took 99.5 s. `doctor` warns, but nothing stops a compare that can't decide. The skill should check load first and wait or warn. | T1 Step 4, 10, addendum |
| 17 | skill: noise before compare; `measure` missing | high | There is no command to check baseline noise or run cheap tests. Six identical runs varied 3.2x to 3.5x, and microbenchmarks moved 3.4x between runs. | T1 Step 4, 8 |
| 18 | skill: cheap tests that don't represent the workload | high | The slice-size cheap test (linear fit at 2^11 to 2^15, on a single 32 MiB feed) predicted a 1.17x cap. The compare estimate was 0.73x (interval 0.70 to 0.77, uncalibrated, load 13.83). The after-the-fact sweep disagreed with compare in size (2^19 took 3% more time than 2^15 in the sweep; compare gave 0.73x). Cheap tests must use the workload's real path and cover the range they extrapolate to. | T2 Step 8, 12 |
| 19 | skill: history | med | Two ideas were already countered by the developer's history (`6b09b2d` moved off bincount) or by a line with no comment that a microbenchmark showed was load-bearing (`ascontiguousarray`: about 17x as long without it, load 9.50). The skill must read `git log -S` and test "redundant" code before proposing to remove it. | T1 Step 8, T3 Step 2, T4 |
| 20 | skill: measured region vs execution cost | med | The time_surface cell spent 90.8% of each 11 s execution in the harness's reference check. The skill had to cut `timed_calls` to keep compare affordable. perfproof doesn't report how small the measured region is next to the execution. | T3 Step 5 |
| 21 | skill: metric choice | med | In T1 (loaded), `wall_interval` (0.83 to 1.10) was narrower than the reported metric's (0.56 to 1.21). In T3 the wall metric gave 0.98 to 1.03 while the metric's estimate was 0.90 (0.87 to 0.94), uncalibrated, because the measured region is 1% of wall. Neither metric is always better, and the CLI doesn't help choose. | T1 Step 10, T3 Step 7 |
| 22 | `compare` with several spec versions | low | "has 2 matching specs; pick one with --spec-hash" doesn't say which is newer. | T3 Step 7 |
| 23 | `compare` report | med | INCONCLUSIVE doesn't say what would settle it (rounds needed at this noise), which `docs/cli.md` promises. | T1 Step 10 |
| 24 | `compare` record | med | "Not recorded: the repository and the candidate's commit." The record can't be traced to the candidate. | T1 Step 10 |
| 25 | `compare` progress | med | 2 to 7 minutes in the foreground with no output (`--detach` and `status` aren't built). | T1 Step 10, T2 Step 11 |
| 26 | `doctor` | low | Reports perfproof's Python (3.12.12), not the project's (3.11.14), and doesn't list pyinstrument or py-spy. | T1 Step 1 |
| 27 | `bench list` / `show` | low | `list` needs a name, and `show` needs the full hash. | T1 Step 6 |
| 28 | prove fast path (skill) | med | Proving an agent PR needed: the diff, the benchmark that covers the changed code (found by hand), the claim in the commit message checked against history and earlier cheap tests, the tests, and output drift beyond the oracle. There is no command for "which benchmark covers this diff". | T4 |
