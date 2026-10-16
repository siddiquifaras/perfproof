# perfproof report: `sum-loop`, run `20261015-120000-sum-loop`

## Verdict

**INCONCLUSIVE**, reason `insufficient_evidence`, decided by rule 5.

Only 2 cross-host session pairs were completed, fewer than n0 = 10, and below n0 speed never decides, so the interval decided nothing whatever it shows. Neither a speedup above the bar nor its absence is shown. Nothing is kept.

Labels:

- Not calibrated for this target class.
- Cross-host: decided under the alternating fresh-host session design, with 2 session pairs. It rests on the assumption that the pool allocates hosts independently of which version runs, which perfproof cannot check. Calibration of this design cannot cover a pool that hands one version systematically different hosts. Gate `same_host` waived by `allow_cross_host`.

## Claim

No claim. The evidence recorded here does not settle whether the candidate should be kept.

Benchmark `sum-loop` at spec hash `71b53044231a1438cd68c11c3138bcfd0d619a8ec17fc8be91aa78fee85f8fb0`, baseline commit `0123456789abcdef0123456789abcdef01234567`. Metric `wall` in `s`, lower is better, objective `wall_time`; min_effect = 0.03 as stored.

Not recorded: the repository and the candidate's commit.

## Measured

- Speedup estimate: 1.12; above 1 means the candidate is faster
- Interval at the last look: 1.04 to 1.2 at confidence 0.99
- Rounds: 40, of at most 200
- Looks: 1, of at most 5
- Procedure: `t_log_ratio`
- Regression: no
- Cross host: yes
- Session pairs: 2

### Diagnostics

- procedure_uncalibrated: no
- metric_disagreement: not computed
- wall_interval: not computed
- lag1_autocorrelation: 0.05
- cv_baseline: 0.02
- cv_candidate: 0.03
- sd_log_ratio: 0.01

## Gates

- `side_effects`: passed
- `lock`: passed
- `procedure`: passed
- `runtime`: passed
- `same_host`: waived
  - Detail: cross-host session design under allow\_cross\_host
- `correctness`: passed
- `coverage`: passed

## Guardrails

- `peak_memory`: passed
  - Unit `bytes`, lower is better
  - Worsening ratio estimate: 1.02; above 1 means the candidate is worse
  - Allowance: 0.1 from `default`, refused beyond 1 + allowance
  - Worsening interval: 0.99 to 1.05 at confidence 0.995
  - Absolute limit, a ceiling: container memory limit, 8000000000.0 bytes
  - Candidate interval: 500000000.0 to 600000000.0 bytes at confidence 0.995
- `p99_latency`: not applicable
  - Reason: a batch job serves no requests

## Calibration

- Interval procedure: `t_log_ratio`
- Procedure uncalibrated when this verdict was given: no
- Target class: `python_cpu`
- Calibration for the target class: `uncalibrated`
- Not calibrated for this target class.

## Warnings

- `host_types_differ`: session 3 has no GPU reading; sessions 0-2 have NVIDIA L4

## Machines

### Session 0

- captured_at: 2026-10-15T12:00:00+00:00
- host_id: `modal-0`
- os: `Linux`
- os_version: unknown (no /etc/os-release in the container)
- kernel: `6.8.0-1015-gcp`
- arch: `x86_64`
- cpu_model: `Intel Xeon @ 2.20GHz`
- physical_cores: 4
- logical_cores: 8
- frequency_policy: `performance`
- load_average: 1.5, 1.25, 1.0
- power_source: unknown (no power supply class in /sys)
- battery_percent: unknown (no battery)
- thermal_state: unknown (no thermal zone readable)
- memory_bytes: 51539607552
- python_version: `3.12.12`
- gpu 0
  - name: `NVIDIA L4`
  - driver_version: `560.35.03`
  - sm_clock_mhz: 2040.0
  - memory_clock_mhz: unknown (nvidia-smi reported \[N/A\])

### Session 1

- captured_at: 2026-10-15T12:01:00+00:00
- host_id: `modal-1`
- os: `Linux`
- os_version: unknown (no /etc/os-release in the container)
- kernel: `6.8.0-1015-gcp`
- arch: `x86_64`
- cpu_model: `Intel Xeon @ 2.20GHz`
- physical_cores: 4
- logical_cores: 8
- frequency_policy: `performance`
- load_average: 1.5, 1.25, 1.0
- power_source: unknown (no power supply class in /sys)
- battery_percent: unknown (no battery)
- thermal_state: unknown (no thermal zone readable)
- memory_bytes: 51539607552
- python_version: `3.12.12`
- gpu 0
  - name: `NVIDIA L4`
  - driver_version: `560.35.03`
  - sm_clock_mhz: 2040.0
  - memory_clock_mhz: unknown (nvidia-smi reported \[N/A\])

### Session 2

- captured_at: 2026-10-15T12:02:00+00:00
- host_id: `modal-2`
- os: `Linux`
- os_version: unknown (no /etc/os-release in the container)
- kernel: `6.8.0-1015-gcp`
- arch: `x86_64`
- cpu_model: `Intel Xeon @ 2.20GHz`
- physical_cores: 4
- logical_cores: 8
- frequency_policy: `performance`
- load_average: 1.5, 1.25, 1.0
- power_source: unknown (no power supply class in /sys)
- battery_percent: unknown (no battery)
- thermal_state: unknown (no thermal zone readable)
- memory_bytes: 51539607552
- python_version: `3.12.12`
- gpu 0
  - name: `NVIDIA L4`
  - driver_version: `560.35.03`
  - sm_clock_mhz: 2040.0
  - memory_clock_mhz: unknown (nvidia-smi reported \[N/A\])

### Session 3

- captured_at: 2026-10-15T12:03:00+00:00
- host_id: `modal-3`
- os: `Linux`
- os_version: unknown (no /etc/os-release in the container)
- kernel: `6.8.0-1015-gcp`
- arch: `x86_64`
- cpu_model: `Intel Xeon @ 2.20GHz`
- physical_cores: 4
- logical_cores: 8
- frequency_policy: `performance`
- load_average: 1.5, 1.25, 1.0
- power_source: unknown (no power supply class in /sys)
- battery_percent: unknown (no battery)
- thermal_state: unknown (no thermal zone readable)
- memory_bytes: 51539607552
- python_version: `3.12.12`
- gpus: unknown (nvidia-smi timed out after 10 s)

## Environments

### `baseline`, session 0

- runtime_executable: `/work/baseline/.venv/bin/python`
- runtime_version: `3.12.12`
- env_names: `HOME`, `OMP_NUM_THREADS`, `PATH`
- env_values `OMP_NUM_THREADS`: `1`
- dependency_lock `uv.lock`: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`

### `candidate`, session 1

- runtime_executable: `/work/candidate/.venv/bin/python`
- runtime_version: `3.12.12`
- env_names: `HOME`, `OMP_NUM_THREADS`, `PATH`
- env_values `OMP_NUM_THREADS`: `1`
- dependency_lock `uv.lock`: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`

### `baseline`, session 2

- runtime_executable: `/work/baseline/.venv/bin/python`
- runtime_version: `3.12.12`
- env_names: `HOME`, `OMP_NUM_THREADS`, `PATH`
- env_values `OMP_NUM_THREADS`: `1`
- dependency_lock `uv.lock`: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`

### `candidate`, session 3

- runtime_executable: `/work/candidate/.venv/bin/python`
- runtime_version: `3.12.12`
- env_names: `HOME`, `OMP_NUM_THREADS`, `PATH`
- env_values `OMP_NUM_THREADS`: `1`
- dependency_lock `uv.lock`: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`

## Settings

Fixed in the spec before any round.

- name: `sum-loop`
- spec_hash: `71b53044231a1438cd68c11c3138bcfd0d619a8ec17fc8be91aa78fee85f8fb0`
- schema_version: 2
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
- allow_cross_host: yes
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
- schema_version: 2
- started_at: 2026-10-15T12:00:00+00:00
- finished_at: 2026-10-15T12:03:00+00:00
- baseline_commit: `0123456789abcdef0123456789abcdef01234567`
- order_seed: 12345

## Files

Relative to the run directory.

- Samples: [`samples.npz`](<samples.npz>)
- Profile: [`profiles/baseline.speedscope.json`](<profiles/baseline.speedscope.json>)
