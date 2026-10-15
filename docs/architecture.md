# Architecture

The design target for the release. `.claude/plan.md` tracks what
exists. [discovery.md](discovery.md) specifies understand and discover;
[method.md](method.md) specifies prove.

## Shape

Three parts behind one engine, reached three ways:

```
 Claude Code ─┐
 Codex ───────┼─ MCP (stdio) ─┐       ┌─ understand: project model, workloads, goals
 other client ┘               ├─ engine ─ discover: angles, hypotheses, cheap tests
 shell, CI ───── CLI ─────────┘       └─ prove: gates, interleaved rounds, verdict
                                              │
                                  local runner ── remote runner (SSH, Modal)
 skills/ ── playbooks per target, and the /perfproof loop
```

The split of responsibility is fixed:

- **Deterministic code** owns state, measurement, statistics, gates,
  undo, budgets and every record.
- **The agent** owns judgment: which hypothesis to pursue, how to write
  a candidate, and whether a new idea repeats an old one.

The MCP server and the CLI are thin. They parse arguments, call the
engine, and turn its results into compact JSON. Nothing that decides a
verdict lives in them.

## Packages

| Package | Part | Job | Rules |
|---|---|---|---|
| `perfproof.schema` | all | Pydantic models every other package exchanges | The only shared vocabulary. Changing it is its own task. |
| `perfproof.understand` | understand | The project model in `project.json`: targets, workloads, goals, constraints, how to run; confirmation and staleness | Unknown stays unknown; the developer confirms before anything is measured. |
| `perfproof.targets` | understand | One module per target class behind one shared interface: detection, workloads, runner, metrics, guardrail measurement, correctness oracle, coverage support, angles, settings space | Every module passes the same conformance suite. Detection carries its evidence and confidence. |
| `perfproof.discover` | discover | Measurement angles, ranking by cap, cheap tests, settings search, choosing among candidates | Every cap is a labelled bound; only prove makes claims. |
| `perfproof.hypotheses` | discover | The append-only hypotheses log | The code finds and shows history; the agent judges whether an idea repeats one. |
| `perfproof.stats` | prove | Intervals, sequential looks, decision rule, guardrails, noise simulation | Pure functions on NumPy arrays. No I/O, no clock, no global RNG. |
| `perfproof.bench` | prove | Spec and lock, snapshots, runner, correctness, differential checks, coverage gate, side-effect guard, compare | Owns subprocesses and timing. |
| `perfproof.machine` | prove | Fingerprint and warnings | Read-only probes. A missing probe is `unknown`, never a guess. |
| `perfproof.profilers` | discover | One adapter per profiler, all returning `Hotspot` lists | Adapters stream large outputs and return the top N. |
| `perfproof.remote` | prove | SSH and Modal runners | The remote side runs the same engine, so interleaving happens on one host. |
| `perfproof.jobs` | all | Long calls as background jobs with progress and resume | No call blocks longer than a client's tool timeout. |
| `perfproof.report` | prove | JSON record and Markdown report | Renders the record. Computes nothing new. |
| `perfproof.optimize` | all | State of a `/perfproof` session: the cycle, budgets, kept changes, undo | Never discards a change it didn't make. |
| `perfproof.mcp`, `perfproof.cli` | all | MCP tools and command line | Thin. |

## On disk

perfproof keeps its state in `.perfproof/` inside the user's
repository, which it adds to `.git/info/exclude`:

```
.perfproof/
  project.json                 targets, workloads, goals, constraints
  benchmarks/<name>/
    <spec-hash>.json           one spec version and its lock manifest
  hypotheses/<workload>.jsonl  every hypothesis, test and verdict, append-only
  worktrees/<sha>/             baseline and candidate checkouts
  jobs/<job-id>.json           progress of a background job
  runs/<run-id>/
    record.json                the full machine-readable result
    report.md                  the readable report
    samples.npz                raw per-round samples
    profiles/                  profiler outputs, untouched
```

A benchmark's name is how people and agents refer to it. Its spec hash
identifies one exact version: changing any spec field gives a new hash
and a new file beside the old one, never an overwrite. Every run record
carries the full spec and its hash, and two specs are never treated as
the same because they share a name.

## MCP tools

Tool outputs stay small and structured so the agent's context stays
clean. Raw data goes to disk and the tool returns its path. Any call
that can outlast a client's tool timeout returns a job ID at once.

| Tool | Part | Returns |
|---|---|---|
| `project` | understand | The project model; proposes changes for the developer to confirm |
| `detect_target` | understand | Target classes, entry points, candidate workloads, with evidence |
| `define_benchmark` | prove | Spec ID and the locked file manifest |
| `snapshot` | prove | Baseline commit and worktree path |
| `profile` | discover | Top hotspots with `file:line`, profile path |
| `discover` | discover | Ranked opportunities, each with its angle, evidence, cap and past hypotheses |
| `hypothesis` | discover | Records or updates a hypothesis and its cheap test |
| `explain_hotspot` | discover | Source excerpt and profile detail for one hotspot |
| `measure` | prove | Median, spread, noise estimate, rounds |
| `check_correctness` | prove | Pass or fail, with the first differences |
| `compare` | prove | Verdict, rule, speedup, interval, gates, guardrails, warnings |
| `job` | all | Status, progress and result of a background job |
| `report` | prove | Path to the report and a short summary |

Remote execution is a `host` argument on `measure`, `profile` and
`compare`, not a separate tool.

## Agent integration

- **MCP:** a stdio server started by `perfproof mcp`. Any MCP client can
  launch it with `uvx perfproof mcp`.
- **Skills:** `skills/perfproof/` is the `/perfproof` loop; one
  playbook per target class sits beside it. Agent Skills format, with
  only the `name` and `description` frontmatter every client accepts.
  `perfproof install-skills` copies them to `.claude/skills/` for
  Claude Code or `.agents/skills/` for Codex.
- **CLI:** every MCP tool has a CLI equivalent, for CI and for agents
  without MCP.
