# perfproof report: `sum-loop`, run `20261015-120000-sum-loop`

## Verdict

**REJECT**, reason `correctness`, decided by rule 1, gate `coverage`.

The candidate changed lines that no check ran: the `coverage` gate failed. Gates run before any timing, so this verdict carries no speedup estimate and says nothing about speed. A REJECT keeps nothing.

## Claim

The candidate failed the `coverage` gate. No claim about speed.

Benchmark `sum-loop` at spec hash `3161a99a0edca6144e31ffa036a85b881579498185c158241c1ec30d8e57bc65`, baseline commit `0123456789abcdef0123456789abcdef01234567`. Metric `wall` in `s`, lower is better, objective `wall_time`; min_effect = 0.03 as stored.

Not recorded: the repository and the candidate's commit.

## Measured

Not measured: gates run before any timing, so this verdict carries no speedup estimate or interval.

- Rounds: 0, of at most 200
- Looks: 0, of at most 5
- Procedure: `t_log_ratio`
- Regression: no
- Cross host: no

### Diagnostics

- procedure_uncalibrated: no
- metric_disagreement: not computed
- wall_interval: not computed
- coarse_resolution: not computed
- lag1_autocorrelation: not computed
- cv_baseline: not computed
- cv_candidate: not computed
- sd_log_ratio: not computed
- resolution_widening: not computed

## Gates

- `side_effects`: passed
- `lock`: passed
- `procedure`: passed
- `runtime`: passed
- `same_host`: passed
- `correctness`: passed
- `coverage`: failed
  - Detail: changed lines never ran under any check
  - Unverified lines in `src/app/io.py`: 152, 153, 158
  - Unverified lines in `src/app/util.py`: 7

## Guardrails

None evaluated: a gate REJECT carries no guardrail results. The spec's guardrails are under Settings.

## Calibration

- Interval procedure: `t_log_ratio`
- Procedure uncalibrated when this verdict was given: no
- Target class: `python_cpu`
- Calibration for the target class: `calibrated`
- Calibrated under these conditions only, not in general: log-normal, CV 5%, one host; AR(1), rho 0.5, one host

## Warnings

None recorded.

## Machines

### Session 0

- captured_at: 2026-10-15T12:00:00+00:00
- host_id: `host-1`
- os: `Darwin`
- os_version: `15.6`
- kernel: `24.6.0`
- arch: `arm64`
- cpu_model: `Apple M4 Pro`
- physical_cores: 12
- logical_cores: 12
- frequency_policy: unknown (macOS exposes no frequency governor)
- load_average: 1.5, 1.25, 1.0
- power_source: `ac`
- battery_percent: 80.0
- thermal_state: unknown (pmset -g therm timed out)
- memory_bytes: 51539607552
- python_version: `3.12.12`
- gpus: none

## Environments

### `baseline`, session 0

- runtime_kind: `interpreter`
- runtime_executable: `/work/baseline/.venv/bin/python`
- runtime_version: `3.12.12`
- env_names: `HOME`, `OMP_NUM_THREADS`, `PATH`
- linked_libraries: not applicable
- env_values `OMP_NUM_THREADS`: `1`
- dependency_lock `uv.lock`: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`

### `candidate`, session 0

- runtime_kind: `interpreter`
- runtime_executable: `/work/candidate/.venv/bin/python`
- runtime_version: `3.12.12`
- env_names: `HOME`, `OMP_NUM_THREADS`, `PATH`
- linked_libraries: not applicable
- env_values `OMP_NUM_THREADS`: `1`
- dependency_lock `uv.lock`: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`

## Settings

Fixed in the spec before any round.

- name: `sum-loop`
- spec_hash: `3161a99a0edca6144e31ffa036a85b881579498185c158241c1ec30d8e57bc65`
- schema_version: 3
- command: `["python", "bench.py", "--n", "1000"]`
- cwd: `.`
- env `OMP_NUM_THREADS`: `1`
- env `PYTHONHASHSEED`: `0`
- metric: kind `wall`, lower is better, unit `s`
- objective: `wall_time`
- correctness command: `["python", "-m", "pytest", "tests/test_core.py"]`
- correctness cwd: `.`
- correctness timeout_seconds: 120.0
- correctness output `out/result.npy`: compare `numbers`, rtol 1e-09, atol 0.0, equal_nan no
- warmup: 2
- budget: max_rounds 200, max_seconds 600.0
- looks: n0 10, k 10, max_looks 5
- min_effect: 0.03
- alpha: 0.05
- timeout_seconds: 30.0
- harness: `bench.py`
- oracle: `tests/test_core.py`
- inputs: `data/input.bin`
- config: `pyproject.toml`
- allow_runtime_change: no
- allow_cross_host: no
- allow_remote_services: no
- allow_unchecked_coverage: no
- reset: none
- guardrail `peak_memory`
  - applicable: yes
  - direction: lower is better
  - unit: `bytes`
  - reason: none given
  - allowance: 0.1 from `default`
  - limit: container memory limit, 8000000000.0 bytes
- guardrail `p99_latency`
  - applicable: no
  - direction: lower is better
  - unit: `s`
  - reason: a batch job serves no requests
  - allowance: none

## Run

- run_id: `20261015-120000-sum-loop`
- perfproof_version: `0.1.0`
- schema_version: 3
- started_at: 2026-10-15T12:00:00+00:00
- finished_at: 2026-10-15T12:03:00+00:00
- baseline_commit: `0123456789abcdef0123456789abcdef01234567`
- order_seed: 12345

## Files

Relative to the run directory.

- Samples: none, no round ran
- Profiles: none
