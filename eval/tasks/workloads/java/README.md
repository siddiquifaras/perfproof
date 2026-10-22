# Java task support

- `Dockerfile`: the official `eclipse-temurin:21-jdk` image plus Maven,
  git and Python, built locally as `perfproof-java:21` (never pushed). It
  is the Linux box for repositories with no Maven wrapper
  (`apache-commons-lang-pr1515`, `google-gson-pr3115-landed`), whose
  manifests were run there, and for checking the Java support on Linux.
- `PerfproofJmh.java`: runs one JMH benchmark and one parameter set and
  prints its primary score as `PERFPROOF {"value": ...}`;
  `skills/perfproof/java.md` has an equivalent program inline (without
  the usage check).
- `Pathologies.java`: the source behind the playbook's cheap tests, one hot loop per slowness class
  (`boxing`, `strings`, `collections`, `streams`, `reflection`,
  `exceptions`, `locks`, `deopt`), used to check its cheap tests.

The other workload directories each hold one program named in its
manifest. They all warm up in process until the iteration time stops
drifting, print a checksum of one iteration (the output an oracle
compares) and then `PERFPROOF {"value": ns per operation}`; see
"Warm-up (Java)" in `docs/method.md`.

`evidence/workload-source.txt` is the exact program that produced the
fixtures in `tests/fixtures/java/` and the warm-up series. `warmup-runs.txt`
holds the excerpt of the output behind the numbers in
"Warm-up (Java)" of `docs/method.md`.

`eval/skill/tasks-java.json` is in the real-repository task format of
the skill eval's `task/t3.19-real-repo-tasks` branch (PR #60); it does
not load with the loader on `main` before that merges. The tasks need a working `java` and
`javac` first on the `PATH` (on macOS the stub in `/usr/bin` is not
one). They run on the host with the JDK on the `PATH`, no container:

```bash
PATH=$JAVA_HOME/bin:$PATH uv run python eval/skill/skill_eval.py \
    --tasks-file eval/skill/tasks-java.json run --label java-1
```

The workload runs recorded in the six Java manifests predate the warm-up
rule now in the committed workloads (a floor of 200 iterations, 1%
tolerance, a cap of 600 that reports instead of failing). The iteration
bodies are unchanged, but the program that ran is not the committed one,
so each manifest has `workload.verified` false and status `builds` until
the workloads are run again on a quiet machine.
