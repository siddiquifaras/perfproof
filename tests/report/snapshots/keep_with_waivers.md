# perfproof report: `sum-loop`, run `20261015-120000-sum-loop`

## Verdict

**KEEP**, reason `performance`, decided by rule 2: speedup 1.08, interval 1.05 to 1.11.

The interval's lower bound is above the bar 1 + min_effect, so the candidate is faster than the baseline by more than min_effect on this benchmark's metric. Every gate passed or was waived by the spec's opt-in, and every guardrail passed or is not applicable.

Labels:

- Not calibrated for this target class.
- Remote services allowed: the benchmark may reach a service address whose host is not local. Gate `side_effects` waived by `allow_remote_services`.
- Runtime change allowed: the interpreter or runtime may differ between the trees. Gate `runtime` waived by `allow_runtime_change`.
- Coverage not checked. Gate `coverage` waived by `allow_unchecked_coverage`.

## Claim

The candidate is faster than the baseline by a ratio above 1 + min_effect on this benchmark's metric, on the machines recorded under Machines, with the checks under Gates and Guardrails. The claim covers this benchmark command and its declared inputs only. The labels under Verdict qualify it.

Benchmark `sum-loop` at spec hash `7938e704347063fa954b68d120e9b10f2fa0088fbb971c51c7655ad8f073ebe1`, baseline commit `0123456789abcdef0123456789abcdef01234567`. Metric `wall` in `s`, lower is better, objective `wall_time`; min_effect = 0.03 as stored.

Not recorded: the repository and the candidate's commit.

## Measured

- Speedup estimate: 1.08; above 1 means the candidate is faster
- Interval at the last look: 1.05 to 1.11 at confidence 0.99
- Rounds: 40, of at most 200
- Looks: 4, of at most 5
- Procedure: `t_log_ratio`
- Regression: no
- Cross host: no

### Diagnostics

- procedure_uncalibrated: no
- metric_disagreement: not computed
- wall_interval: not computed
- coarse_resolution: not computed
- lag1_autocorrelation: 0.05
- cv_baseline: 0.02
- cv_candidate: 0.03
- sd_log_ratio: 0.01
- resolution_widening: not computed

## Gates

- `side_effects`: waived
  - Detail: DATABASE\_URL points at db.internal, not local
- `lock`: passed
- `procedure`: passed
- `runtime`: waived
  - Detail: the candidate upgrades numpy
- `same_host`: passed
- `correctness`: passed
- `coverage`: waived
  - Detail: perfproof can't instrument this target yet

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
- Target class: `native`
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
- spec_hash: `7938e704347063fa954b68d120e9b10f2fa0088fbb971c51c7655ad8f073ebe1`
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
- budget: max_rounds 200, max_seconds 600.0
- looks: n0 10, k 10, max_looks 5
- min_effect: 0.03
- alpha: 0.05
- timeout_seconds: 30.0
- harness: `bench.py`
- oracle: `tests/test_core.py`
- inputs: `data/input.bin`
- config: `pyproject.toml`
- allow_runtime_change: yes
- allow_cross_host: no
- allow_remote_services: yes
- allow_unchecked_coverage: yes
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
- started_at: 2026-10-15T17:30:00+05:30
- finished_at: 2026-10-15T17:34:05+05:30
- baseline_commit: `0123456789abcdef0123456789abcdef01234567`
- order_seed: 12345

## Files

Relative to the run directory.

- Samples: [`samples.npz`](<samples.npz>)
- Profile: [`profiles/baseline (run 1).speedscope.json`](<profiles/baseline (run 1).speedscope.json>)
- Profile: [`profiles/candidate<2>.folded`](<profiles/candidate\<2\>.folded>)
