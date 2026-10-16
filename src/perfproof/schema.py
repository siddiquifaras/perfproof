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

The JSON Schema checks structure: field names, types, enums, numeric
bounds and string patterns. These models check more, and only they are
authoritative. JSON Schema alone does not express:

- relative-path normalization (no ``..``, ``.``, empty or absolute
  parts);
- cross-field rules, such as a verdict's rule, outcome, reason and gate
  agreeing, gate verdicts carrying no estimate, ``missing`` reasons
  matching the ``None`` probes, a record's gates and verdict agreeing,
  or a guardrail's status agreeing with its intervals;
- the check that a stored ``spec_hash`` matches the spec's content;
- the sorting and de-duplication of lock manifests, environment
  variable names and lockfiles.

A document that passes the JSON Schema can still be refused here.
"""

import hashlib
import itertools
import json
import re
import sys
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal, Self, cast, get_args

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    PlainSerializer,
    Strict,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic.json_schema import models_json_schema

__all__ = [
    "DEFAULT_GUARDRAILS",
    "GATE_REASON",
    "MODELS",
    "SCHEMA_VERSION",
    "AbsoluteLimit",
    "AllowanceSource",
    "Arm",
    "BenchmarkSpec",
    "Budget",
    "CalibrationStatus",
    "CommandOutput",
    "CorrectnessSpec",
    "DependencyLock",
    "Diagnostics",
    "Difference",
    "DifferentialCheck",
    "Direction",
    "GateName",
    "GateResult",
    "GateStatus",
    "GpuInfo",
    "GuardrailResult",
    "GuardrailSpec",
    "GuardrailStatus",
    "Hotspot",
    "Interval",
    "LinkedLibrary",
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
    "QuarantinedTest",
    "Reason",
    "ResetSpec",
    "ResolutionSource",
    "Rule",
    "RunRecord",
    "RunWarning",
    "RuntimeKind",
    "Sample",
    "SkippedCalls",
    "TargetClass",
    "TreeEnvironment",
    "UnverifiedLines",
    "Verdict",
    "json_schema",
    "json_schema_text",
]

SCHEMA_VERSION: Final = 3
"""Version of the on-disk format; bumped on any incompatible change.

Version 3 follows the method changes: a one-host look schedule needs
even ``n0`` and ``k``; values carry a resolution, and a verdict the
resolution stop, its widening and the calibration conditions it rests
on; a tree's runtime has a kind and its native build's outside-tree
libraries; a record names its repository and candidate commit;
differential checks declare a numeric tolerance and record their
capture limit. Every version 2 document is refused, whatever its
content, and none is migrated: a stored spec, manifest or record must
be written again under version 3.

Version 2 added the safety checks: the `side_effects` and `coverage`
gates, guardrails, the `guardrail` reason, the reset command, the
opt-ins for remote services and unchecked coverage, and the thin-path
gate status and diagnostic. Version 1 records list five gates, so they
no longer validate.
"""

MetricKind = Literal["wall", "cpu", "reported", "counter"]
Direction = Literal["lower_is_better", "higher_is_better"]
Arm = Literal["baseline", "candidate"]
Outcome = Literal["KEEP", "REJECT", "INCONCLUSIVE"]
Reason = Literal[
    "performance",
    "correctness",
    "integrity",
    "guardrail",
    "insufficient_evidence",
]


def _exact_int(value: object) -> object:
    """Refuse a bool or float where an integer literal is expected.

    Literal fields compare by equality, so without this ``2.0`` and
    ``True`` would pass as ``2`` and ``1``.
    """
    if type(value) is not int:
        msg = f"expected an integer, got {value!r}"
        raise ValueError(msg)
    return value


Rule = Annotated[Literal[1, 2, 3, 5], BeforeValidator(_exact_int)]
"""Decision rule that decided, numbered as in ``docs/method.md``.

Rule 4 (continue) never ends a comparison, so no verdict carries it.
"""
SchemaVersion = Annotated[Literal[3], BeforeValidator(_exact_int)]
GateName = Literal[
    "side_effects",
    "lock",
    "procedure",
    "runtime",
    "same_host",
    "correctness",
    "coverage",
]
GateStatus = Literal["passed", "failed", "waived", "not_available"]
"""``not_available``: the gate could not be checked in this run, because
it is not built yet or the target gives it nothing to check with. It
never counts as passed and blocks KEEP."""
GuardrailStatus = Literal[
    "passed", "breached", "not_established", "unmeasurable", "not_applicable"
]
AllowanceSource = Literal["default", "target_class", "project"]
LockRole = Literal["harness", "oracle", "input", "config"]
Procedure = Literal[
    "t_log_ratio",
    "t_log_ratio_batch_means",
    "hodges_lehmann_wilcoxon",
    "bootstrap_median_ratio",
]
TargetClass = Literal[
    "python_cpu",
    "native",
    "web_service",
    "llm_serving",
    "gpu_kernel",
    "agentic",
]
"""The six release target classes of ``docs/targets.md``."""
CalibrationStatus = Literal["calibrated", "uncalibrated"]
ResolutionSource = Literal["clock", "count", "declared", "printed"]
"""Where a value's resolution came from, per "Resolution" in
``docs/method.md``: the clock's step, measured once per run (`wall`,
`cpu`); one count (`counter`); the spec's declared resolution, or the
place of the last digit printed (`reported`)."""
RuntimeKind = Literal["interpreter", "native_build"]
"""What a tree's benchmark runs as: an interpreter with a version, or a
native executable built in the tree. An unknown runtime has no kind: it
is ``None`` with its reason in ``missing``."""

GATE_REASON: Final[Mapping[GateName, Reason]] = MappingProxyType(
    {
        "side_effects": "integrity",
        "lock": "integrity",
        "procedure": "integrity",
        "runtime": "integrity",
        "same_host": "integrity",
        "correctness": "correctness",
        "coverage": "correctness",
    }
)
"""REJECT reason of each failed gate, per ``docs/method.md``."""

_ALL_GATES: Final[frozenset[str]] = frozenset(get_args(GateName))
_WAIVABLE_GATES: Final = frozenset(
    {"side_effects", "runtime", "same_host", "coverage"}
)
_ABSENT: Final = object()
_RULE_OUTCOMES: Final[Mapping[int, frozenset[tuple[Outcome, Reason]]]] = {
    1: frozenset(
        {
            ("REJECT", "correctness"),
            ("REJECT", "integrity"),
            ("INCONCLUSIVE", "insufficient_evidence"),
        }
    ),
    2: frozenset(
        {
            ("KEEP", "performance"),
            ("REJECT", "guardrail"),
            ("INCONCLUSIVE", "insufficient_evidence"),
        }
    ),
    3: frozenset({("REJECT", "performance")}),
    5: frozenset({("INCONCLUSIVE", "insufficient_evidence")}),
}
_NOT_APPLICABLE: Final[Mapping[RuntimeKind | None, frozenset[str]]] = (
    MappingProxyType(
        {
            "interpreter": frozenset({"linked_libraries"}),
            "native_build": frozenset({"runtime_version"}),
            None: frozenset(),
        }
    )
)
"""`TreeEnvironment` fields that don't apply to each runtime kind."""
_UNSHOWN: Final = frozenset({"not_established", "unmeasurable"})
_MEASURED: Final = frozenset({"passed", "breached", "not_established"})
_REPORTED_ONLY: Final[frozenset[MetricKind]] = frozenset({"reported"})
_OBJECTIVE_KINDS: Final[Mapping[str, frozenset[MetricKind]]] = (
    MappingProxyType(
        {
            "wall_time": frozenset({"wall", "reported"}),
            "cpu_time": frozenset({"cpu", "reported"}),
            "peak_memory": _REPORTED_ONLY,
            "peak_gpu_memory": _REPORTED_ONLY,
            "p99_latency": _REPORTED_ONLY,
            "throughput": _REPORTED_ONLY,
        }
    )
)
_OTHER_KINDS: Final[frozenset[MetricKind]] = frozenset({"reported", "counter"})
_KNOWN_DIRECTION: Final[Mapping[str, Direction]] = MappingProxyType(
    {
        "wall_time": "lower_is_better",
        "cpu_time": "lower_is_better",
        "peak_memory": "lower_is_better",
        "peak_gpu_memory": "lower_is_better",
        "p99_latency": "lower_is_better",
        "throughput": "higher_is_better",
    }
)
"""The one correct direction of each metric name perfproof knows, for
objectives and guardrails alike; a project's own names are free."""


def _check_direction(name: str, direction: Direction, role: str) -> None:
    """Refuse a known metric name paired with the wrong direction.

    A wrong direction inverts what counts as better, so a rise in
    memory or latency would be rewarded as an improvement.
    """
    expected = _KNOWN_DIRECTION.get(name)
    if expected is not None and direction != expected:
        msg = f"{role} {name} is {expected}, not {direction}"
        raise ValueError(msg)


DEFAULT_GUARDRAILS: Final[Mapping[TargetClass | None, tuple[str, ...]]] = (
    MappingProxyType(
        {
            "python_cpu": ("peak_memory",),
            "native": ("peak_memory",),
            "web_service": ("peak_memory", "p99_latency"),
            "llm_serving": ("peak_memory", "peak_gpu_memory", "p99_latency"),
            "gpu_kernel": ("peak_memory", "peak_gpu_memory"),
            "agentic": ("peak_memory", "p99_latency"),
            None: ("peak_memory", "peak_gpu_memory", "p99_latency"),
        }
    )
)
"""Guardrails ``docs/method.md`` applies by default, per target class.

Each must be declared in the spec, applied or not applicable with a
reason, unless it is the primary objective. ``p99_latency`` is a
default only when it isn't the objective, and an agentic app that
serves no requests marks it not applicable. An unknown target class
(``None``) fails closed: every default in the table is required.
Allowances and overrides are not here; they belong to the guardrail
step."""


def _check_relative_path(value: str) -> str:
    """Refuse paths that are absolute, unnormalized or leave their root.

    Paths in the schema are POSIX paths relative to a tree or a run
    directory. Requiring the normalized form keeps one spelling per
    file, so hashes and lock manifests compare equal across machines.
    """
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


def _check_relative_dir(value: str) -> str:
    """Accept a normalized relative directory, or ``"."`` for root."""
    return value if value == "." else _check_relative_path(value)


class _FrozenMap(Mapping[str, str]):
    """Read-only, hashable and picklable mapping of str to str.

    Pydantic's ``frozen`` stops attribute assignment but not writes into
    a dict field, and a write into `BenchmarkSpec.env` would silently
    change the spec's hash. ``MappingProxyType`` would block the write
    but cannot be pickled or deep-copied.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, str] | None = None) -> None:
        self._data = dict(data or {})

    def __getitem__(self, key: str) -> str:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __hash__(self) -> int:
        return hash(frozenset(self._data.items()))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        return self._data == dict(cast("Mapping[object, object]", other))

    def __reduce__(self) -> tuple[type[Self], tuple[dict[str, str]]]:
        return (type(self), (self._data,))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"


def _check_reasons(
    model: BaseModel,
    missing: Mapping[str, str],
    not_applicable: frozenset[str] = frozenset(),
) -> None:
    """Require a reason in ``missing`` for exactly the ``None`` fields.

    A probe that fails leaves ``None`` and says why, so an unknown value
    is never mistaken for an absent one or filled with a default. Fields
    in ``not_applicable`` are ``None`` because they don't apply, which
    is known, so they take no reason.
    """
    fields = set(type(model).model_fields) - {"missing"} - not_applicable
    unset = {f for f in fields if getattr(model, f) is None}
    if unset != set(missing):
        msg = (
            f"fields without a value {sorted(unset)} must match the "
            f"reasons in missing {sorted(missing)}"
        )
        raise ValueError(msg)


# Scalars are strict: JSON numbers, booleans and strings are taken as
# they are, never coerced from another type ("3" is not 3, 1 is not
# True). Containers and datetimes stay lax so JSON arrays and ISO
# strings from ``json.load`` validate.
Str = Annotated[str, Strict()]
Flag = Annotated[bool, Strict()]
RelPath = Annotated[str, Strict(), AfterValidator(_check_relative_path)]
RelDir = Annotated[str, Strict(), AfterValidator(_check_relative_dir)]
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
# The serializers' return types repeat the key and value constraints so
# the exported JSON Schema keeps them.
EnvMap = Annotated[
    Mapping[EnvName, Str],
    AfterValidator(_FrozenMap),
    PlainSerializer(dict, return_type=dict[EnvName, Str]),
]
ReasonMap = Annotated[
    Mapping[Str, Text],
    AfterValidator(_FrozenMap),
    PlainSerializer(dict, return_type=dict[Str, Text]),
]
ValueMap = Annotated[
    Mapping[Text, Str],
    AfterValidator(_FrozenMap),
    PlainSerializer(dict, return_type=dict[Text, Str]),
]


_URL_USERINFO: Final = re.compile(
    r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://(?P<user>[^/?#]*)@"
)
_SCP_USER: Final = re.compile(r"^(?P<user>[^/@]+)@[^/:]+:")
_LOGIN_NAME: Final = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
_SSH_SCHEMES: Final = frozenset({"ssh", "git+ssh"})


def _check_repository(value: str) -> str:
    """Refuse a repository location that may write a credential to disk.

    It fails closed. A URL of any scheme but ``ssh`` and ``git+ssh``
    (``https``, ``git+https``, ``http`` and the rest) has no user part
    at all, since a repository URL has no need of one there and it is
    where a token goes (``https://<token>@host``). An ``ssh`` or
    ``git+ssh`` URL, or scp-like syntax (``u@host:path``), keeps its
    user part only when it is a plain login name such as ``git``:
    lowercase letters, digits, ``_``, ``.`` and ``-``, starting with a
    letter or ``_``, at most 32 characters, with no ``:password``.
    The user part is what comes before ``@`` within the authority,
    which ends at the first ``/``. A stated limit: an SSH user that
    happens to look like a short lowercase token
    (``ghp_x@host:o/r``) is kept.
    """
    url = _URL_USERINFO.match(value)
    if url is not None:
        ssh = url["scheme"].lower() in _SSH_SCHEMES
        allowed = ssh and _LOGIN_NAME.match(url["user"]) is not None
    else:
        scp = _SCP_USER.match(value)
        allowed = scp is None or _LOGIN_NAME.match(scp["user"]) is not None
    if not allowed:
        msg = (
            "the repository location has a user part that may be a "
            "credential; record it without"
        )
        raise ValueError(msg)
    return value


Repository = Annotated[
    str,
    StringConstraints(strict=True, min_length=1),
    AfterValidator(_check_repository),
]


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
        If the direction or unit contradicts the kind, ``event`` is set
        for anything but a ``counter`` metric or missing for one, or
        ``resolution`` is set for anything but a ``reported`` metric.
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
    resolution: Positive | None = None
    """For a `reported` metric, the step ``q`` its values are known to,
    in ``unit``, when the benchmark knows it: the timer it reads, or the
    unit it rounds or truncates to. ``None`` leaves each value's step to
    the place of its last printed digit, with the continuity check of
    ``docs/method.md``. Never set for other kinds, whose step is the
    clock's (`wall`, `cpu`) or one count (`counter`)."""

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
        if self.resolution is not None and self.kind != "reported":
            msg = (
                f"a {self.kind} metric's resolution is measured, not "
                "declared; only a reported metric declares one"
            )
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
    cwd: RelDir = "."
    """Working directory of ``command``, relative to the tree."""
    timeout_seconds: Seconds | None = None
    """Time limit for ``command``, in seconds."""
    outputs: tuple[OutputCheck, ...] = ()
    """Declared outputs compared between the two trees."""
    differential_rtol: NonNegative = 0.0
    """Relative tolerance for floating-point values the differential
    checks compare (captured, replayed and generated calls),
    dimensionless, relative to the baseline's value. ``0.0`` with
    ``differential_atol`` ``0.0`` compares exactly, so reordered float
    arithmetic mismatches."""
    differential_atol: NonNegative = 0.0
    """Absolute tolerance for those floating-point values, in their own
    units."""

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
    rounds, for at most ``max_looks`` looks. On one host rounds come in
    order pairs, so ``n0`` and ``k`` are even and every scheduled look
    falls on complete pairs; `BenchmarkSpec` refuses an odd one unless
    the spec opts in to the cross-host design, whose observations are
    session pairs.
    """

    n0: Annotated[int, Strict(), Field(ge=10)]
    """Rounds before the first look; at least 10, because t intervals on
    fewer rounds are too fragile under heavy tails. Even on one host."""
    k: PositiveInt
    """Rounds between looks. Even on one host."""
    max_looks: PositiveInt
    """Most looks, ``K`` in ``docs/method.md``. Each look's two-sided
    interval is at confidence ``1 - alpha / (2 K)``: a Bonferroni split
    over the looks designed to halve the error ``alpha`` allows."""


class ResetSpec(_Model):
    """A command run before every execution to restore external state.

    Used so a database, for example, starts each round in the same
    state. A reset that fails stops the run.
    """

    command: Argv
    """Reset command as an argv list, launched without a shell. Exit
    status 0 succeeds."""
    cwd: RelDir = "."
    """Working directory of ``command``, relative to the tree."""
    timeout_seconds: Seconds
    """Time limit for one reset, in seconds."""


class AbsoluteLimit(_Model):
    """A limit on a guardrail's own value in the candidate.

    For a lower-is-better guardrail the limit is a ceiling, such as a
    container memory limit or a latency SLO; for a higher-is-better one
    it is a floor. It is checked on the candidate's measurements, not
    on a ratio.
    """

    value: Positive
    """The limit, in the guardrail's `GuardrailSpec.unit`."""
    label: Text
    """What the limit is, such as ``"container memory limit"``."""


class GuardrailSpec(_Model):
    """One metric that must not get worse while the primary improves.

    Fixed with the spec, before any round, so an allowance can never be
    chosen after seeing the data. An applicable guardrail checks a
    relative allowance, absolute limits, or both; one marked not
    applicable checks nothing and says why.

    Raises
    ------
    pydantic.ValidationError
        If a guardrail marked not applicable has no reason or has an
        allowance or limits, an applicable one has neither,
        ``allowance`` and ``allowance_source`` are not set together, or
        a known metric name has the wrong direction (see
        `BenchmarkSpec.objective`).
    """

    metric: Code
    """Name of the guarded metric, such as ``peak_memory`` (max RSS of
    the whole process tree), ``peak_gpu_memory`` or ``p99_latency``.
    Shares a namespace with `BenchmarkSpec.objective`."""
    direction: Direction
    """Whether smaller or larger values are better; the worsening ratio
    is oriented so that above 1 means the candidate is worse."""
    unit: Text
    """Unit of the guarded metric's values and of its limits."""
    applicable: Flag = True
    """Whether the guardrail applies to this workload."""
    reason: Str = ""
    """Why the guardrail is not applicable, or why the project applied
    or overrode it; required when not applicable."""
    allowance: NonNegative | None = None
    """Largest relative worsening refused only beyond it, as a fraction
    (``0.10`` is 10%): a refusal threshold, not a budget to spend. The
    guardrail's bar is ``1 + allowance``. ``None`` when only absolute
    limits are checked."""
    allowance_source: AllowanceSource | None = None
    """Where ``allowance`` came from: the provisional default, the
    target class, or the project."""
    limits: tuple[AbsoluteLimit, ...] = ()
    """Absolute limits on the candidate's own value."""

    @model_validator(mode="after")
    def _check_checks(self) -> Self:
        _check_direction(self.metric, self.direction, "guardrail")
        if (self.allowance is None) != (self.allowance_source is None):
            msg = "allowance and allowance_source are set together"
            raise ValueError(msg)
        if not self.applicable:
            if not self.reason:
                msg = f"guardrail {self.metric} is not applicable: say why"
                raise ValueError(msg)
            if self.allowance is not None or self.limits:
                msg = (
                    f"guardrail {self.metric} is not applicable, so it has "
                    "no allowance or limits"
                )
                raise ValueError(msg)
        elif self.allowance is None and not self.limits:
            msg = (
                f"guardrail {self.metric} applies, so it needs an allowance, "
                "limits, or both"
            )
            raise ValueError(msg)
        return self


class BenchmarkSpec(_Model):
    """A frozen benchmark definition and its content hash.

    ``name`` is how people refer to a benchmark; ``spec_hash`` is the
    identity of one exact version of it. Two specs with the same name
    are never assumed equal. Runs and lock manifests store the hash.

    Changing any field gives a different ``spec_hash`` and so a new
    spec, which needs a new baseline. When a stored spec is loaded with
    its ``spec_hash``, the hash is recomputed and must match.

    Raises
    ------
    pydantic.ValidationError
        If ``n0`` exceeds ``max_rounds``, ``n0`` or ``k`` is odd without
        the cross-host opt-in, a path is declared twice
        across the locked groups, a guardrail metric is declared twice
        or is the primary objective, guardrails are declared without an
        objective, the metric kind cannot measure the objective or its
        direction is not the objective's, or a supplied ``spec_hash``
        does not match the content.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION
    """Version of this format."""
    name: Name
    """Stable, human-facing benchmark identifier. Every version of a
    benchmark shares it, so it never identifies a spec on its own;
    ``spec_hash`` does."""
    command: Argv
    """Benchmark command as an argv list, launched without a shell."""
    cwd: RelDir = "."
    """Working directory, relative to the tree root."""
    env: EnvMap = Field(default_factory=_FrozenMap)
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
    """Error the promise allows; each look's interval is at confidence
    ``1 - alpha / (2 K)`` for ``K`` looks."""
    timeout_seconds: Seconds
    """Time limit for one benchmark execution, in seconds."""
    harness: Annotated[tuple[RelPath, ...], Field(min_length=1)]
    """Files of the benchmark and its harness, locked. For a `reported`
    metric these include the code that times and prints the value. The
    order of this and the other path lists is part of ``spec_hash``."""
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
    allow_remote_services: Flag = False
    """Opt-in: the benchmark may reach a service address whose host is
    not local. Waives the `side_effects` gate and labels the verdict."""
    allow_unchecked_coverage: Flag = False
    """Opt-in: for a target where perfproof cannot measure coverage, a
    KEEP without the `coverage` gate. Waives that gate and labels the
    verdict "coverage not checked"."""
    reset: ResetSpec | None = None
    """Command run before every execution, warmup included; ``None``
    when the benchmark needs no reset."""
    objective: Code | None = None
    """Name of the primary objective that ``metric`` measures, such as
    ``wall_time``, ``p99_latency`` or ``throughput``, in the namespace
    of `GuardrailSpec.metric`. Required when ``guardrails`` is not
    empty, so that no metric is both the primary and a guardrail.

    The metric's kind must be able to measure it, and its direction
    must be the name's one correct direction, or naming it would exempt
    a default guardrail that nothing checks, or reward the objective
    getting worse:

    ===================  ==================  ================
    objective            metric kinds        direction
    ===================  ==================  ================
    ``wall_time``        wall, reported      lower_is_better
    ``cpu_time``         cpu, reported       lower_is_better
    ``peak_memory``      reported            lower_is_better
    ``peak_gpu_memory``  reported            lower_is_better
    ``p99_latency``      reported            lower_is_better
    ``throughput``       reported            higher_is_better
    any other name       reported, counter   either
    ===================  ==================  ================

    Any other name is a counter event or the project's own objective.
    So a `wall` metric's objective is ``wall_time`` and a `cpu`
    metric's is ``cpu_time``. The same directions bind
    `GuardrailSpec`."""
    guardrails: tuple[GuardrailSpec, ...] = ()
    """Guardrails, each applied or marked not applicable, one per
    metric. Their order is part of ``spec_hash``."""

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
        claimed: object = _ABSENT
        if isinstance(data, Mapping) and "spec_hash" in data:
            fields = cast("Mapping[str, object]", data)
            claimed = fields["spec_hash"]
            data = {k: v for k, v in fields.items() if k != "spec_hash"}
        spec = handler(data)
        if claimed is not _ABSENT and claimed != spec.spec_hash:
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
        odd = [
            f"{name} = {value}"
            for name, value in (("n0", self.looks.n0), ("k", self.looks.k))
            if value % 2
        ]
        if odd and not self.allow_cross_host:
            msg = (
                f"on one host rounds come in order pairs, so n0 and k are "
                f"even, not {', '.join(odd)}"
            )
            raise ValueError(msg)
        paths = [*self.harness, *self.oracle, *self.inputs, *self.config]
        if len(set(paths)) != len(paths):
            msg = f"a locked path is declared more than once: {paths}"
            raise ValueError(msg)
        metrics = [g.metric for g in self.guardrails]
        if len(set(metrics)) != len(metrics):
            msg = f"a guardrail metric is declared more than once: {metrics}"
            raise ValueError(msg)
        if metrics and self.objective is None:
            msg = "guardrails need the primary objective named"
            raise ValueError(msg)
        if self.objective is not None:
            kinds = _OBJECTIVE_KINDS.get(self.objective, _OTHER_KINDS)
            if self.metric.kind not in kinds:
                msg = (
                    f"a {self.metric.kind} metric cannot measure the "
                    f"objective {self.objective}; it needs one of "
                    f"{sorted(kinds)}"
                )
                raise ValueError(msg)
            _check_direction(
                self.objective, self.metric.direction, "the objective"
            )
        if self.objective in metrics:
            msg = (
                f"{self.objective} is the primary objective, so it cannot "
                "also be a guardrail"
            )
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

    schema_version: SchemaVersion = SCHEMA_VERSION
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

    Raises
    ------
    pydantic.ValidationError
        If ``resolution`` and ``resolution_source`` are not set
        together.
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
    resolution: Positive | None = None
    """The step ``q`` that ``value`` is known to within, in
    `MetricSpec.unit`; it widens the interval as ``docs/method.md``
    sets out under "Resolution". ``None`` when not recorded, which
    leaves the widening unknown."""
    resolution_source: ResolutionSource | None = None
    """Where ``resolution`` came from: ``clock``, ``count``,
    ``declared`` or ``printed``; set with it."""
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

    @model_validator(mode="after")
    def _check_resolution(self) -> Self:
        if (self.resolution is None) != (self.resolution_source is None):
            msg = "resolution and resolution_source are set together"
            raise ValueError(msg)
        return self


class Difference(_Model):
    """One recorded difference between the two arms.

    Raises
    ------
    pydantic.ValidationError
        If both sides are ``None``.
    """

    item: Text
    """What differs: a file path, environment variable, setting or
    output."""
    baseline: Str | None
    """Value in the baseline; ``None`` when absent there."""
    candidate: Str | None
    """Value in the candidate; ``None`` when absent there."""

    @model_validator(mode="after")
    def _check_sides(self) -> Self:
        if self.baseline is None and self.candidate is None:
            msg = f"{self.item!r} is absent in both arms, so not a difference"
            raise ValueError(msg)
        return self


class QuarantinedTest(_Model):
    """A test whose result changed across the baseline runs.

    It is flaky, so it is not used to judge the candidate. Lines only
    quarantined tests ran still count as unverified.

    Raises
    ------
    pydantic.ValidationError
        If every baseline run gave the same result, which is not flaky.
    """

    test_id: Text
    """The test's identifier as the test runner names it, such as a
    pytest node id."""
    baseline_results: Annotated[tuple[Code, ...], Field(min_length=2)]
    """The test's result in each baseline run, in run order, such as
    ``passed`` or ``failed``."""

    @model_validator(mode="after")
    def _check_flaky(self) -> Self:
        if len(set(self.baseline_results)) < 2:
            msg = (
                f"{self.test_id!r} gave {self.baseline_results[0]} in every "
                "baseline run, so it is not flaky"
            )
            raise ValueError(msg)
        return self


class SkippedCalls(_Model):
    """Calls to one function a differential check could not use."""

    stage: Literal["capture", "replay"]
    """``capture``: a call whose arguments or result could not be
    captured faithfully (open files, sockets, live objects).
    ``replay``: a captured call that could not be replayed."""
    reason: Text
    """Why, such as ``"argument 0 is an open file"``."""
    count: PositiveInt
    """How many calls were skipped for this reason."""


class DifferentialCheck(_Model):
    """Old and new versions of one changed function on the same inputs.

    Real calls are captured while the baseline benchmark runs and
    replayed against the candidate in a separate process; generated
    inputs are run through both versions where type hints allow. Every
    call that was seen but not replayed is accounted for in
    ``skipped``.

    Raises
    ------
    pydantic.ValidationError
        If the replay-stage skips do not account for exactly the
        captured calls that were not replayed, a stage and reason
        repeat, more calls mismatched than were run, mismatches and
        ``differences`` are not present together, or more calls were
        captured than ``capture_limit`` allows.
    """

    function: Text
    """The changed function, as ``module:qualified.name``."""
    captured: Count
    """Real calls captured during the baseline benchmark. When it
    equals ``capture_limit`` it is the cap, not a count of every
    call."""
    capture_limit: PositiveInt | None = None
    """Most real calls captured for this function; ``None`` when no
    limit applied. Calls past it are not captured, so a report labels
    ``captured`` at the limit as a bound."""
    replayed: Count
    """Captured calls replayed against the candidate."""
    generated: Count = 0
    """Generated inputs run through both versions."""
    skipped: tuple[SkippedCalls, ...] = ()
    """Calls not captured, or captured but not replayed, by reason."""
    mismatches: Count
    """Replayed or generated calls whose results differed."""
    differences: tuple[Difference, ...] = ()
    """The first differences, first ones first."""

    @model_validator(mode="after")
    def _check_counts(self) -> Self:
        keys = [(s.stage, s.reason) for s in self.skipped]
        if len(set(keys)) != len(keys):
            msg = f"a skip stage and reason repeat: {keys}"
            raise ValueError(msg)
        not_replayed = sum(
            s.count for s in self.skipped if s.stage == "replay"
        )
        if self.replayed + not_replayed != self.captured:
            msg = (
                f"{self.captured} captured calls must be {self.replayed} "
                f"replayed plus {not_replayed} skipped at replay"
            )
            raise ValueError(msg)
        if self.mismatches > self.replayed + self.generated:
            msg = (
                f"{self.mismatches} mismatches out of {self.replayed} "
                f"replayed and {self.generated} generated calls"
            )
            raise ValueError(msg)
        if (self.mismatches > 0) != bool(self.differences):
            msg = "mismatches and their first differences go together"
            raise ValueError(msg)
        if self.capture_limit is not None and self.captured > (
            self.capture_limit
        ):
            msg = (
                f"{self.captured} calls captured past the capture limit of "
                f"{self.capture_limit}"
            )
            raise ValueError(msg)
        return self


class UnverifiedLines(_Model):
    """Changed executable lines of one file that no check ran.

    Raises
    ------
    pydantic.ValidationError
        If a line repeats.
    """

    path: RelPath
    """Source file, relative to the candidate tree."""
    lines: Annotated[tuple[PositiveInt, ...], Field(min_length=1)]
    """1-based line numbers in the candidate, sorted."""

    @field_validator("lines", mode="after")
    @classmethod
    def _sort_lines(cls, lines: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(lines)) != len(lines):
            msg = f"line numbers repeat: {lines}"
            raise ValueError(msg)
        return tuple(sorted(lines))


class CommandOutput(_Model):
    """What a command that failed a gate printed, such as a reset."""

    command: Argv
    """The argv that ran."""
    exit_code: Annotated[int, Strict()]
    """Exit status; negative for termination by a signal."""
    timed_out: Flag
    """Whether perfproof stopped it at its time limit."""
    stdout: Str
    """Standard output, or its tail when ``truncated``."""
    stderr: Str
    """Standard error, or its tail when ``truncated``."""
    truncated: Flag = False
    """Whether either stream was cut to its tail."""


class GateResult(_Model):
    """Result of one gate.

    Raises
    ------
    pydantic.ValidationError
        If a gate that did not pass has no ``detail``; a gate other than
        `side_effects`, `runtime`, `same_host` or `coverage` is waived;
        ``quarantined`` or ``differential`` is set on a gate other than
        `correctness`, or ``unverified`` on one other than `coverage`;
        unverified lines, a differential mismatch or ``output`` come
        with a gate that did not fail; or a test, function or path
        repeats.
    """

    gate: GateName
    """`side_effects`, `lock`, `procedure`, `runtime` or `same_host`
    (integrity), or `correctness` or `coverage`."""
    status: GateStatus
    """`waived` only under the spec's opt-ins; `not_available` when the
    gate could not be checked in this run."""
    detail: Str = ""
    """Why the gate failed, was waived, or is not available."""
    differences: tuple[Difference, ...] = ()
    """Differences found, first ones first."""
    truncated: Flag = False
    """Whether more differences existed than are listed."""
    quarantined: tuple[QuarantinedTest, ...] = ()
    """`correctness` only: flaky tests left out of the judgement."""
    differential: tuple[DifferentialCheck, ...] = ()
    """`correctness` only: differential checks, one per changed
    function."""
    unverified: tuple[UnverifiedLines, ...] = ()
    """`coverage` only: changed lines no check ran, by file."""
    output: CommandOutput | None = None
    """For a failed gate, the output of the command whose failure
    failed it, such as a reset; ``None`` otherwise."""

    @model_validator(mode="after")
    def _check_status(self) -> Self:
        if self.status != "passed" and not self.detail:
            msg = f"a {self.status} gate needs a detail"
            raise ValueError(msg)
        if self.status == "waived" and self.gate not in _WAIVABLE_GATES:
            msg = f"the {self.gate} gate cannot be waived"
            raise ValueError(msg)
        if self.gate != "correctness" and (
            self.quarantined or self.differential
        ):
            msg = "quarantined and differential belong to the correctness gate"
            raise ValueError(msg)
        if self.gate != "coverage" and self.unverified:
            msg = "unverified lines belong to the coverage gate"
            raise ValueError(msg)
        self._check_failure_evidence()
        self._check_unique()
        return self

    def _check_failure_evidence(self) -> None:
        """Check evidence of a failure only comes with a failed gate."""
        if self.status == "failed":
            return
        if self.unverified:
            msg = "unverified lines fail the coverage gate"
            raise ValueError(msg)
        if any(d.mismatches for d in self.differential):
            msg = "a differential mismatch fails the correctness gate"
            raise ValueError(msg)
        if self.output is not None:
            msg = f"output is recorded for a failed gate, not a {self.status}"
            raise ValueError(msg)

    def _check_unique(self) -> None:
        """Refuse a test, function or file listed twice."""
        for name, keys in (
            ("quarantined test", [q.test_id for q in self.quarantined]),
            ("differential function", [d.function for d in self.differential]),
            ("unverified path", [u.path for u in self.unverified]),
        ):
            if len(set(keys)) != len(keys):
                msg = f"a {name} is listed more than once: {keys}"
                raise ValueError(msg)


class Interval(_Model):
    """Two-sided confidence interval with positive bounds.

    On the ratio scale for a speedup (above 1 means faster) and for a
    guardrail's worsening ratio (above 1 means worse); in the
    guardrail's unit for a candidate's own value.

    Raises
    ------
    pydantic.ValidationError
        If ``lower > upper``.
    """

    lower: Ratio
    """Lower bound ``L``."""
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

    procedure_uncalibrated: Flag
    """Required, with no default, so no writer gets a calibrated
    verdict by leaving it out. Whether the interval procedure had not
    yet passed calibration
    when this verdict was given (the thin path before the M1 exit).
    Such a verdict is INCONCLUSIVE whatever its interval, or a gate
    REJECT; never KEEP or a performance or guardrail REJECT."""
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
    coarse_resolution: Flag | None = None
    """Whether the resolution stop of ``docs/method.md`` ended the
    comparison: a value no larger than its own step, or a `reported`
    metric with no declared resolution that failed the continuity
    check. ``True`` only on a rule 1 verdict: the INCONCLUSIVE the stop
    gives, or a gate REJECT decided after it."""
    resolution_widening: NonNegative | None = None
    """How far the resolution moved both bounds of the deciding look's
    interval outward, on the log scale: the mean ``w`` over its rounds
    of ``e_i = -log(1 - q_b / b_i) - log(1 - q_c / c_i)``, so ``L`` was
    multiplied by ``exp(-w)`` and ``U`` by ``exp(w)``. Dimensionless."""

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
    interval, wall-clock interval or regression flag. The resolution
    stop, checked after the gates of rule 1 and before rules 2 and 3,
    is the rule 1 INCONCLUSIVE: it names no gate, comes at a look, has
    the ``coarse_resolution`` diagnostic, and like a gate verdict
    carries no estimate, interval or wall-clock interval. Rule 2 (the
    interval clears the bar) gives KEEP, a guardrail REJECT, which
    keeps its estimate and interval to show what the speed cost, or
    INCONCLUSIVE when compliance couldn't be established.

    Raises
    ------
    pydantic.ValidationError
        If the fields contradict each other as described above, a rule
        2 or 3 verdict lacks its estimate or interval, an interval
        comes without a look on at least 2 rounds, a calibrated verdict
        names no target class or no calibration conditions, an
        uncalibrated one names conditions, or a verdict under an
        uncalibrated procedure is anything but a rule 1 verdict or a
        rule 5 INCONCLUSIVE.
    """

    outcome: Outcome
    """KEEP, REJECT or INCONCLUSIVE."""
    reason: Reason
    """Why: `performance`, `correctness`, `integrity`, `guardrail` or
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
    session_pairs: Count | None = None
    """Consecutive baseline and candidate session pairs measured, the
    observations of the cross-host design; ``None`` on one host."""
    target_class: TargetClass | None = None
    """Target class judged under; ``None`` when unknown, in which case
    every default guardrail of `DEFAULT_GUARDRAILS` must be declared."""
    calibration: CalibrationStatus
    """Whether the procedure is calibrated for ``target_class``."""
    calibration_conditions: tuple[Text, ...] = ()
    """The conditions ``target_class`` was calibrated under, as the
    calibration results in ``docs/method.md`` name them (noise
    scenarios, host modes, trace sets), so the report can say the
    verdict rests on those and not on calibration in general. Not
    empty exactly when ``calibration`` is ``calibrated``."""
    diagnostics: Diagnostics
    """Diagnostics such as ``procedure_uncalibrated``; required, since
    it holds whether the procedure was calibrated."""

    @model_validator(mode="after")
    def _check_rule(self) -> Self:
        allowed = _RULE_OUTCOMES[self.rule]
        if (self.outcome, self.reason) not in allowed:
            msg = (
                f"rule {self.rule} gives one of {sorted(allowed)}, not "
                f"{self.outcome} {self.reason}"
            )
            raise ValueError(msg)
        if self.rule == 1 and self.outcome == "INCONCLUSIVE":
            self._check_resolution_stop()
        elif self.rule == 1:
            self._check_gate_verdict()
        elif self.gate is not None:
            msg = f"only rule 1 names a gate, rule {self.rule} has {self.gate}"
            raise ValueError(msg)
        if self.diagnostics.coarse_resolution and self.rule != 1:
            msg = (
                "coarse_resolution ends the comparison under rule 1, so a "
                f"rule {self.rule} verdict cannot have it"
            )
            raise ValueError(msg)
        if self.rule in (2, 3) and (
            self.estimate is None or self.interval is None
        ):
            msg = f"a rule {self.rule} verdict needs an estimate and interval"
            raise ValueError(msg)
        if self.diagnostics.procedure_uncalibrated and self.rule not in (1, 5):
            msg = (
                "under an uncalibrated procedure only a rule 1 verdict or a "
                f"rule 5 INCONCLUSIVE is given, not rule {self.rule}"
            )
            raise ValueError(msg)
        if (
            self.diagnostics.procedure_uncalibrated
            and self.calibration == "calibrated"
        ):
            msg = "an uncalibrated procedure is not calibrated for a class"
            raise ValueError(msg)
        if self.interval is not None and (self.looks < 1 or self.rounds < 2):
            msg = (
                "an interval comes from a look on at least 2 rounds, not "
                f"{self.looks} looks on {self.rounds} rounds"
            )
            raise ValueError(msg)
        below_one = self.interval is not None and self.interval.upper < 1
        if self.regression != (self.rule == 3 and below_one):
            msg = (
                "regression is set exactly for a performance REJECT (rule "
                "3) whose upper bound is below 1"
            )
            raise ValueError(msg)
        if self.cross_host != (self.session_pairs is not None):
            msg = "session_pairs is set for cross-host verdicts and only them"
            raise ValueError(msg)
        if self.calibration == "calibrated" and self.target_class is None:
            msg = "a calibrated verdict must name its target class"
            raise ValueError(msg)
        if (self.calibration == "calibrated") != bool(
            self.calibration_conditions
        ):
            msg = (
                "a calibrated verdict names the conditions it was "
                "calibrated under, and only a calibrated one does"
            )
            raise ValueError(msg)
        return self

    def _check_resolution_stop(self) -> None:
        """Check a rule 1 INCONCLUSIVE is the resolution stop.

        It is decided at a look, by a value's resolution rather than by
        a gate, so it names no gate and has ``coarse_resolution``. The
        measurement can't resolve the change, so like a gate verdict it
        carries no speedup estimate, interval or wall-clock interval.
        """
        if self.gate is not None:
            msg = (
                "a rule 1 INCONCLUSIVE is the resolution stop, which names "
                f"no gate, not {self.gate}"
            )
            raise ValueError(msg)
        if not self.diagnostics.coarse_resolution:
            msg = (
                "a rule 1 INCONCLUSIVE needs the coarse_resolution diagnostic"
            )
            raise ValueError(msg)
        if self.looks < 1:
            msg = (
                "the resolution stop is decided at a look, but none was taken"
            )
            raise ValueError(msg)
        for name, value in (
            ("estimate", self.estimate),
            ("interval", self.interval),
            ("wall_interval", self.diagnostics.wall_interval),
        ):
            if value is not None:
                msg = (
                    "the resolution stop carries no speedup claim, so no "
                    f"{name}"
                )
                raise ValueError(msg)

    def _check_gate_verdict(self) -> None:
        """Check that a gate verdict makes no claim about speed."""
        if self.gate is None:
            msg = "rule 1 needs the failed gate"
            raise ValueError(msg)
        expected = GATE_REASON[self.gate]
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


class GuardrailResult(_Model):
    """How one guardrail of the spec ended a comparison.

    The status must agree with the intervals against the guardrail's
    allowance and limits; `RunRecord` checks that, since the spec holds
    them. Here only what needs no spec is checked.

    Raises
    ------
    pydantic.ValidationError
        If an `unmeasurable` or `not_applicable` guardrail has no reason
        or carries a measurement, ``estimate`` and ``worsening`` are not
        set together, or a `passed`, `breached` or `not_established`
        guardrail has no interval. A guardrail with no interval at all
        is `unmeasurable`, with its reason.
    """

    metric: Code
    """`GuardrailSpec.metric` of the guardrail."""
    status: GuardrailStatus
    """`passed`, `breached`, `not_established`, `unmeasurable` or
    `not_applicable`, as defined in ``docs/method.md``."""
    estimate: Ratio | None = None
    """Point estimate of the worsening ratio; above 1 means the
    candidate is worse. Reported for every measured guardrail, so a
    regression within its allowance is visible."""
    worsening: Interval | None = None
    """Interval on the worsening ratio, at confidence
    ``1 - alpha / (2 K G)``; checked against ``1 + allowance``."""
    candidate: Interval | None = None
    """Interval on the candidate's own value, in the guardrail's unit,
    at the same confidence; checked against the absolute limits."""
    reason: Str = ""
    """Why the guardrail is `unmeasurable` or `not_applicable`;
    required for those two."""

    @model_validator(mode="after")
    def _check_status(self) -> Self:
        measured = (
            self.estimate is not None
            or self.worsening is not None
            or self.candidate is not None
        )
        if self.status in ("unmeasurable", "not_applicable"):
            if not self.reason:
                msg = f"guardrail {self.metric} is {self.status}: say why"
                raise ValueError(msg)
            if measured:
                msg = (
                    f"guardrail {self.metric} is {self.status}, so it "
                    "carries no measurement"
                )
                raise ValueError(msg)
        if (self.estimate is None) != (self.worsening is None):
            msg = "estimate and worsening are set together"
            raise ValueError(msg)
        has_interval = self.worsening is not None or self.candidate is not None
        if self.status in _MEASURED and not has_interval:
            msg = f"guardrail {self.metric} is {self.status}: needs intervals"
            raise ValueError(msg)
        return self


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
    file: Text | None
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
    """One NVIDIA GPU as ``nvidia-smi`` reports it.

    A clock ``nvidia-smi`` cannot read (``[N/A]``) is ``None`` with a
    reason in ``missing``, so it never discards the rest of the GPU and
    is never recorded as 0.

    Raises
    ------
    pydantic.ValidationError
        If a ``None`` clock has no reason in ``missing``, or ``missing``
        names a field that has a value or does not exist.
    """

    name: Text
    """Model name."""
    driver_version: Text
    """Driver version."""
    sm_clock_mhz: Positive | None = None
    """Current SM clock, in MHz."""
    memory_clock_mhz: Positive | None = None
    """Current memory clock, in MHz."""
    missing: ReasonMap = Field(default_factory=_FrozenMap)
    """Reason for each clock left ``None``, keyed by field name."""

    @model_validator(mode="after")
    def _check_missing(self) -> Self:
        _check_reasons(self, self.missing)
        return self


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
    missing: ReasonMap = Field(default_factory=_FrozenMap)
    """Reason for each probe left ``None``, keyed by field name."""

    @model_validator(mode="after")
    def _check_missing(self) -> Self:
        _check_reasons(self, self.missing)
        return self


class RunWarning(_Model):
    """Something the report flags that does not change the verdict."""

    code: Code
    """Stable snake_case identifier, such as ``on_battery``."""
    message: Text
    """Human-readable explanation."""


class DependencyLock(_Model):
    """Hash of one dependency lockfile in a tree, e.g. ``uv.lock``."""

    path: RelPath
    """Lockfile path, relative to the tree root."""
    sha256: Sha256Hex
    """SHA-256 of the file's bytes."""


class LinkedLibrary(_Model):
    """A library from outside the tree that a native build loads.

    A native executable built in the tree is a build of the code under
    test, but the libraries it links from outside the tree are part of
    the runtime, which the `runtime` gate compares between the arms.

    Raises
    ------
    pydantic.ValidationError
        If ``path`` and ``sha256`` do not match ``source``: a ``file``
        has both, a ``dyld_shared_cache`` library only a path, and a
        ``weak_absent`` one neither.
    """

    name: Text
    """The library as the executable names it: its install name or
    soname."""
    source: Literal["file", "dyld_shared_cache", "weak_absent"]
    """``file``: a file on disk, hashed. ``dyld_shared_cache``: a macOS
    system library that exists only in the dyld shared cache, so there
    is no file to hash. ``weak_absent``: a weak link the loader found
    no library for."""
    path: Text | None = None
    """Resolved path, with symlinks followed; ``None`` for
    ``weak_absent``."""
    sha256: Sha256Hex | None = None
    """SHA-256 of the file's bytes; ``None`` unless ``source`` is
    ``file``."""

    @model_validator(mode="after")
    def _check_source(self) -> Self:
        has_path = self.source != "weak_absent"
        has_hash = self.source == "file"
        for field, wanted in (("path", has_path), ("sha256", has_hash)):
            if (getattr(self, field) is not None) != wanted:
                need = "needs a" if wanted else "takes no"
                msg = f"a {self.source} library {need} {field}"
                raise ValueError(msg)
        return self


class TreeEnvironment(_Model):
    """What one version's tree ran with, recorded for the reader.

    Nothing here is a gate by itself. The `runtime` gate compares the
    runtime between the arms unless the spec allows runtime changes: an
    interpreter by ``runtime_executable`` and ``runtime_version``, a
    native build by the libraries it links outside the tree. The
    `procedure` gate covers only the spec's declared environment. Every
    other difference between the arms is recorded and reported as a
    warning.

    ``runtime_kind`` says which fields apply. An interpreter has an
    executable and a version, and no linked libraries. A native build
    has an executable and no version, since it has no interpreter. An
    unknown runtime (``runtime_kind`` ``None``) has its reason in
    ``missing`` and no version. A field that doesn't apply is ``None``
    and takes no reason in ``missing``.

    Raises
    ------
    pydantic.ValidationError
        If a ``None`` field that applies has no reason in ``missing``,
        ``missing`` names a field that has a value or doesn't apply, a
        field is set that doesn't apply to ``runtime_kind``, a known
        runtime has no executable or an interpreter no version; if
        ``env_values`` names a variable not in ``env_names``; or if a
        name, library or lockfile path repeats.
    """

    version: Arm
    """Which tree."""
    session: Count = 0
    """Index into `RunRecord.machines` of the session this was probed
    in; always 0 on one machine."""
    runtime_kind: RuntimeKind | None = None
    """``interpreter`` or ``native_build``, as the probe identified the
    runtime behind the benchmark's launchers; ``None`` when it could not
    be identified, with the reason in ``missing``. Only this field says
    a runtime is a native build."""
    runtime_executable: Text | None = None
    """Resolved interpreter or native executable that ran the
    benchmark, such as the absolute path of the tree's Python."""
    runtime_version: Text | None = None
    """Version the interpreter reports; an interpreter only."""
    linked_libraries: tuple[LinkedLibrary, ...] | None = None
    """For a native build, the libraries from outside the tree that it
    loads, sorted by name; empty when it loads none. Doesn't apply to an
    interpreter."""
    env_names: tuple[Text, ...] | None = None
    """Names of every environment variable the benchmark ran with,
    sorted."""
    env_values: ValueMap = Field(default_factory=_FrozenMap)
    """Values of the variables known to affect performance, such as
    ``OMP_NUM_THREADS`` and ``PYTHONHASHSEED``; other values are not
    recorded."""
    dependency_locks: tuple[DependencyLock, ...] | None = None
    """Dependency lockfile hashes, sorted by path; empty when the tree
    has none."""
    missing: ReasonMap = Field(default_factory=_FrozenMap)
    """Reason for each field left ``None``, keyed by field name."""

    @field_validator("env_names", mode="after")
    @classmethod
    def _sort_names(
        cls, names: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if names is None:
            return None
        if len(set(names)) != len(names):
            msg = f"environment variable names repeat: {names}"
            raise ValueError(msg)
        return tuple(sorted(names))

    @field_validator("linked_libraries", mode="after")
    @classmethod
    def _sort_libraries(
        cls, libraries: tuple[LinkedLibrary, ...] | None
    ) -> tuple[LinkedLibrary, ...] | None:
        if libraries is None:
            return None
        names = [library.name for library in libraries]
        if len(set(names)) != len(names):
            msg = f"linked library names repeat: {names}"
            raise ValueError(msg)
        return tuple(sorted(libraries, key=lambda library: library.name))

    @field_validator("dependency_locks", mode="after")
    @classmethod
    def _sort_locks(
        cls, locks: tuple[DependencyLock, ...] | None
    ) -> tuple[DependencyLock, ...] | None:
        if locks is None:
            return None
        paths = [lock.path for lock in locks]
        if len(set(paths)) != len(paths):
            msg = f"lockfile paths repeat: {paths}"
            raise ValueError(msg)
        return tuple(sorted(locks, key=lambda lock: lock.path))

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        kind = self.runtime_kind
        not_applicable = _NOT_APPLICABLE[kind]
        for field in sorted(not_applicable):
            if getattr(self, field) is not None:
                msg = f"{field} does not apply to a {kind} runtime"
                raise ValueError(msg)
        if kind is None and self.runtime_version is not None:
            msg = "a runtime with a version is an interpreter, so say so"
            raise ValueError(msg)
        if kind is None and self.linked_libraries is not None:
            msg = "linked_libraries are recorded for a native build only"
            raise ValueError(msg)
        if kind is not None and self.runtime_executable is None:
            msg = f"a {kind} runtime was identified, so it has an executable"
            raise ValueError(msg)
        if kind == "interpreter" and self.runtime_version is None:
            msg = "an interpreter is identified with its version"
            raise ValueError(msg)
        _check_reasons(self, self.missing, not_applicable)
        unlisted = set(self.env_values) - set(self.env_names or ())
        if unlisted:
            msg = f"env_values for variables not in env_names: {unlisted}"
            raise ValueError(msg)
        return self


def _guardrail_checks(
    spec: GuardrailSpec, result: GuardrailResult
) -> list[Literal["within", "breach", "open", "absent"]]:
    """Return the state of each allowance and limit check.

    There is one check per allowance and per limit, the checks that
    ``docs/method.md`` counts in ``G``. Each is compared exactly, in
    float64, with no tolerance, like the primary bar. Compliance is
    inclusive and a breach strict: an allowance is within when the
    worsening ratio's upper bound is at most ``1 + allowance`` and
    breached when its lower bound exceeds that; a ceiling (a
    lower-is-better guardrail) is within when the candidate's upper
    bound is at most the limit and breached when its lower bound
    exceeds it, and a floor mirrors that. A check whose interval is
    missing is ``absent``; one that is neither within nor breached is
    ``open``.
    """

    def state(
        within: bool, breach: bool
    ) -> Literal["within", "breach", "open"]:
        return "within" if within else "breach" if breach else "open"

    checks: list[Literal["within", "breach", "open", "absent"]] = []
    worsening, candidate = result.worsening, result.candidate
    if spec.allowance is not None:
        bar = 1 + spec.allowance
        checks.append(
            "absent"
            if worsening is None
            else state(worsening.upper <= bar, worsening.lower > bar)
        )
    for limit in spec.limits:
        value = limit.value
        if candidate is None:
            checks.append("absent")
        elif spec.direction == "lower_is_better":
            checks.append(
                state(candidate.upper <= value, candidate.lower > value)
            )
        else:
            checks.append(
                state(candidate.lower >= value, candidate.upper < value)
            )
    return checks


def _check_guardrail_status(
    spec: GuardrailSpec, result: GuardrailResult
) -> None:
    """Check a guardrail's status is the one its evidence gives.

    `not_applicable` exactly when the spec marks the guardrail so, with
    the spec's reason. `passed` needs every check within; `breached`
    needs some check breached, even if another check's interval is
    missing, so the breaching interval is always present and every
    other measured one is kept; `not_established` needs no check
    breached, not every check within, and at least one check whose
    interval was measured: an interval no check reads establishes
    nothing, so that guardrail is `unmeasurable`, with its reason.
    `unmeasurable` carries no measurement, which `GuardrailResult`
    checks.
    """
    if spec.applicable == (result.status == "not_applicable"):
        msg = (
            f"guardrail {spec.metric} is {result.status}, but the spec "
            f"marks it {'applicable' if spec.applicable else 'not'}"
        )
        raise ValueError(msg)
    if not spec.applicable:
        if result.reason != spec.reason:
            msg = f"guardrail {spec.metric} must give the spec's reason"
            raise ValueError(msg)
        return
    if result.status == "unmeasurable":
        return
    checks = _guardrail_checks(spec, result)
    all_within = all(c == "within" for c in checks)
    earned = {
        "passed": all_within,
        "breached": "breach" in checks,
        "not_established": "breach" not in checks
        and not all_within
        and any(c != "absent" for c in checks),
    }[result.status]
    if not earned:
        msg = (
            f"guardrail {spec.metric} is {result.status}, but its checks "
            f"against the allowance and limits give {checks}"
        )
        raise ValueError(msg)


class RunRecord(_Model):
    """Everything about one comparison; written as ``record.json``.

    Raises
    ------
    pydantic.ValidationError
        If the verdict disagrees with the gates (a failed gate must
        decide, the deciding gate must be one that failed, and any other
        verdict needs every gate listed, none failed), a gate is listed
        twice, a waiver or cross-host verdict lacks the spec's opt-in,
        the environments miss a version, repeat a version and session,
        or name a session with no machine, rounds ran without a samples
        path, rounds or looks exceed the spec's budget, the interval
        contradicts the verdict against the bar ``1 + min_effect``, a
        cross-host verdict claims more session pairs than ``machines``
        holds sessions for, or has fewer than ``n0`` pairs and is a
        rule 2 or 3 verdict or the resolution stop, a ``wall_interval``
        appears for a metric other than ``reported``, ``finished_at``
        precedes ``started_at``, or ``candidate_commit`` and
        ``candidate_dirty`` are not set together. Also if the
        guardrail results do not match the spec's guardrails one to one
        (none for a gate verdict), the spec of any other verdict omits
        a default guardrail of ``docs/method.md`` (peak memory, and the
        target class's own) that is not the objective, a status
        disagrees with its intervals, allowance and limits, a KEEP has
        a gate `not_available` or a guardrail other than `passed` or
        `not_applicable`, a guardrail REJECT has no `breached`
        guardrail, or a rule 2 INCONCLUSIVE has a breach or nothing
        that blocked the KEEP. The resolution stop is not a gate
        verdict: it lists every gate and every guardrail result.
    """

    schema_version: SchemaVersion = SCHEMA_VERSION
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
    repository: Repository | None = None
    """The repository compared in, so a claim names what it is about:
    its remote URL, without credentials, or the path of its root when
    it has no remote. ``None`` when not recorded, which the report says.
    A user part that may be a credential is refused, as
    `_check_repository` sets out, so no token is written to the
    record."""
    baseline_commit: GitSha
    """Git commit of the baseline."""
    candidate_commit: GitSha | None = None
    """Git commit the candidate tree was at; ``None`` when not
    recorded."""
    candidate_dirty: Flag | None = None
    """Whether the candidate tree had changes not in
    ``candidate_commit``, so that commit alone does not reproduce what
    was measured; set exactly when ``candidate_commit`` is."""
    order_seed: Count
    """Seed for the in-round order of the two versions."""
    verdict: Verdict
    """The verdict."""
    gates: tuple[GateResult, ...]
    """Gate results, including recorded differences between arms. A
    gate verdict may list only the gates that ran; any other verdict
    lists all seven."""
    guardrails: tuple[GuardrailResult, ...] = ()
    """One result per guardrail of the spec, for any verdict but a gate
    REJECT, which carries none."""
    machines: Annotated[tuple[MachineFingerprint, ...], Field(min_length=1)]
    """Fingerprint per session, indexed by `Sample.session`; one entry
    on one machine."""
    environments: Annotated[tuple[TreeEnvironment, ...], Field(min_length=2)]
    """What each tree ran with, per session; both versions appear."""
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
        if (self.candidate_commit is None) != (self.candidate_dirty is None):
            msg = "candidate_commit and candidate_dirty are set together"
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
        if v.gate is None and frozenset(status) != _ALL_GATES:
            msg = (
                f"a rule {v.rule} verdict needs all {len(_ALL_GATES)} gates "
                f"listed, got {status}"
            )
            raise ValueError(msg)
        opt_ins = {
            "side_effects": self.spec.allow_remote_services,
            "runtime": self.spec.allow_runtime_change,
            "same_host": self.spec.allow_cross_host,
            "coverage": self.spec.allow_unchecked_coverage,
        }
        for gate, allowed in opt_ins.items():
            if status.get(gate) == "waived" and not allowed:
                msg = f"{gate} gate waived without the spec's opt-in"
                raise ValueError(msg)
        cross = status.get("same_host") == "waived"
        if v.cross_host != cross:
            msg = "a cross-host verdict goes with a waived same_host gate"
            raise ValueError(msg)
        if v.rounds and self.samples_path is None:
            msg = f"{v.rounds} rounds ran but samples_path is None"
            raise ValueError(msg)
        self._check_environments()
        self._check_against_spec()
        self._check_guardrails()
        self._check_keep_blockers(status)
        return self

    def _check_guardrails(self) -> None:
        """Check one result per spec guardrail, each status earned.

        A gate REJECT carries no guardrail results, like it carries no
        speedup estimate. Every other verdict has exactly one result per
        guardrail of the spec, and each status is the one its intervals
        give against the guardrail's allowance and limits, compared
        exactly as `_guardrail_checks` describes.

        Nothing is left implicit: the spec must declare, applied or not
        applicable with a reason, peak memory for every target class
        and the defaults of the verdict's target class, except the one
        that is the primary objective.
        """
        results = self.guardrails
        metrics = [r.metric for r in results]
        if len(set(metrics)) != len(metrics):
            msg = f"a guardrail result is listed more than once: {metrics}"
            raise ValueError(msg)
        if self.verdict.gate is not None:
            if results:
                msg = "a gate verdict carries no guardrail results"
                raise ValueError(msg)
            return
        specs = {g.metric: g for g in self.spec.guardrails}
        target = self.verdict.target_class
        defaults = set(DEFAULT_GUARDRAILS[target])
        undeclared = sorted(defaults - set(specs) - {self.spec.objective})
        if undeclared:
            msg = (
                f"default guardrails {undeclared} for target class {target} "
                "must be declared, applied or not applicable with a reason"
            )
            raise ValueError(msg)
        if set(metrics) != set(specs):
            msg = (
                f"guardrail results {sorted(metrics)} must match the spec's "
                f"guardrails {sorted(specs)}"
            )
            raise ValueError(msg)
        for result in results:
            _check_guardrail_status(specs[result.metric], result)

    def _check_keep_blockers(self, status: Mapping[str, str]) -> None:
        """Check rule 2's outcome against the gates and guardrails.

        A KEEP needs every gate checked and every guardrail shown
        compliant. A guardrail REJECT needs a breach. A rule 2
        INCONCLUSIVE needs no breach and something that blocked the
        KEEP: a gate `not_available`, or a guardrail `not_established`
        or `unmeasurable`.
        """
        v = self.verdict
        unavailable = sorted(
            g for g, s in status.items() if s == "not_available"
        )
        states = {r.metric: r.status for r in self.guardrails}
        breached = sorted(m for m, s in states.items() if s == "breached")
        unshown = sorted(m for m, s in states.items() if s in _UNSHOWN)
        if v.outcome == "KEEP" and (unavailable or breached or unshown):
            msg = (
                f"a KEEP needs every gate checked and every guardrail passed "
                f"or not_applicable; gates not available {unavailable}, "
                f"guardrails breached {breached}, not shown {unshown}"
            )
            raise ValueError(msg)
        if v.reason == "guardrail" and not breached:
            msg = "a guardrail REJECT needs a breached guardrail"
            raise ValueError(msg)
        if v.rule == 2 and v.outcome == "INCONCLUSIVE":
            if breached:
                msg = f"guardrails {breached} are breached, so this is REJECT"
                raise ValueError(msg)
            if not (unavailable or unshown):
                msg = "a rule 2 INCONCLUSIVE needs something that blocked KEEP"
                raise ValueError(msg)

    def _check_bar(self) -> None:
        """Check the recorded interval reproduces the recorded verdict.

        The comparisons are exactly those of ``docs/method.md``:
        strict ``L > 1 + min_effect`` for KEEP, inclusive
        ``U <= 1 + min_effect`` for a performance REJECT, and neither
        for INCONCLUSIVE. No tolerance is applied, so the decision code
        must decide on these same float64 bounds and bar.

        Cross host, ``session_pairs`` cannot exceed half the recorded
        sessions. A comparison with fewer than ``n0`` session pairs is
        INCONCLUSIVE whatever its interval shows, so it may not be KEEP
        or a performance REJECT, and its INCONCLUSIVE is exempt from the
        straddle check. Nor may it end at the resolution stop, which
        comes only at a look on ``n0`` pairs or more. From ``n0`` pairs
        on, the usual rules apply. A verdict under an uncalibrated
        procedure is exempt from the straddle check in the same way, for
        the same reason.
        """
        v = self.verdict
        n0 = self.spec.looks.n0
        if v.session_pairs is not None and 2 * v.session_pairs > len(
            self.machines
        ):
            msg = (
                f"{v.session_pairs} session pairs need at least "
                f"{2 * v.session_pairs} sessions in machines, not "
                f"{len(self.machines)}"
            )
            raise ValueError(msg)
        too_few_pairs = v.session_pairs is not None and v.session_pairs < n0
        resolution_stop = v.rule == 1 and v.gate is None
        if too_few_pairs and (v.rule in (2, 3) or resolution_stop):
            msg = (
                f"a cross-host comparison with {v.session_pairs} session "
                f"pairs, fewer than n0 = {n0}, has taken no look, so it is "
                "a rule 5 INCONCLUSIVE or a gate REJECT"
            )
            raise ValueError(msg)
        if v.interval is None:
            return
        bar = 1 + self.spec.min_effect
        keep = v.interval.lower > bar
        reject = v.interval.upper <= bar
        exempt = too_few_pairs or v.diagnostics.procedure_uncalibrated
        if (
            (v.rule == 2 and not keep)
            or (v.rule == 3 and not reject)
            or (v.rule == 5 and not exempt and (keep or reject))
        ):
            msg = (
                f"rule {v.rule} contradicts the interval "
                f"[{v.interval.lower!r}, {v.interval.upper!r}] against the "
                f"bar {bar!r}"
            )
            raise ValueError(msg)

    def _check_environments(self) -> None:
        """Check each tree environment belongs to a known session."""
        keys = [(e.version, e.session) for e in self.environments]
        if len(set(keys)) != len(keys):
            msg = f"a version and session repeat in environments: {keys}"
            raise ValueError(msg)
        if {e.version for e in self.environments} != {"baseline", "candidate"}:
            msg = "environments must cover both versions"
            raise ValueError(msg)
        unknown = {e.session for e in self.environments} - set(
            range(len(self.machines))
        )
        if unknown:
            msg = f"environments name sessions with no machine: {unknown}"
            raise ValueError(msg)

    def _check_against_spec(self) -> None:
        """Check the verdict fits the spec's budget, metric and bar."""
        v, spec = self.verdict, self.spec
        self._check_bar()
        if v.rounds > spec.budget.max_rounds:
            msg = (
                f"{v.rounds} rounds exceed max_rounds = "
                f"{spec.budget.max_rounds}"
            )
            raise ValueError(msg)
        if v.looks > spec.looks.max_looks:
            msg = f"{v.looks} looks exceed max_looks = {spec.looks.max_looks}"
            raise ValueError(msg)
        if (
            v.diagnostics.wall_interval is not None
            and spec.metric.kind != "reported"
        ):
            msg = "a wall_interval is computed only for a reported metric"
            raise ValueError(msg)


MODELS: Final[tuple[type[BaseModel], ...]] = (
    MetricSpec,
    OutputCheck,
    CorrectnessSpec,
    Budget,
    LookSchedule,
    ResetSpec,
    AbsoluteLimit,
    GuardrailSpec,
    BenchmarkSpec,
    LockedFile,
    LockManifest,
    Sample,
    Difference,
    QuarantinedTest,
    SkippedCalls,
    DifferentialCheck,
    UnverifiedLines,
    CommandOutput,
    GateResult,
    Interval,
    Diagnostics,
    Verdict,
    GuardrailResult,
    Hotspot,
    GpuInfo,
    MachineFingerprint,
    RunWarning,
    DependencyLock,
    LinkedLibrary,
    TreeEnvironment,
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
