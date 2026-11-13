# T4.2 held-out labels

I labelled the held-out tasks on two Hetzner CCX13 machines, Ubuntu 24.04,
one in Falkenstein (`hetzner-fsn1-ccx13`) and one in Helsinki
(`hetzner-hel1-ccx13`), both running `eval/label/label.py run --held-out`
under the rule in `eval/label/README.md`.

## Setup

- Both machines ran from a bundle at 6650240. That is the pre-rewrite ID of
  main's 2e12e99d. The SHA-256 of every rule file recorded in the results
  matches the file on current main (f75edd49), so the rule is the same.
- 19 tasks were attempted on each machine. gin-gonic-gin-pr2365 was dropped
  by decision before the run and is not counted.
- 16 tasks have a valid result on both machines, 200 pairs each, and were
  combined with `label.py combine`. Three failed setup on both machines and
  have no label (below).
- Per-machine results and pyperf files are in
  `eval/label/results/label/<machine>/`, the combined labels in
  `eval/label/results/final/`, and each machine's pair logs in
  `pairs.tgz` beside its results.

## Labels

Each cell is the machine's label and its bootstrap geometric-mean speedup of
the reference tree over the task tree.

| Task | fsn1 | hel1 | Combined |
|---|---|---|---|
| agno-agi-agno-pr3013 | improvement (1.308) | improvement (1.364) | improvement |
| astral-sh-uv-pr20879 | below-bar (1.000) | below-bar (1.002) | below-bar |
| crewaiinc-crewai-pr2382-landed | below-bar (1.001) | below-bar (1.000) | below-bar |
| crewaiinc-crewai-pr5187 | improvement (1.038) | improvement (1.040) | improvement |
| crewaiinc-crewai-pr5584 | improvement (1.127) | improvement (1.119) | improvement |
| django-django-pr20911 | improvement (1.331) | improvement (1.412) | improvement |
| gin-gonic-gin-pr4466 | below-bar (1.008) | below-bar (1.011) | below-bar |
| honojs-hono-pr5118-landed | below-bar (0.999) | below-bar (1.000) | below-bar |
| nestjs-nest-pr18136 | improvement (1.818) | improvement (1.770) | improvement |
| networkx-networkx-pr7810 | improvement (12.613) | improvement (18.697) | improvement |
| nlohmann-json-pr5836 | improvement (1.709) | improvement (1.872) | improvement |
| psf-black-pr3467 | improvement (40.880) | improvement (43.767) | improvement |
| pydata-xarray-pr10921-landed | below-bar (0.998) | below-bar (1.007) | below-bar |
| python-mypy-pr20802 | below-bar (0.999) | below-bar (0.998) | below-bar |
| swe-perf-sympy-sympy-26546 | below-bar (0.999) | below-bar (1.000) | below-bar |
| tokio-rs-tokio-pr5829 | improvement (1.045) | improvement (1.112) | improvement |

Result: 9 improvement, 7 below-bar, 0 unstable, 0 regression.

## Flags

- **gin-gonic-gin-pr4466 is compile-dominated.** Its workload runs
  `docker run --rm golang:1.25 go test ...` with only the module cache
  mounted, so from the command every execution appears to recompile
  (I didn't time a build on its own). The mean wall time is about 54 s
  per run on fsn1 (54.3 s for the task tree, 53.9 s for the reference), so
  the benchmark is drowned. Treat its `below-bar` as not about the change.
- **gin-gonic-gin-pr2365** has the same workload pattern, so I dropped it
  from this run rather than spend about 6 h per machine on it.
- **scipy-scipy-pr24913-landed** fails setup on Linux: its recipe passes
  `-Dblas=accelerate -Dlapack=accelerate`, which is macOS only.
- **libuv-libuv-pr2064** fails `cmake --build build -j 8` on both machines:
  the linker reports multiple definitions of `handle` and `sock` in the
  `uv_run_tests` target.
- **run-llama-llama-index-pr21755** fails setup on `uv sync --python
  3.14+gil`: uv found no interpreter for `3.14+gil` in managed installations
  or the search path.
- networkx-networkx-pr7810 (12.6 and 18.7) and psf-black-pr3467 (40.9 and
  43.8) have very large speedups, and the two machines differ by a lot on
  networkx. Both are `improvement` under the rule. I have not looked into why.

## Correctness

None of the 16 has a suite recipe. `eval/label/suites.json` holds recipes for
the three pilot tasks only, and every held-out result records
`suite_recipe: null`. So all 16 correctness labels are `unknown`. The task's
own oracle exited 0 on the reference tree on both machines for all 16, but the
rule needs the suite to run before it gives `pass`.

## What this does and doesn't establish

It gives 16 performance labels, each reproduced on two machines of the same
type, made under a rule committed before the run. For the 9 improvements the
effect is above the 3% bar on both machines.

It does not give correctness labels, and it does not cover 3 of the 19
attempted tasks. Both machines are the same CCX13 type, so this says nothing
about other hardware. The gin-gonic-gin-pr4466 label is not about the change.
I have not run the A/A check on these tasks, and no perfproof run has been
scored against these labels yet.
