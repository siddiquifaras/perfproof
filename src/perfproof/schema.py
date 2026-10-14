"""Data contracts shared by every perfproof package.

Each model here is a frozen Pydantic model. Anything that crosses a
package boundary, is written to disk, or is returned by the MCP server
or the CLI is one of them, so field names and meanings follow
``docs/method.md`` and ``docs/architecture.md``.

All models validate strictly: unknown fields are refused, values are
not coerced between types, and floats must be finite. The root
documents (`BenchmarkSpec`, `LockManifest`, `RunRecord`) carry a
``schema_version``. The checked-in ``schema.json`` next to this module
is the exported JSON Schema; regenerate it with
``uv run python -m perfproof.schema > src/perfproof/schema.json``.
"""

import hashlib
import itertools
import json
import sys
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal, Self, cast

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    Strict,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic.json_schema import models_json_schema

__all__ = [
    "MODELS",
    "SCHEMA_VERSION",
    "Arm",
    "BenchmarkSpec",
    "Budget",
    "CalibrationStatus",
    "CorrectnessSpec",
    "Diagnostics",
    "Difference",
    "Direction",
    "GateName",
    "GateResult",
    "GateStatus",
    "GpuInfo",
    "Hotspot",
    "Interval",
    "LockManifest",
    "LockRole",
    "LockedFile",
    "LookSchedule",
    "MachineFingerprint",
    "MetricKind",
    "MetricSpec",
    "Outcome",
    "OutputCheck",
    "Procedure",
    "Reason",
    "Rule",
    "RunRecord",
    "RunWarning",
    "Sample",
    "TargetClass",
    "Verdict",
    "json_schema",
    "json_schema_text",
]

SCHEMA_VERSION: Final = 1
"""Version of the on-disk format; bumped on any incompatible change."""

MetricKind = Literal["wall", "cpu", "reported", "counter"]
Direction = Literal["lower_is_better", "higher_is_better"]
Arm = Literal["baseline", "candidate"]
Outcome = Literal["KEEP", "REJECT", "INCONCLUSIVE"]
Reason = Literal[
    "performance", "correctness", "integrity", "insufficient_evidence"
]
Rule = Literal[1, 2, 3, 5]
"""Decision rule that decided, numbered as in ``docs/method.md``.

Rule 4 (continue) never ends a comparison, so no verdict carries it.
"""
GateName = Literal["correctness", "lock", "procedure", "runtime", "same_host"]
GateStatus = Literal["passed", "failed", "waived"]
LockRole = Literal["harness", "oracle", "input", "config"]
Procedure = Literal[
    "t_log_ratio",
    "t_log_ratio_batch_means",
    "hodges_lehmann_wilcoxon",
    "bootstrap_median_ratio",
]
TargetClass = Literal["python_cpu", "native", "gpu_kernel", "llm_serving"]
CalibrationStatus = Literal["calibrated", "uncalibrated"]

_WAIVABLE_GATES: Final = frozenset({"runtime", "same_host"})
_RULE_OUTCOME: Final[Mapping[int, tuple[Outcome, frozenset[str]]]] = {
    1: ("REJECT", frozenset({"correctness", "integrity"})),
    2: ("KEEP", frozenset({"performance"})),
    3: ("REJECT", frozenset({"performance"})),
    5: ("INCONCLUSIVE", frozenset({"insufficient_evidence"})),
}


def _check_relative_path(value: str) -> str:
    """Refuse paths that are absolute, unnormalized or leave their root.

    Paths in the schema are POSIX paths relative to a tree or a run
    directory. Requiring the normalized form keeps one spelling per
    file, so hashes and lock manifests compare equal across machines.
    """
    if value == ".":
        return value
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or any(part in ("", ".", "..") for part in parts)
    ):
        msg = f"{value!r} is not a normalized relative POSIX path"
        raise ValueError(msg)
    return value


# Scalars are strict: JSON numbers, booleans and strings are taken as
# they are, never coerced from another type ("3" is not 3, 1 is not
# True). Containers and datetimes stay lax so JSON arrays and ISO
# strings from ``json.load`` validate.
Str = Annotated[str, Strict()]
Flag = Annotated[bool, Strict()]
RelPath = Annotated[str, Strict(), AfterValidator(_check_relative_path)]
Sha256Hex = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")
]
GitSha = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$"),
]
Name = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
Code = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]*$")
]
EnvName = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
]
Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
Argv = Annotated[tuple[Text, ...], Field(min_length=1)]
Count = Annotated[int, Strict(), Field(ge=0)]
PositiveInt = Annotated[int, Strict(), Field(ge=1)]
Positive = Annotated[float, Strict(), Field(gt=0)]
Seconds = Positive
NonNegative = Annotated[float, Strict(), Field(ge=0)]
Fraction = Annotated[float, Strict(), Field(ge=0, le=1)]
Ratio = Positive
Level = Annotated[float, Strict(), Field(gt=0, lt=1)]


def _sha256_json(payload: object) -> str:
    """Hash ``payload`` in a canonical JSON form.

    Keys are sorted and separators fixed, so the hash depends only on
    content, never on the order keys were written in.
    """
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Model(BaseModel):
    """Base for every schema model: frozen, closed, finite floats."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        use_attribute_docstrings=True,
    )


class MetricSpec(_Model):
    """The one metric a benchmark is judged on.

    ``wall`` and ``cpu`` are times in seconds and ``counter`` is an
    event count, so all three are lower-is-better. Only ``reported``
    metrics choose a direction.

    Raises
    ------
    pydantic.ValidationError
        If the direction or unit contradicts the kind, or ``event`` is
        set for anything but a ``counter`` metric or missing for one.
    """

    kind: MetricKind
    """`wall`, `cpu`, `reported` (``PERFPROOF {"value": ...}`` line on
    stdout) or `counter` (``perf stat``, Linux)."""
    direction: Direction
    """Whether smaller or larger values are better. Orients the ratio so
    that above 1 always means the candidate is faster."""
    unit: Text
    """Unit of `Sample.value`; ``"s"`` for `wall` and `cpu`."""
    event: Text | None = None
    """``perf`` event name for a `counter` metric, such as
    ``"instructions"``; ``None`` for every other kind."""

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if self.kind != "reported" and self.direction != "lower_is_better":
            msg = f"{self.kind} metrics are lower_is_better"
            raise ValueError(msg)
        if self.kind in ("wall", "cpu") and self.unit != "s":
            msg = f"{self.kind} metrics are in seconds, unit must be 's'"
            raise ValueError(msg)
        if (self.kind == "counter") != (self.event is not None):
            msg = "event is required for counter metrics and only for them"
            raise ValueError(msg)
        return self


class OutputCheck(_Model):
    """One declared output compared between the baseline and candidate.

    Raises
    ------
    pydantic.ValidationError
        If a tolerance or ``equal_nan`` is set for a ``bytes`` or
        ``text`` comparison.
    """

    path: RelPath
    """Output file, relative to the tree it is produced in."""
    compare: Literal["bytes", "text", "numbers"]
    """Exact bytes, exact text, or numbers within tolerance (numbers in
    text, or ``.npy`` arrays)."""
    rtol: NonNegative = 0.0
    """Relative tolerance for ``numbers``, dimensionless."""
    atol: NonNegative = 0.0
    """Absolute tolerance for ``numbers``, in the output's own units."""
    equal_nan: Flag = False
    """Whether NaN equals NaN for ``numbers``."""

    @model_validator(mode="after")
    def _check_tolerances(self) -> Self:
        if self.compare != "numbers" and (
            self.rtol or self.atol or self.equal_nan
        ):
            msg = "rtol, atol and equal_nan apply only to numbers"
            raise ValueError(msg)
        return self


class CorrectnessSpec(_Model):
    """How the correctness gate decides the candidate is right.

    At least one check is required: a gate that checks nothing would
    pass every candidate.

    Raises
    ------
    pydantic.ValidationError
        If there is neither a command nor an output, if a command has no
        timeout or a timeout has no command, or if an output path
        repeats.
    """

    command: Argv | None = None
    """Test command run in the candidate tree, as an argv list. Exit
    status 0 passes."""
    cwd: RelPath = "."
    """Working directory of ``command``, relative to the tree."""
    timeout_seconds: Seconds | None = None
    """Time limit for ``command``, in seconds."""
    outputs: tuple[OutputCheck, ...] = ()
    """Declared outputs compared between the two trees."""

    @model_validator(mode="after")
    def _check_has_checks(self) -> Self:
        if self.command is None and not self.outputs:
            msg = "correctness needs a command, outputs, or both"
            raise ValueError(msg)
        if (self.command is None) != (self.timeout_seconds is None):
            msg = "timeout_seconds is required with command and only with it"
            raise ValueError(msg)
        paths = [o.path for o in self.outputs]
        if len(set(paths)) != len(paths):
            msg = f"output paths repeat: {paths}"
            raise ValueError(msg)
        return self


class Budget(_Model):
    """Run budget; the comparison never runs past either limit."""

    max_rounds: PositiveInt
    """Most rounds to run. One round is one baseline and one candidate
    execution."""
    max_seconds: Seconds
    """Most wall-clock time to spend on rounds, in seconds."""


class LookSchedule(_Model):
    """When the sequential procedure looks at the data.

    The first look comes after ``n0`` rounds, then one every ``k``
    rounds, for at most ``max_looks`` looks.
    """

    n0: Annotated[int, Strict(), Field(ge=10)]
    """Rounds before the first look; at least 10, because t intervals on
    fewer rounds are too fragile under heavy tails."""
    k: PositiveInt
    """Rounds between looks."""
    max_looks: PositiveInt
    """Most looks, ``K`` in ``docs/method.md``. Each look's confidence
    is ``1 - alpha / max_looks``."""


class BenchmarkSpec(_Model):
    """A frozen benchmark definition and its content hash.

    Changing any field gives a different ``spec_hash`` and so a new
    spec, which needs a new baseline. When a stored spec is loaded with
    its ``spec_hash``, the hash is recomputed and must match.

    Raises
    ------
    pydantic.ValidationError
        If ``n0`` exceeds ``max_rounds``, a path is declared twice
        across the locked groups, or a supplied ``spec_hash`` does not
        match the content.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    """Version of this format."""
    name: Name
    """Benchmark name; also its file name under
    ``.perfproof/benchmarks/``."""
    command: Argv
    """Benchmark command as an argv list, launched without a shell."""
    cwd: RelPath = "."
    """Working directory, relative to the tree root."""
    env: Mapping[EnvName, Str] = Field(default_factory=dict[str, str])
    """Declared environment variables, set for every execution and
    covered by the procedure gate."""
    metric: MetricSpec
    """The metric the verdict is decided on."""
    correctness: CorrectnessSpec
    """The correctness gate."""
    warmup: Count
    """Discarded executions per version before the first round."""
    budget: Budget
    """Run budget."""
    looks: LookSchedule
    """Look schedule, fixed before the first round."""
    min_effect: NonNegative = 0.03
    """Smallest speedup worth keeping; the bar is ``1 + min_effect``."""
    alpha: Level = 0.05
    """Total error budget, split across looks."""
    timeout_seconds: Seconds
    """Time limit for one benchmark execution, in seconds."""
    harness: Annotated[tuple[RelPath, ...], Field(min_length=1)]
    """Files of the benchmark and its harness, locked. For a `reported`
    metric these include the code that times and prints the value."""
    oracle: tuple[RelPath, ...] = ()
    """Correctness oracle files (tests, expected outputs), locked."""
    inputs: tuple[RelPath, ...] = ()
    """Declared input data, locked."""
    config: tuple[RelPath, ...] = ()
    """Declared config files, locked."""
    allow_runtime_change: Flag = False
    """Opt-in: the interpreter or runtime may differ between trees.
    Waives the `runtime` gate and labels the verdict."""
    allow_cross_host: Flag = False
    """Opt-in: compare across hosts with the alternating fresh-host
    session design. Waives the `same_host` gate and labels the
    verdict."""

    @computed_field
    @property
    def spec_hash(self) -> str:
        """SHA-256 of the spec's canonical JSON, 64 hex digits.

        Covers every field above, nested ones included, independent of
        key order.
        """
        return _sha256_json(
            self.model_dump(mode="json", exclude_computed_fields=True)
        )

    @model_validator(mode="wrap")
    @classmethod
    def _check_stored_hash(
        cls, data: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        claimed: object = None
        if isinstance(data, Mapping) and "spec_hash" in data:
            fields = cast("Mapping[str, object]", data)
            claimed = fields["spec_hash"]
            data = {k: v for k, v in fields.items() if k != "spec_hash"}
        spec = handler(data)
        if claimed is not None and claimed != spec.spec_hash:
            msg = (
                f"stored spec_hash {claimed!r} does not match the content "
                f"hash {spec.spec_hash}"
            )
            raise ValueError(msg)
        return spec

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.looks.n0 > self.budget.max_rounds:
            msg = (
                f"n0 = {self.looks.n0} exceeds max_rounds = "
                f"{self.budget.max_rounds}"
            )
            raise ValueError(msg)
        paths = [*self.harness, *self.oracle, *self.inputs, *self.config]
        if len(set(paths)) != len(paths):
            msg = f"a locked path is declared more than once: {paths}"
            raise ValueError(msg)
        return self


class LockedFile(_Model):
    """One file covered by the lock gate."""

    path: RelPath
    """Path relative to the tree root."""
    role: LockRole
    """Which declared group the file belongs to."""
    sha256: Sha256Hex
    """SHA-256 of the file's bytes."""
    size_bytes: Count
    """File size in bytes."""


class LockManifest(_Model):
    """Hashes of every locked file of one spec, sorted by path.

    Raises
    ------
    pydantic.ValidationError
        If a path appears twice.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    """Version of this format."""
    spec_hash: Sha256Hex
    """`BenchmarkSpec.spec_hash` of the spec these files belong to."""
    files: tuple[LockedFile, ...]
    """Locked files, sorted by path."""

    @field_validator("files", mode="after")
    @classmethod
    def _sort_files(
        cls, files: tuple[LockedFile, ...]
    ) -> tuple[LockedFile, ...]:
        ordered = tuple(sorted(files, key=lambda f: f.path))
        for a, b in itertools.pairwise(ordered):
            if a.path == b.path:
                msg = f"{a.path!r} appears more than once"
                raise ValueError(msg)
        return ordered


class Sample(_Model):
    """One measured execution of one version.

    Warmup executions are discarded and never become samples.
    """

    version: Arm
    """Which tree ran."""
    round: Count
    """Round index, from 0."""
    position: Annotated[int, Strict(), Field(ge=0, le=1)]
    """Order within the round: 0 ran first, 1 second."""
    session: Count = 0
    """Index into `RunRecord.machines` of the host session this ran in;
    always 0 on one machine."""
    value: Ratio
    """Metric value in `MetricSpec.unit`; positive, since ratios are
    taken."""
    wall_seconds: NonNegative
    """Wall-clock time of the whole command, in seconds, recorded for
    every metric."""
    cpu_seconds: NonNegative | None
    """User plus system CPU time from ``wait4``, in seconds; ``None``
    when not available."""
    max_rss_bytes: Count | None
    """Peak resident set size from ``wait4``, in bytes; ``None`` when
    not available."""
    exit_code: Annotated[int, Strict()]
    """Exit status; negative for termination by a signal."""
    command_hash: Sha256Hex
    """SHA-256 of the executed argv, declared environment, working
    directory and resolved executable, as launched."""


class Difference(_Model):
    """One recorded difference between the two arms."""

    item: Text
    """What differs: a file path, environment variable, setting or
    output."""
    baseline: Str | None
    """Value in the baseline; ``None`` when absent there."""
    candidate: Str | None
    """Value in the candidate; ``None`` when absent there."""


class GateResult(_Model):
    """Result of one gate.

    Raises
    ------
    pydantic.ValidationError
        If a failed or waived gate has no ``detail``, or a gate other
        than `runtime` or `same_host` is waived.
    """

    gate: GateName
    """`correctness`, or one of the integrity gates `lock`, `procedure`,
    `runtime` and `same_host`."""
    status: GateStatus
    """`waived` only for `runtime` and `same_host` under the spec's
    opt-ins."""
    detail: Str = ""
    """Why the gate failed or was waived."""
    differences: tuple[Difference, ...] = ()
    """Differences found, first ones first."""
    truncated: Flag = False
    """Whether more differences existed than are listed."""

    @model_validator(mode="after")
    def _check_status(self) -> Self:
        if self.status != "passed" and not self.detail:
            msg = f"a {self.status} gate needs a detail"
            raise ValueError(msg)
        if self.status == "waived" and self.gate not in _WAIVABLE_GATES:
            msg = f"the {self.gate} gate cannot be waived"
            raise ValueError(msg)
        return self


class Interval(_Model):
    """Two-sided confidence interval for a speedup, on the ratio scale.

    Raises
    ------
    pydantic.ValidationError
        If ``lower > upper``.
    """

    lower: Ratio
    """Lower bound ``L``; above 1 means faster."""
    upper: Ratio
    """Upper bound ``U``."""
    confidence: Level
    """Confidence level of this interval, per look."""

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.lower > self.upper:
            msg = f"lower {self.lower} exceeds upper {self.upper}"
            raise ValueError(msg)
        return self


class Diagnostics(_Model):
    """Measurements that qualify a verdict without deciding it.

    ``None`` means not computed.

    Raises
    ------
    pydantic.ValidationError
        If ``metric_disagreement`` is set without ``wall_interval``.
    """

    metric_disagreement: Flag | None = None
    """For a `reported` metric: whether one of the spec-metric and
    wall-clock intervals shows the candidate faster and the other
    excludes that."""
    wall_interval: Interval | None = None
    """Wall-clock interval computed alongside a `reported` metric."""
    lag1_autocorrelation: (
        Annotated[float, Strict(), Field(ge=-1, le=1)] | None
    ) = None
    """Lag-1 autocorrelation of the log ratios; above 0.3 is warned."""
    cv_baseline: NonNegative | None = None
    """Coefficient of variation of the baseline's values."""
    cv_candidate: NonNegative | None = None
    """Coefficient of variation of the candidate's values."""
    sd_log_ratio: NonNegative | None = None
    """Standard deviation of the per-round log ratios."""

    @model_validator(mode="after")
    def _check_disagreement(self) -> Self:
        if self.metric_disagreement is not None and self.wall_interval is None:
            msg = "metric_disagreement needs the wall_interval it came from"
            raise ValueError(msg)
        return self


class Verdict(_Model):
    """The answer to one comparison.

    ``rule``, ``outcome``, ``reason`` and ``gate`` must agree with the
    decision rule in ``docs/method.md``. Gates run before timing, so a
    correctness or integrity verdict carries no speedup estimate,
    interval, wall-clock interval or regression flag.

    Raises
    ------
    pydantic.ValidationError
        If the fields contradict each other as described above, a
        performance verdict lacks its estimate, interval or a look, or a
        calibrated verdict names no target class.
    """

    outcome: Outcome
    """KEEP, REJECT or INCONCLUSIVE."""
    reason: Reason
    """Why: `performance`, `correctness`, `integrity` or
    `insufficient_evidence`."""
    rule: Rule
    """The decision rule that decided."""
    gate: GateName | None = None
    """The failed gate, for rule 1; ``None`` otherwise."""
    estimate: Ratio | None = None
    """Speedup estimate (geometric-mean ratio for the primary
    procedure); above 1 means the candidate is faster."""
    interval: Interval | None = None
    """Interval of the deciding look."""
    rounds: Count
    """Rounds measured."""
    looks: Count
    """Looks taken."""
    procedure: Procedure
    """Interval procedure in force."""
    regression: Flag = False
    """Performance REJECT with ``U < 1``: the candidate is slower."""
    cross_host: Flag = False
    """Decided under the cross-host session design. It rests on the
    assumption that the pool allocates hosts independently of which
    version runs."""
    target_class: TargetClass | None = None
    """Target class judged under; ``None`` when unknown."""
    calibration: CalibrationStatus
    """Whether the procedure is calibrated for ``target_class``."""
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)
    """Diagnostics such as ``metric_disagreement``."""

    @model_validator(mode="after")
    def _check_rule(self) -> Self:
        outcome, reasons = _RULE_OUTCOME[self.rule]
        if self.outcome != outcome or self.reason not in reasons:
            msg = (
                f"rule {self.rule} gives {outcome} with reason in "
                f"{sorted(reasons)}, not {self.outcome} {self.reason}"
            )
            raise ValueError(msg)
        if self.rule == 1:
            self._check_gate_verdict()
        elif self.gate is not None:
            msg = f"only rule 1 names a gate, rule {self.rule} has {self.gate}"
            raise ValueError(msg)
        if self.rule in (2, 3) and (
            self.estimate is None or self.interval is None
        ):
            msg = "a performance verdict needs an estimate and interval"
            raise ValueError(msg)
        if self.interval is not None and self.looks < 1:
            msg = "an interval comes from a look, but looks is 0"
            raise ValueError(msg)
        if self.regression and self.rule != 3:
            msg = "only a performance REJECT (rule 3) flags a regression"
            raise ValueError(msg)
        if self.calibration == "calibrated" and self.target_class is None:
            msg = "a calibrated verdict must name its target class"
            raise ValueError(msg)
        return self

    def _check_gate_verdict(self) -> None:
        """Check that a gate verdict makes no claim about speed."""
        if self.gate is None:
            msg = "rule 1 needs the failed gate"
            raise ValueError(msg)
        expected = "correctness" if self.gate == "correctness" else "integrity"
        if self.reason != expected:
            msg = f"the {self.gate} gate gives reason {expected}"
            raise ValueError(msg)
        if (
            self.estimate is not None
            or self.interval is not None
            or self.diagnostics.wall_interval is not None
        ):
            msg = f"a {self.reason} verdict carries no speedup estimate"
            raise ValueError(msg)


class Hotspot(_Model):
    """One ranked entry of a profile.

    Raises
    ------
    pydantic.ValidationError
        If ``self_fraction`` exceeds ``total_fraction`` or ``line`` is
        given without ``file``.
    """

    function: Text
    """Function or kernel name."""
    file: Str | None
    """Source file, relative to the project root when inside it;
    ``None`` when the profiler gives none."""
    line: PositiveInt | None
    """1-based line in ``file``; ``None`` when unknown."""
    frame_kind: Literal["python", "native", "gpu_kernel"]
    """Kind of frame; frames of different kinds are never merged."""
    is_library: Flag
    """Whether the frame is outside the project's own code."""
    self_fraction: Fraction
    """Share of the profile's total spent in this frame itself."""
    total_fraction: Fraction | None
    """Share including callees; ``None`` if the profiler gives none."""

    @model_validator(mode="after")
    def _check_shares(self) -> Self:
        if (
            self.total_fraction is not None
            and self.self_fraction > self.total_fraction
        ):
            msg = "self_fraction exceeds total_fraction"
            raise ValueError(msg)
        if self.line is not None and self.file is None:
            msg = "line given without file"
            raise ValueError(msg)
        return self


class GpuInfo(_Model):
    """One NVIDIA GPU as ``nvidia-smi`` reports it."""

    name: Text
    """Model name."""
    driver_version: Text
    """Driver version."""
    sm_clock_mhz: Positive
    """Current SM clock, in MHz."""
    memory_clock_mhz: Positive
    """Current memory clock, in MHz."""


class MachineFingerprint(_Model):
    """The machine one session ran on, probed once per session.

    Every probe is optional. A probe that fails leaves its field
    ``None`` and records why in ``missing``; it never fills in a
    plausible value.

    Raises
    ------
    pydantic.ValidationError
        If a ``None`` probe has no reason in ``missing``, or ``missing``
        names a probe that has a value or does not exist.
    """

    captured_at: AwareDatetime
    """When the probes ran, with time zone."""
    host_id: Text | None = None
    """Stable identity of the host, compared by the `same_host` gate."""
    os: Text | None = None
    """Operating system name, such as ``"Darwin"`` or ``"Linux"``."""
    os_version: Text | None = None
    """Operating system version."""
    kernel: Text | None = None
    """Kernel release."""
    arch: Text | None = None
    """Machine architecture, such as ``"arm64"``."""
    cpu_model: Text | None = None
    """CPU model name."""
    physical_cores: PositiveInt | None = None
    """Physical CPU cores."""
    logical_cores: PositiveInt | None = None
    """Logical CPUs."""
    frequency_policy: Text | None = None
    """CPU frequency governor or policy."""
    load_average: tuple[NonNegative, NonNegative, NonNegative] | None = None
    """1, 5 and 15 minute load averages."""
    power_source: Literal["ac", "battery", "ups"] | None = None
    """Power source."""
    battery_percent: Annotated[float, Strict(), Field(ge=0, le=100)] | None = (
        None
    )
    """Battery charge, in percent."""
    thermal_state: Text | None = None
    """Thermal or throttling state as the OS reports it."""
    memory_bytes: PositiveInt | None = None
    """Physical memory, in bytes."""
    python_version: Text | None = None
    """Version of the Python running perfproof."""
    gpus: tuple[GpuInfo, ...] | None = None
    """NVIDIA GPUs; empty when there are none."""
    missing: Mapping[str, Text] = Field(default_factory=dict[str, str])
    """Reason for each probe left ``None``, keyed by field name."""

    @model_validator(mode="after")
    def _check_missing(self) -> Self:
        probes = set(type(self).model_fields) - {"captured_at", "missing"}
        unset = {p for p in probes if getattr(self, p) is None}
        if unset != set(self.missing):
            msg = (
                f"probes without a value {sorted(unset)} must match the "
                f"reasons in missing {sorted(self.missing)}"
            )
            raise ValueError(msg)
        return self


class RunWarning(_Model):
    """Something the report flags that does not change the verdict."""

    code: Code
    """Stable snake_case identifier, such as ``on_battery``."""
    message: Text
    """Human-readable explanation."""


class RunRecord(_Model):
    """Everything about one comparison; written as ``record.json``.

    Raises
    ------
    pydantic.ValidationError
        If the verdict disagrees with the gates (a failed gate must
        decide, and the deciding gate must be one that failed), a gate
        is listed twice, a waiver or cross-host verdict lacks the spec's
        opt-in, rounds ran without a samples path, or ``finished_at``
        precedes ``started_at``.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    """Version of this format."""
    run_id: Name
    """Run identifier; the directory name under ``.perfproof/runs/``."""
    perfproof_version: Text
    """perfproof version that ran the comparison."""
    started_at: AwareDatetime
    """When the comparison started, with time zone."""
    finished_at: AwareDatetime
    """When it finished, with time zone."""
    spec: BenchmarkSpec
    """The spec compared under."""
    baseline_commit: GitSha
    """Git commit of the baseline."""
    order_seed: Count
    """Seed for the in-round order of the two versions."""
    verdict: Verdict
    """The verdict."""
    gates: tuple[GateResult, ...]
    """Gate results, including recorded differences between arms."""
    machines: Annotated[tuple[MachineFingerprint, ...], Field(min_length=1)]
    """Fingerprint per session, indexed by `Sample.session`; one entry
    on one machine."""
    warnings: tuple[RunWarning, ...] = ()
    """Warnings for the report."""
    samples_path: RelPath | None
    """Raw samples file, relative to the run directory; ``None`` when no
    round ran."""
    profile_paths: tuple[RelPath, ...] = ()
    """Profiler outputs, relative to the run directory."""

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.finished_at < self.started_at:
            msg = "finished_at precedes started_at"
            raise ValueError(msg)
        names = [g.gate for g in self.gates]
        if len(set(names)) != len(names):
            msg = f"a gate is listed more than once: {names}"
            raise ValueError(msg)
        status = {g.gate: g.status for g in self.gates}
        failed = {g for g, s in status.items() if s == "failed"}
        v = self.verdict
        if failed and v.gate not in failed:
            msg = f"gates {sorted(failed)} failed, so one must decide"
            raise ValueError(msg)
        if v.gate is not None and v.gate not in failed:
            msg = f"verdict names gate {v.gate}, which did not fail"
            raise ValueError(msg)
        if status.get("runtime") == "waived" and not (
            self.spec.allow_runtime_change
        ):
            msg = "runtime gate waived without allow_runtime_change"
            raise ValueError(msg)
        cross = status.get("same_host") == "waived"
        if cross and not self.spec.allow_cross_host:
            msg = "same_host gate waived without allow_cross_host"
            raise ValueError(msg)
        if v.cross_host != cross:
            msg = "a cross-host verdict goes with a waived same_host gate"
            raise ValueError(msg)
        if v.rounds and self.samples_path is None:
            msg = f"{v.rounds} rounds ran but samples_path is None"
            raise ValueError(msg)
        return self


MODELS: Final[tuple[type[BaseModel], ...]] = (
    MetricSpec,
    OutputCheck,
    CorrectnessSpec,
    Budget,
    LookSchedule,
    BenchmarkSpec,
    LockedFile,
    LockManifest,
    Sample,
    Difference,
    GateResult,
    Interval,
    Diagnostics,
    Verdict,
    Hotspot,
    GpuInfo,
    MachineFingerprint,
    RunWarning,
    RunRecord,
)
"""Every public model, in the order they appear in the JSON Schema."""


def json_schema() -> dict[str, Any]:
    """Return the JSON Schema of every model in `MODELS`.

    The schema describes the serialized form, so computed fields such as
    `BenchmarkSpec.spec_hash` appear in it.

    Returns
    -------
    dict of str to Any
        JSON Schema document whose ``$defs`` has one entry per model.
    """
    _, schema = models_json_schema(
        [(model, "serialization") for model in MODELS],
        title=f"perfproof schema version {SCHEMA_VERSION}",
    )
    return schema


def json_schema_text() -> str:
    """Return `json_schema` as the exact text of the checked-in file.

    Returns
    -------
    str
        Indented JSON with a trailing newline.
    """
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.stdout.write(json_schema_text())
