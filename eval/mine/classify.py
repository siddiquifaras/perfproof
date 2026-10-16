"""Text rules that sort mined pull requests into task candidates.

These are deterministic keyword and pattern rules over the text GitHub
returned. They decide only which pull requests a human should look
at, and they record the exact text that matched, so the lead can
check every classification against its source. They never produce a
performance label: that is T4.2's independent measurement.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

# "optimize" and "vectorize" are also names: SciPy's optimize module
# and numpy's vectorize function. Used as a name, after a dot or before
# a dot, slash or colon, the word doesn't count.
PERF_TITLE = re.compile(
    r"\b(perf|performance|faster|speed\s?up|speedup|speed"
    r"|(?<!\.)(?:optimi[sz]e|vectori[sz]e)(?![./:])"
    r"|optimi[sz]ation|optimi[sz]ed|accelerate|slow"
    r"|latency|throughput)\b",
    re.IGNORECASE,
)

# A bare capital X after a number is usually a product name ("Ryzen 7
# 5800X"), so it counts only when a speed word follows.
SPEEDUP = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?-i:x|\u00d7)\s?(?:faster|speed\s?up|improvement"
    r"|quicker|throughput)?(?=[\s,.;:)|]|$)"
    r"|\b\d+(?:\.\d+)?\s?(?-i:X)\s?(?:faster|speed\s?up|improvement"
    r"|quicker|throughput)\b"
    r"|\b\d+(?:\.\d+)?\s?%\s?(?:faster|speed\s?up|improvement|reduction"
    r"|less|fewer|lower|higher|more throughput|decrease|increase)",
    re.IGNORECASE,
)
TIMING = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:ns|\u00b5s|\u03bcs|us|ms|sec|seconds|s)\b"
    r"(?:\s?(?:\u00b1|\+-)\s?\d+(?:\.\d+)?\s?(?:ns|\u00b5s|\u03bcs|us|ms|s))?",
)
CONTEXT_CHARS = 60
TABLE_LEAD_LINES = 4
NOT_SPEED_AFTER = re.compile(
    r"[ \t]*(?:gpus?|h\d+|a\d+|b\d+|l4|t4|nodes?|cards?|devices?|cores?"
    r"|cpus?|[kmgt]i?b\b|vram|\d)",
    re.IGNORECASE,
)
SPEED_CONTEXT = re.compile(
    r"\b(faster|slower|speed\s?ups?|speed|latency|throughput|time|times"
    r"|timing|before|after|perf|performance|improve\w*|reduc\w*"
    r"|benchmark\w*|criterion|asv|pyperf|timeit|hyperfine|slow\w*"
    r"|(?:nano|micro|milli)?seconds?|measured|measurement)\b"
    r"|\d\s?(?:ns|ms|us|\u00b5s|s)\b"
    r"|(?<![\w/])(?:ns|ms|\u00b5s|\u03bcs|tok/s|tokens/s|t/s|tflop/s"
    r"|tflops|gflop/s|gb/s|req/s|it/s)(?![\w/])"
    r"|(?<![\w/])us(?=\s*[|)\]])|\(us\)",
    re.IGNORECASE,
)
NON_RUNTIME_TITLE = re.compile(
    r"^\s*(?:\[(?:docs?|ci|tests?|example)\]"
    r"|(?:docs?|ci|tests?|chore\(ci\)|fix\(tests?\)|build\(ci\))\s*:)"
    r"|\b(?:speed\s?up|faster|slow|optimi[sz]e)\b[^.]{0,12}"
    r"\b(?:tests?|test suite|ci|ci jobs?|linting|docs)\b",
    re.IGNORECASE,
)
BENCH_WORDS = re.compile(
    r"\b(benchmark|benchmarks|asv|pyperf|timeit|hyperfine|criterion"
    r"|before|after|baseline|speedup|throughput|tokens/s|req/s"
    r"|requests/sec|ops/sec)\b",
    re.IGNORECASE,
)
BENCH_PATH = re.compile(
    r"(^|/)(bench|benches|benchmark|benchmarks|asv_bench|perf)(/|_|\.)"
    r"|bench[^/]*\.(py|rs|c|cc|cpp|js|ts|go|cu)$",
    re.IGNORECASE,
)
# "spec" only as a directory or a *_spec / *.spec file: spec_decode/
# in an LLM server is speculative decoding, not a test.
TEST_PATH = re.compile(
    r"(^|/)(tests?|testing)(/|_)|(^|/)spec/|(^|/)test_[^/]+\.py$"
    r"|_test\.(py|go|rs|cc|cpp)$|\.test\.(js|ts)$|_spec\.rb$"
    r"|\.spec\.(js|ts)$",
    re.IGNORECASE,
)
PYTEST_FILE = re.compile(r"(^|/)(test_[^/]*|[^/]*_test)\.py$")
WORKLOAD_LINE = re.compile(
    r"^\s*(?:\$\s+|>>>\s+|In \[\d+\]:\s+)?"
    r"((?:python3?\s+-m\s+(?:timeit|pyperf)|asv\s+(?:run|continuous"
    r"|compare)|hyperfine|cargo\s+bench|go\s+test\s+.*-bench"
    r"|pytest\s+.*--benchmark|npm\s+run\s+bench|pnpm\s+(?:run\s+)?bench"
    r"|node\s+\S*bench|%timeit|python3?\s+\S*bench\S*\.py"
    r"|vllm\s+bench\s+(?:serve|latency|throughput)\b"
    r"|python3?\s+-m\s+sglang\.bench\S*"
    r"|python3?\s+benchmarks/\S+|\./\S*bench\S*).*)$",
    re.IGNORECASE | re.MULTILINE,
)

REVERTS_PR = re.compile(
    r"\breverts?\s+(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<num>\d+)",
    re.IGNORECASE,
)
REVERTS_URL = re.compile(
    r"https://github\.com/(?P<repo>[\w.-]+/[\w.-]+)/pull/(?P<num>\d+)",
)
REVERTS_SHA = re.compile(
    r"\bthis reverts commit (?P<sha>[0-9a-f]{7,40})", re.IGNORECASE
)
TITLE_PR_NUM = re.compile(r'^Revert\s+"[^"]*\(#(?P<num>\d+)\)"')

NO_GAIN = re.compile(
    r"(slower|no (?:measurable |significant |real )?(?:improvement"
    r"|speed\s?up|benefit|gain|difference)|did ?n[o']t help"
    r"|does ?n[o']t help|not faster|performance regression"
    r"|perf(?:ormance)? regress\w*|regress\w* (?:the )?performance"
    r"|made (?:it|things) slower)",
    re.IGNORECASE,
)
# A revert because a build, CI job or lint step failed says nothing
# about the change's behaviour, so its reason stays unknown.
_BUILD = (
    r"(?:build|builds|building|wheels?|compil\w*|lint\w*|pylint"
    r"|tutorials?|ci image|image build|workflows?|import errors?)"
)
_FAIL = r"(?:fail\w*|break\w*|broke|broken|errors?)"
# Only a failure word and a build word in the same sentence, within a
# few words of each other, counts: a revert that merely links a CI run
# while describing a behaviour break is still a break.
BUILD_OR_CI = re.compile(
    rf"\b{_FAIL}\b[^.\n]{{0,60}}\b{_BUILD}\b"
    rf"|\b{_BUILD}\b[^.\n]{{0,60}}\b{_FAIL}\b",
    re.IGNORECASE,
)
BREAKING = re.compile(
    r"\b(break|breaks|broke|broken|breaking|crash\w*|segfault\w*"
    r"|incorrect\w*|wrong|bug|bugs|fails?|failing|failures?|errors?"
    r"|deadlock\w*|hang|hangs|corrupt\w*|leak\w*|race condition"
    r"|nondetermin\w*|accuracy)\b",
    re.IGNORECASE,
)

# "introduced in #N" alone also describes features and optimizations,
# so the phrase must say the regression or slowdown came from #N.
CULPRIT = re.compile(
    r"(?:regress\w*|slowdown|slower)\s+(?:\S+\s+){0,3}?"
    r"(?:introduced|caused)?\s*(?:in|by|from|since)"
    r"\s+(?:PR\s+)?(?:(?:https://github\.com/[\w.-]+/[\w.-]+/pull/)"
    r"|#)(?P<num>\d+)",
    re.IGNORECASE,
)

SNIPPET_CHARS = 60


@dataclass(frozen=True)
class Evidence:
    """Signals of measured performance evidence in a pull request.

    Attributes
    ----------
    speedups : tuple of str
        Speedup or percentage claims found in the description.
    timings : tuple of str
        Timing values found in the description.
    bench_words : bool
        Whether benchmarking vocabulary appears in the description.
    bench_files : tuple of str
        Changed files whose paths look like benchmarks.
    """

    speedups: tuple[str, ...]
    timings: tuple[str, ...]
    bench_words: bool
    bench_files: tuple[str, ...]

    @property
    def has_numbers(self) -> bool:
        """Whether the description reports measured numbers.

        Returns
        -------
        bool
            True for a speedup or percentage claim, or for at least two
            timings next to benchmarking vocabulary.
        """
        return bool(self.speedups) or (
            len(self.timings) >= 2 and self.bench_words
        )

    @property
    def any(self) -> bool:
        """Whether there is numeric or benchmark-file evidence.

        Returns
        -------
        bool
            True if `has_numbers` or a benchmark file changed.
        """
        return self.has_numbers or bool(self.bench_files)


def _dedupe(items: list[str], limit: int) -> tuple[str, ...]:
    seen: list[str] = []
    for item in items:
        text = " ".join(item.split())
        if text and text not in seen:
            seen.append(text)
        if len(seen) == limit:
            break
    return tuple(seen)


def is_perf_title(title: str) -> bool:
    """Return whether a title talks about performance.

    Parameters
    ----------
    title : str
        Pull request title.

    Returns
    -------
    bool
        True if a performance keyword appears as a word.
    """
    return PERF_TITLE.search(title) is not None


def evidence(body: str | None, files: list[str]) -> Evidence:
    """Find performance evidence in a pull request.

    Parameters
    ----------
    body : str or None
        Pull request description.
    files : list of str
        Paths changed by the pull request.

    Returns
    -------
    Evidence
        What was found, with the matched text kept for review.
    """
    text = body or ""
    speedups = _dedupe(
        [
            m.group(0)
            for m in SPEEDUP.finditer(text)
            if _speed_context(text, m)
        ],
        5,
    )
    timings = _dedupe([m.group(0) for m in TIMING.finditer(text)], 8)
    return Evidence(
        speedups=speedups,
        timings=timings,
        bench_words=BENCH_WORDS.search(text) is not None,
        bench_files=tuple(f for f in files if BENCH_PATH.search(f)),
    )


def _speed_context(text: str, match: re.Match[str]) -> bool:
    """Whether a speedup-shaped match is about speed at all.

    "1x GPU" or "2x H100" counts devices, "4x" next to "GiB" is a
    memory figure, and "64 x 4096" is a shape. A multiplier counts only
    with a speed word nearby (within a few words, on its line, or in
    the header of the table it sits in) and no device, memory unit or
    number right after it. A percentage already carries its own speed
    word.
    """
    if "%" in match.group(0):
        return True
    after = text[match.end() : match.end() + 12]
    if NOT_SPEED_AFTER.match(after):
        return False
    window = text[
        max(0, match.start() - CONTEXT_CHARS) : match.end() + CONTEXT_CHARS
    ]
    lines = text[: match.end()].split("\n")
    rest = text[match.end() :].split("\n", 1)[0]
    line = lines[-1] + rest
    context = [window, line]
    # In a Markdown table the speed word is usually in the header row,
    # or in the heading or paragraph just above the table.
    if line.lstrip().startswith("|"):
        top = len(lines) - 1
        while top > 0 and lines[top - 1].lstrip().startswith("|"):
            top -= 1
        context.append(lines[top])
        context.append("\n".join(lines[max(0, top - TABLE_LEAD_LINES) : top]))
    return any(SPEED_CONTEXT.search(c) is not None for c in context)


def is_non_runtime_title(title: str) -> bool:
    """Whether a title is about tests, CI, docs or examples.

    Parameters
    ----------
    title : str
        Pull request title.

    Returns
    -------
    bool
        True for titles like "Speed up prepare CI job", "perf: speed
        up make tests" or "[Docs] ...": a change that makes tests or CI
        faster is not a runtime performance change.
    """
    return NON_RUNTIME_TITLE.search(title) is not None


def test_files(files: list[str]) -> list[str]:
    """Return the changed paths that look like tests.

    Parameters
    ----------
    files : list of str
        Paths changed by a pull request.

    Returns
    -------
    list of str
        The subset whose path matches a test-file pattern.
    """
    return [f for f in files if TEST_PATH.search(f)]


def workload_line(body: str | None) -> str | None:
    """Return the first benchmark command quoted in a description.

    Parameters
    ----------
    body : str or None
        Pull request description.

    Returns
    -------
    str or None
        The command as written, without a shell or REPL prompt and with
        backslash continuations joined, or None if no line looks like
        a benchmark invocation or the command can't be recovered whole.
    """
    text = body or ""
    match = WORKLOAD_LINE.search(text)
    if match is None:
        return None
    command = match.group(1).strip()
    # A shell command continued with backslashes is joined; one that
    # can't be recovered whole (open quotes, a dangling continuation)
    # stays unknown rather than being stored as a fragment.
    rest = text[match.end() :].splitlines()[1:]
    while command.endswith("\\"):
        if not rest:
            return None
        command = command[:-1].rstrip() + " " + rest.pop(0).strip()
    if command.startswith("%"):
        return command
    try:
        shlex.split(command)
    except ValueError:
        return None
    return command


def reverted_ref(
    repo: str, title: str, body: str | None
) -> tuple[str, str] | None:
    """Find what a revert pull request reverts.

    Parameters
    ----------
    repo : str
        ``owner/name`` of the revert's repository.
    title : str
        Revert pull request title.
    body : str or None
        Revert pull request description.

    Returns
    -------
    tuple of (str, str) or None
        ``("pr", number)`` or ``("commit", sha)``, or None if the text
        names neither. A reference into another repository is ignored.
    """
    text = body or ""
    for pattern in (REVERTS_PR, REVERTS_URL):
        match = pattern.search(text)
        if match is not None:
            other = match.group("repo")
            if other is None or other.lower() == repo.lower():
                return ("pr", match.group("num"))
    match = REVERTS_SHA.search(text)
    if match is not None:
        return ("commit", match.group("sha"))
    match = TITLE_PR_NUM.search(title)
    if match is not None:
        return ("pr", match.group("num"))
    return None


def _snippet(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - SNIPPET_CHARS)
    end = min(len(text), match.end() + SNIPPET_CHARS)
    return " ".join(text[start:end].split())


QUOTED = re.compile(r'"[^"\n]*"')
REVERT_BOILERPLATE = re.compile(
    r"\breverts?\s+[\w./-]*#\d+|\bthis reverts commit\s+[0-9a-f]+",
    re.IGNORECASE,
)


def revert_text(title: str, body: str | None) -> str:
    """Return the part of a revert that can explain why it was made.

    The quoted original title and the "Reverts #N" or "This reverts
    commit" lines are dropped, so words from the reverted change's own
    title ("BUG/PERF: ...") are not mistaken for the reason.

    Parameters
    ----------
    title : str
        Revert pull request title.
    body : str or None
        Revert pull request description.

    Returns
    -------
    str
        Title and description with the boilerplate removed.
    """
    text = f"{title}\n{body or ''}"
    text = REVERT_BOILERPLATE.sub("", text)
    return QUOTED.sub("", text)


def revert_reason(text: str) -> tuple[str, str | None]:
    """Classify why a change was reverted, from the revert's text.

    Parameters
    ----------
    text : str
        Revert title and description joined.

    Returns
    -------
    tuple of (str, str or None)
        ``"reverted-no-gain"``, ``"reverted-breaking"`` or
        ``"reverted-unknown-reason"``, and the text around the match
        (both matches when both kinds appear, None when neither does).
    """
    no_gain = NO_GAIN.search(text)
    breaking = BREAKING.search(text)
    build = BUILD_OR_CI.search(text)
    if build is not None and no_gain is None:
        return ("reverted-unknown-reason", _snippet(text, build))
    if no_gain is not None and breaking is None:
        return ("reverted-no-gain", _snippet(text, no_gain))
    if breaking is not None and no_gain is None:
        return ("reverted-breaking", _snippet(text, breaking))
    if no_gain is not None and breaking is not None:
        both = f"{_snippet(text, no_gain)} | {_snippet(text, breaking)}"
        return ("reverted-unknown-reason", both)
    return ("reverted-unknown-reason", None)


def culprit_pr(body: str | None) -> tuple[str, str] | None:
    """Find the pull request a regression fix names as the cause.

    Parameters
    ----------
    body : str or None
        Description of a pull request that fixes a regression.

    Returns
    -------
    tuple of (str, str) or None
        The culprit's number and the text around the mention, or None
        if the description does not name one in a recognised phrase.
    """
    text = body or ""
    match = CULPRIT.search(text)
    if match is None:
        return None
    return (match.group("num"), _snippet(text, match))
