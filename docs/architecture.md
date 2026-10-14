# Architecture

The design target for v1. `.claude/plan.md` tracks what exists.

## Shape

One engine, three ways in:

```
 Claude Code ─┐
 Codex ───────┼─ MCP (stdio) ─┐
 other client ┘               ├─> engine ─> local runner ──> the user's code
 shell, CI ───── CLI ─────────┘          └─> remote runner ─> SSH host / Modal
 skills/ ── playbooks the agent reads, one per target
```

The MCP server and the CLI are thin. They parse arguments, call the
engine, and turn its results into compact JSON. Nothing that decides a
verdict lives in them.

## Packages

| Package | Job | Rules |
|---|---|---|
| `perfproof.schema` | Pydantic models every other package exchanges: benchmark spec, sample, verdict, hotspot, machine fingerprint, run record | The only shared vocabulary. Changes need the lead. |
| `perfproof.stats` | Intervals, sequential looks, decision rule, noise simulation | Pure functions on NumPy arrays. No I/O, no clock, no global RNG. |
| `perfproof.bench` | Spec and lock, worktree snapshots, warmup, interleaved rounds, correctness gate | Owns subprocesses and timing. |
| `perfproof.machine` | Fingerprint and warnings (power, load, thermal, frequency policy, GPU clocks) | Read-only probes. A missing probe is `unknown`, never a guess. |
| `perfproof.profilers` | One adapter per profiler, all returning `Hotspot` lists | Adapters stream large outputs and return the top N. |
| `perfproof.targets` | Detect the project type and propose a benchmark | Heuristics with explicit confidence. |
| `perfproof.remote` | SSH and Modal runners | The remote side runs the same engine, so interleaving happens on one host. |
| `perfproof.report` | JSON record and Markdown report | Renders the record. Computes nothing new. |
| `perfproof.mcp` | MCP tool definitions | Thin. |
| `perfproof.cli` | Command line | Thin. |

## On disk

perfproof keeps its state in `.perfproof/` inside the user's
repository, which it adds to `.git/info/exclude`:

```
.perfproof/
  benchmarks/<name>.json     spec and lock manifest
  worktrees/<sha>/           baseline checkouts
  runs/<run-id>/
    record.json              the full machine-readable result
    report.md                the readable report
    samples.npz              raw per-round samples
    profiles/                profiler outputs, untouched
```

## MCP tools

Tool outputs stay small and structured so the agent's context stays
clean. Raw data goes to disk and the tool returns its path.

| Tool | Returns |
|---|---|
| `detect_target` | Project type, entry points, a suggested benchmark command |
| `define_benchmark` | Spec ID and the locked file manifest |
| `snapshot` | Baseline commit and worktree path |
| `measure` | Median, spread, noise estimate, rounds |
| `profile` | Top hotspots with `file:line`, profile path |
| `explain_hotspot` | Source excerpt and profile detail for one hotspot |
| `check_correctness` | Pass or fail, with the first differences |
| `compare` | Verdict, rule, speedup, interval, gates, warnings |
| `report` | Path to the report and a short summary |

Remote execution is a `host` argument on `measure`, `profile` and
`compare`, not a separate tool.

## Agent integration

- **MCP:** a stdio server started by `perfproof mcp`. Any MCP client can
  launch it with `uvx perfproof mcp`.
- **Skills:** `skills/` holds one playbook per target in the Agent
  Skills format, with only the `name` and `description` frontmatter
  every client accepts. `perfproof install-skills` copies them to
  `.claude/skills/` for Claude Code or `.agents/skills/` for Codex.
- **CLI:** every MCP tool has a CLI equivalent, for CI and for agents
  without MCP.
