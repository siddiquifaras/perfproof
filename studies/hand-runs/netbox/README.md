# Hand-runs on NetBox (T3.10, part 2)

The `/perfproof` skill (T3.8) does not exist yet. Here a person played
the skill by hand on NetBox, a Django REST API, using the perfproof CLI
as it stands, to find out what the skill must do and where the CLI
falls short. Each transcript is one entry point, as if the user typed
it.

The agent messages in the transcripts are condensed from the step logs
(`scratch/logs/e1..e4.log`, not committed): wording is shortened, and
numbers are the logs' own, rounded where a message says so (for
example 6.2% shown as "about 6%").

| File | Entry point | The user's line |
|---|---|---|
| [e1-bare.md](e1-bare.md) | bare | `/perfproof` |
| [e2-free-text.md](e2-free-text.md) | free text | `/perfproof the device list API is slow` |
| [e3-named.md](e3-named.md) | a named function | `/perfproof GenericObjectSerializer.get_object in netbox/api/serializers/generic.py` |
| [e4-prove-change.md](e4-prove-change.md) | an existing change | `/perfproof I had an agent speed up the cable list API. It made two branches, agent/cable-prefetch and agent/cable-defer. Are they faster?` |
| [findings.md](findings.md) | | every gap, by CLI command or skill decision |
| [harness.md](harness.md) | | the benchmark harness, specs and by-hand scripts exactly as they ran |

## Every compare that ran

All on one host, `compare` from PR #24 (`463161b`), 20 rounds unless
a gate stopped it first. Intervals at confidence 0.9875; speedup above
1 means the candidate is faster. None of these is a calibrated
verdict.

| Entry | Spec | Candidate | Outcome | Estimate | Interval | Wall | Load before → after |
|---|---|---|---|---|---|---|---|
| e1 | `api-mix` | H1, cable FK prefetch | REJECT correctness (harness put its port in the output; NetBox's query pin) | none | none | 1505.9 s | 28.80 → 48.79 |
| e1 | `api-mix-2` | H1 + pin update | INCONCLUSIVE | 1.115 | 1.023 to 1.214 | 370.5 s | 7.29 → 10.35 (28.84 mid-run) |
| e2 | `devices-list` | H3, filters built once per class | INCONCLUSIVE | 1.067 | 0.931 to 1.221 | 421.3 s | 8.73 → 7.90 |
| e3 | `cables-list` | H1 + H2, GenericPrefetch + pin update | INCONCLUSIVE | 5.072 | 4.728 to 5.440 | 257.4 s | 8.02 → 8.72 |
| e4 | `cables-list` | agent/cable-prefetch as given | REJECT integrity, gate side_effects (no harness in the tree) | none | none | 1.5 s | 7.51 |
| e4 | `cables-list` | agent commit on the harness | REJECT correctness (NetBox's query pin) | none | none | 221.3 s | 7.74 → 11.15 |
| e4 | `cables-list` | agent commit + pin update | INCONCLUSIVE | 5.736 | 5.169 to 6.365 | 289.8 s | 12.32 → 11.30 |
| e4 | `cables-list` | agent/cable-defer on the harness | REJECT correctness (NetBox's query pin) | none | none | 164.8 s | 11.52 → 6.68 |
| e4 | `cables-list` | agent/cable-defer + pin raised, measurement only | INCONCLUSIVE | 0.772 | 0.645 to 0.924 | 326.0 s | 5.72 → 8.85 |

## Read this before any number

- **No verdict here is calibrated.** `compare` is the thin path of PR
  #24: until T1.6/T1.7 pass, every comparison that clears its gates
  ends INCONCLUSIVE, reason `insufficient_evidence`, with the
  diagnostic `procedure_uncalibrated`. The intervals it prints are
  recorded, not decided on.
- **The machine was heavily loaded.** Other agents were building Rust
  and running tests on the same 10-core M4. 1-minute load averages
  from 3.4 to 48.8 were recorded across the steps (each step lists its
  load before and after); most steps of entries 2–4 ran between 5 and
  13. In entry 1 the hand-runner's own test run also overlapped several
  measurements (e1 §4). Timings taken minutes apart are not comparable; only the
  interleaved rounds inside one `compare` are.
- **Expected is kept apart from measured.** Caps and predictions are
  labelled as bounds or predictions. Every number labelled as measured
  came from a command shown in a transcript; two observations made
  outside the step log (a `ps` listing, one `uptime`) are shown as such
  where they are used.
- The user's replies are **simulated** by the hand-runner, marked
  `USER (simulated)`, written as a NetBox operator would plausibly
  answer. They are not Faras's words.

## Setup

All of it local, nothing pushed anywhere.

| Piece | Version or state |
|---|---|
| NetBox | `netbox-community/netbox` at tag `v4.7.3` (`3cd2c1d`), cloned to `scratch/netbox` |
| Demo data | `netbox-community/netbox-demo-data` at `faeae59`, `sql/netbox-demo-v4.7.sql` |
| Postgres | `postgres:15-alpine` (15.15), container `nb-pg`, `--cpus 2 --memory 1g`, `127.0.0.1:55432` |
| Redis | `redis:7-alpine` (7.4.7), container `nb-redis`, `--cpus 1 --memory 256m`, `127.0.0.1:56379` |
| Python | CPython 3.12.12 in `scratch/netbox/.venv`, NetBox's `requirements.txt` with `psycopg[binary,pool]` in place of `psycopg[c,pool]` (no `pg_config` on this Mac) |
| Server | gunicorn 26.2.0, one worker, one thread, started by the harness from the tree under test |
| perfproof | `main` at `98c22d3` for `doctor`, `bench`; `origin/task/t2.10-profile` at `055eca4` (PR #23) for `profile`; `origin/task/t2.5-compare` at `463161b` (PR #24) for `compare` |
| Machine | Apple M4, 10 cores, 16 GB, macOS 15.7.7, on AC power |

Setup steps that were not perfproof's business but that the skill will
meet on any real Django project:

1. `postgres:17-alpine` was not cached and its pull stalled; NetBox
   4.7 needs PostgreSQL 15 or later, so the cached 15 image was used.
2. The v4.7 demo dump does not load cleanly: 11 `CREATE TRIGGER`
   statements on ltree `path` columns fail with `operator does not
   exist: public.ltree = public.ltree`, because pg_dump sets
   `search_path` to empty and the triggers' `WHEN (old.path IS
   DISTINCT FROM new.path)` resolves `=` unqualified. The data rows
   load. The 11 triggers were recreated with `SET search_path =
   public`, giving the dump's 22 ltree triggers.
3. The loaded database was copied to `netbox_pristine` with `CREATE
   DATABASE ... TEMPLATE netbox`, so a reset would be `DROP`/`CREATE
   ... TEMPLATE`. No run here needed one: every request in every
   workload is a GET, and `compare` refuses a spec with a reset anyway
   (findings F-07).
4. NetBox reads `netbox/netbox/configuration.py`, which is gitignored,
   so it doesn't exist in a tree `compare` checks out from git. The
   harness carries its own config module (`perfproof-bench/nbconfig.py`)
   and selects it with `NETBOX_CONFIGURATION` (findings F-05).
5. The database password and Django secret key are random local test
   values in gitignored files under `scratch/`; the configs read them
   from there. Neither appears in this study.

## Reproduce

The harness and specs are in [harness.md](harness.md). From a clone of
this repository with the containers up and the demo data loaded:

```bash
uv run perfproof bench define api-mix --spec specs/api-mix.json --repo scratch/netbox --json
```

`profile` and `compare` ran from detached worktrees of the PR branches,
for example `uv run --project scratch/pp-compare perfproof compare ...`.
