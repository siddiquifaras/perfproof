# perfproof report: `sum-loop`, run `20261015-120000-sum-loop`

## Verdict

**INCONCLUSIVE**, reason `insufficient_evidence`, decided by rule 1.

The resolution stop ended the comparison at a look: a value was no larger than its own resolution, or a `reported` metric with no declared resolution failed the continuity check (`coarse_resolution`). Neither a speedup above the bar nor its absence is shown. Nothing is kept. More printed digits, a declared resolution or more work per execution can resolve it.

Labels:

- Not calibrated for this target class.

## Claim

No claim. The evidence recorded here does not settle whether the candidate should be kept.

Benchmark `sum-loop` at spec hash `25bd9c39955a2c613e3da76f3bc00702ee0dee642660e120acbbc218891dbb58`, baseline commit `0123456789abcdef0123456789abcdef01234567`. Metric `wall` in `s`, lower is better, objective `wall_time`; min_effect = 0.03 as stored.

Not recorded: the repository and the candidate's commit.

## Measured

- Speedup estimate: not computed
- Interval at the last look: not computed
- Rounds: 10, of at most 60
- Looks: 1, of at most 6
- Procedure: `t_log_ratio`
- Regression: no
- Cross host: no

### Diagnostics

- procedure_uncalibrated: no
- metric_disagreement: not computed
- wall_interval: not computed
- coarse_resolution: yes
- lag1_autocorrelation: 0.05
- cv_baseline: 0.02
- cv_candidate: 0.03
- sd_log_ratio: 0.01
- resolution_widening: 0.2

## Gates

- `side_effects`: passed
- `lock`: passed
- `procedure`: passed
- `runtime`: passed
- `same_host`: passed
- `correctness`: passed
- `coverage`: passed

## Guardrails

- `peak_memory`: passed
  - Unit `bytes`, lower is better
  - Worsening ratio estimate: 1.02; above 1 means the candidate is worse
  - Regression within the allowance: costs 2.00% on the point estimate, inside the allowance of 10.00% from `default`. The allowance is a refusal threshold, not a budget to spend
  - Allowance: 0.1 from `default`, refused beyond 1 + allowance
  - Worsening interval: 0.99 to 1.05 at confidence 0.995
  - Absolute limit, a ceiling: container memory limit, 8000000000.0 bytes
  - Candidate bounds in these runs: 500000000.0 to 600000000.0 bytes; this describes the session and can move on a busier or quieter machine
  - Limit container memory limit: median 540000000.0 bytes; in these runs it stayed under 600000000.0 bytes (cap 8000000000.0 bytes, 7400000000.0 bytes to spare); 0 of 40 rounds over the cap. On a busier or quieter machine this number can move
- `p99_latency`: not applicable
  - Reason: a batch job serves no requests

## Calibration

- Interval procedure: `t_log_ratio`
- Procedure uncalibrated when this verdict was given: no
- Target class: `python_cpu`
- Calibration for the target class: `uncalibrated`
- Not calibrated for this target class.

## Warnings

- `high_load`: 1-minute load average is 1.5

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
- spec_hash: `25bd9c39955a2c613e3da76f3bc00702ee0dee642660e120acbbc218891dbb58`
- schema_version: 4
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
- budget: max_rounds 60, max_seconds 600.0
- looks: n0 10, k 10, max_looks 6
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
- schema_version: 4
- started_at: 2026-10-15T12:00:00+00:00
- finished_at: 2026-10-15T12:03:00+00:00
- baseline_commit: `0123456789abcdef0123456789abcdef01234567`
- order_seed: 12345

## Files

Relative to the run directory.

- Samples: [`samples.npz`](<samples.npz>)
- Profile: [`profiles/baseline.speedscope.json`](<profiles/baseline.speedscope.json>)
