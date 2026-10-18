"""Full test-suite recipes and readers for their results.

A task's full suite is its entry in ``suites.json``: the command, the
reader for its results, and where the command came from. A reader
turns the suite's output into counts and failing test ids; when it
can't, or when no test passed, the counts are None and the
correctness label is ``unknown``.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

HERE = Path(__file__).resolve().parent
SUITES = HERE / "suites.json"
JUNIT = "eval-label-junit.xml"
DEFAULT_TIMEOUT_S = 3600.0

Reader = Literal["pytest-junit", "unittest", "cargo"]
READERS: tuple[Reader, ...] = ("pytest-junit", "unittest", "cargo")


@dataclass
class SuiteResult:
    """Counts and failing ids read from one suite run.

    Attributes
    ----------
    passed, failed, errors, skipped : int or None
        Test counts; None when the results could not be read.
    failing : list of str or None
        Ids of failed and errored tests, sorted; None when unreadable.
    reason : str
        Why the results could not be read, when they couldn't.
    """

    passed: int | None = None
    failed: int | None = None
    errors: int | None = None
    skipped: int | None = None
    failing: list[str] | None = field(default=None)
    reason: str = ""

    @property
    def read(self) -> bool:
        """Whether counts and failing ids were read.

        Returns
        -------
        bool
            True when ``failing`` is not None.
        """
        return self.failing is not None

    def to_json(self) -> dict[str, Any]:
        """Return the result as JSON-ready data.

        Returns
        -------
        dict
            Field names to values.
        """
        return asdict(self)


def _needs_a_pass(result: SuiteResult) -> SuiteResult:
    # A run in which no test passed (none collected, or a build that
    # only reached an empty summary) proves nothing about the tree.
    if result.read and not result.passed:
        return SuiteResult(reason="the suite ran with zero tests passed")
    return result


def recipes() -> dict[str, dict[str, Any]]:
    """Return every task's full-suite recipe.

    Returns
    -------
    dict
        Task id to recipe: ``command``, ``reader``, ``source``, and
        optional ``setup`` (lines run first), ``timeout_s`` and
        ``notes``.
    """
    return cast(dict[str, dict[str, Any]], json.loads(SUITES.read_text()))


def read_junit(text: str) -> SuiteResult:
    """Read a pytest JUnit XML report.

    Parameters
    ----------
    text : str
        The report, as pytest's ``--junitxml`` wrote it.

    Returns
    -------
    SuiteResult
        Counts from the test cases (not the summary attributes), and
        ``<classname>::<name>`` ids of failed and errored cases.
    """
    try:
        # The report is one pytest just wrote on this machine.
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError as exc:
        return SuiteResult(reason=f"JUnit report unreadable: {exc}")
    passed = failed = errors = skipped = 0
    failing: list[str] = []
    for case in root.iter("testcase"):
        cid = f"{case.get('classname', '')}::{case.get('name', '')}"
        tags = {child.tag for child in case}
        if "error" in tags:
            errors += 1
            failing.append(cid)
        elif "failure" in tags:
            failed += 1
            failing.append(cid)
        elif "skipped" in tags:
            skipped += 1
        else:
            passed += 1
    if passed + failed + errors + skipped == 0:
        return SuiteResult(reason="JUnit report has no test cases")
    return _needs_a_pass(
        SuiteResult(passed, failed, errors, skipped, sorted(set(failing)))
    )


_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_SUMMARY = re.compile(r"^(OK|FAILED)(?: \(([^)]*)\))?\s*$", re.MULTILINE)
_UNITTEST_FAIL = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", re.MULTILINE)


def read_unittest(text: str) -> SuiteResult:
    """Read the output of a unittest runner (Django's ``runtests.py``).

    Parameters
    ----------
    text : str
        The runner's combined output.

    Returns
    -------
    SuiteResult
        Counts from the ``Ran N tests`` line and the ``OK`` or
        ``FAILED (...)`` summary after it, and failing ids from the
        ``FAIL:`` and ``ERROR:`` headers, as ``module.Class.test``.
    """
    ran = _RAN.findall(text)
    summary = _SUMMARY.findall(text)
    if not ran or not summary:
        return SuiteResult(reason="no 'Ran N tests' line and summary")
    total = int(ran[-1])
    counts: dict[str, int] = {}
    for part in summary[-1][1].split(","):
        key, _, value = part.strip().partition("=")
        if value.strip().isdigit():
            counts[key.strip()] = int(value)
    failed = counts.get("failures", 0)
    errors = counts.get("errors", 0)
    skipped = counts.get("skipped", 0)
    failing: set[str] = set()
    for _, name, where in _UNITTEST_FAIL.findall(text):
        # Python 3.11+ prints (module.Class.test), older (module.Class).
        failing.add(where if where.endswith("." + name) else f"{where}.{name}")
    passed = total - failed - errors - skipped
    return _needs_a_pass(
        SuiteResult(passed, failed, errors, skipped, sorted(failing))
    )


_CARGO_RESULT = re.compile(
    r"^test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored;",
    re.MULTILINE,
)
_CARGO_FAIL = re.compile(r"^test (\S+) \.\.\. FAILED$", re.MULTILINE)


def read_cargo(text: str) -> SuiteResult:
    """Read the output of ``cargo test``.

    Parameters
    ----------
    text : str
        Combined output of ``cargo test --no-fail-fast``.

    Returns
    -------
    SuiteResult
        Counts summed over every ``test result:`` line, and the names
        of tests reported ``... FAILED``.
    """
    results = _CARGO_RESULT.findall(text)
    if not results:
        return SuiteResult(reason="no 'test result:' line")
    passed = sum(int(p) for p, _, _ in results)
    failed = sum(int(f) for _, f, _ in results)
    skipped = sum(int(i) for _, _, i in results)
    failing = sorted(set(_CARGO_FAIL.findall(text)))
    return _needs_a_pass(SuiteResult(passed, failed, 0, skipped, failing))


def read_results(reader: str, output: str, tree: Path) -> SuiteResult:
    """Read a suite's results with the named reader.

    Parameters
    ----------
    reader : str
        One of `READERS`.
    output : str
        The suite's combined output.
    tree : Path
        The tree it ran in (``pytest-junit`` reads `JUNIT` there).

    Returns
    -------
    SuiteResult
        The counts, or a reason they could not be read.
    """
    if reader == "pytest-junit":
        report = tree / JUNIT
        if not report.exists():
            return SuiteResult(reason=f"no {JUNIT} written")
        return read_junit(report.read_text(errors="replace"))
    if reader == "unittest":
        return read_unittest(output)
    if reader == "cargo":
        return read_cargo(output)
    return SuiteResult(reason=f"unknown reader {reader!r}")
