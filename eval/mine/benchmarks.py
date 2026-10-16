"""Published-benchmark candidates: GSO, SWE-Perf and SWE-fficiency.

Task lists come from the Hugging Face datasets-server rows API; the
per-task cross-machine audit results come from the audit's public
data repository (arXiv 2607.01211), pinned to one commit. Both are
cached under ``cache/benchmarks/``. Candidates are the tasks whose
reference patch passed the benchmark's own validity rule in every
cross-machine replay (``final_status`` of
``cross-machine-construction-valid``) in repositories whose
environment plausibly builds on macOS arm64 with uv. The audit picks
and flags tasks; it is not a label.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ghcache import GhError, gh_json
from task import Change, Command, Link, Task, write_task

CACHE = Path(__file__).resolve().parent / "cache" / "benchmarks"
ROWS_API = "https://datasets-server.huggingface.co/rows"
TIMEOUT_S = 120.0
PAUSE_S = 0.5
MAX_ATTEMPTS = 4

AUDIT_REPO = "chenzhi-cz/performance-optimization-benchmark-reliability"
AUDIT_SHA = "44a1cb86a9f765b0375b4cfb479641f3c19f0774"
AUDIT_SUMMARY = "data/cross_machine_task_summary.csv"
AUDIT_VALID = "cross-machine-construction-valid"
MAX_PER_REPO = 6

DIFF_FILE = re.compile(r"^diff --git a/(\S+) b/", re.MULTILINE)


@dataclass(frozen=True)
class Benchmark:
    """One published benchmark's dataset and audit naming.

    Attributes
    ----------
    name : str
        Short name used in task ids and the cache directory.
    dataset : str
        Hugging Face dataset id.
    audit_name : str
        The ``benchmark`` value in the audit's CSVs.
    page : int
        Rows per datasets-server request; SWE-fficiency rows are large
        enough that 100 a page times out.
    repos : frozenset of str
        Repositories whose environments plausibly build here: pure
        Python, or Python with prebuilt wheels for their compiled
        dependencies. An inference from the repository, not a build.
    """

    name: str
    dataset: str
    audit_name: str
    page: int
    repos: frozenset[str]


BENCHMARKS = (
    Benchmark(
        "gso",
        "gso-bench/gso",
        "gso",
        100,
        frozenset(
            {
                "pydantic/pydantic",
                "tornadoweb/tornado",
                "huggingface/transformers",
            }
        ),
    ),
    Benchmark(
        "swe-perf",
        "SWE-Perf/SWE-Perf",
        "sweperf",
        100,
        frozenset(
            {
                "sympy/sympy",
                "pydata/xarray",
                "mwaskom/seaborn",
                "pylint-dev/pylint",
                "psf/requests",
                "sphinx-doc/sphinx",
            }
        ),
    ),
    Benchmark(
        "swe-fficiency",
        "swefficiency/swefficiency",
        "swefficiency",
        10,
        frozenset({"sympy/sympy", "pydata/xarray", "dask/dask"}),
    ),
)


def _get(url: str) -> bytes:
    error = ""
    for _ in range(MAX_ATTEMPTS):
        time.sleep(PAUSE_S)
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:  # noqa: S310
                return cast(bytes, resp.read())
        except OSError as exc:
            error = str(exc)
    msg = f"GET {url}: {error}"
    raise OSError(msg)


def rows_url(bench: Benchmark, offset: int) -> str:
    """Return the datasets-server URL for one page of rows.

    Parameters
    ----------
    bench : Benchmark
        The benchmark.
    offset : int
        Index of the first row.

    Returns
    -------
    str
        The request URL.
    """
    return (
        f"{ROWS_API}?dataset={bench.dataset}&config=default&split=test"
        f"&offset={offset}&length={bench.page}"
    )


def iter_rows(bench: Benchmark) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield every row of a benchmark, fetching pages not yet cached.

    Parameters
    ----------
    bench : Benchmark
        The benchmark.

    Yields
    ------
    tuple of (int, dict)
        The offset of the row's page and the row's fields.
    """
    offset = 0
    total: int | None = None
    while total is None or offset < total:
        path = CACHE / bench.name / f"rows_{offset}.json"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_get(rows_url(bench, offset)))
        page = json.loads(path.read_text())
        total = int(page["num_rows_total"])
        for item in page["rows"]:
            yield offset, item["row"]
        offset += bench.page


def audit_summary() -> dict[tuple[str, str], dict[str, str]]:
    """Read the audit's per-task summary, fetching it if needed.

    Returns
    -------
    dict
        Rows keyed by ``(benchmark, instance_id)``.
    """
    path = CACHE / "audit" / AUDIT_SUMMARY
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        url = (
            f"https://raw.githubusercontent.com/{AUDIT_REPO}/{AUDIT_SHA}/"
            f"{AUDIT_SUMMARY}"
        )
        path.write_bytes(_get(url))
    with path.open(newline="") as handle:
        return {
            (r["benchmark"], r["instance_id"]): r
            for r in csv.DictReader(handle)
        }


KEEP_FIELDS = (
    "repo",
    "instance_id",
    "base_commit",
    "opt_commit",
    "head_commit",
    "gt_commit_message",
    "efficiency_test",
    "test_cmd",
)


def _trim(row: dict[str, Any]) -> dict[str, Any]:
    patch = str(row.get("gt_diff") or row.get("patch") or "")
    kept = {k: row[k] for k in KEEP_FIELDS if k in row}
    kept["files"] = DIFF_FILE.findall(patch)
    kept["n_tests"] = len(row.get("tests") or [])
    kept["n_pass_to_pass"] = len(row.get("PASS_TO_PASS") or [])
    return kept


def _audit_note(row: dict[str, str]) -> str:
    keys = (
        "final_status",
        "strict_drop_reason",
        "observed_run_count",
        "construction_valid_run_count",
        "faster_only_all_runs",
        "construction_valid_all_runs",
        "median_native_score",
        "min_threshold_margin",
    )
    fields = ", ".join(f"{k}={row.get(k) or 'empty'}" for k in keys)
    return (
        f"{fields} (from {AUDIT_SUMMARY} at "
        f"github.com/{AUDIT_REPO}@{AUDIT_SHA[:12]})"
    )


def _parent(repo: str, sha: str) -> str | None:
    argv = [
        "api",
        f"repos/{repo}/commits/{sha}",
        "--jq",
        "{sha: .sha, parents: [.parents[].sha], html_url: .html_url,"
        " date: .commit.committer.date, message: .commit.message}",
    ]
    try:
        info = cast(dict[str, Any], gh_json(argv))
    except GhError:
        return None
    parents = cast(list[str], info["parents"])
    return parents[0] if parents else None


def _repo_meta(repo: str) -> dict[str, Any]:
    argv = [
        "repo",
        "view",
        repo,
        "--json",
        "nameWithOwner,stargazerCount,pushedAt,isArchived,primaryLanguage,url",
    ]
    try:
        return cast(dict[str, Any], gh_json(argv))
    except GhError:
        return {}


def _commands(
    bench: Benchmark, row: dict[str, Any], source: str
) -> tuple[Command, Command]:
    if bench.name == "gso":
        workload = Command(
            command="python workload.py",
            source=f"the prob_script field of {source}, saved as workload.py",
            verified=False,
        )
        oracle = Command(
            command=None,
            source="unknown: GSO rows carry no test command; its "
            f"{row['n_tests']} generated tests check results against "
            "a stored reference",
            verified=False,
        )
    elif bench.name == "swe-perf":
        tests = cast(list[str], row["efficiency_test"])
        workload = Command(
            command="python -m pytest " + " ".join(tests),
            source=f"the efficiency_test field of {source}",
            verified=False,
        )
        oracle = Command(
            command=None,
            source="unknown: SWE-Perf rows carry no test command",
            verified=False,
        )
    else:
        workload = Command(
            command="python workload.py",
            source=f"the workload field of {source}, saved as workload.py",
            verified=False,
        )
        oracle = Command(
            command=str(row["test_cmd"]),
            source=f"the test_cmd field of {source}; the benchmark runs "
            f"it on {row['n_pass_to_pass']} PASS_TO_PASS tests",
            verified=False,
        )
    return workload, oracle


def _task(
    bench: Benchmark,
    offset: int,
    row: dict[str, Any],
    audit: dict[str, str],
    miner: str,
) -> Task | None:
    repo = str(row["repo"])
    instance = str(row["instance_id"])
    source = f"{bench.dataset} row {instance}"
    page = rows_url(bench, offset)
    files = cast(list[str], row["files"])
    if bench.name == "gso":
        commit = str(row["opt_commit"])
        task_commit = _parent(repo, commit)
        basis = (
            f"first parent of {commit} (gh api commits); the row gives "
            f"base_commit as {row['base_commit']!r}"
        )
        title = str(row["gt_commit_message"]).splitlines()[0]
        url = f"https://github.com/{repo}/commit/{commit}"
        kind = "commit"
        ref_commit: str | None = commit
    else:
        task_commit = str(row["base_commit"])
        basis = f"the base_commit field of {source}"
        # The reference is base_commit plus the row's patch. SWE-Perf's
        # head_commit is the pull request's branch tip, which can sit
        # behind the base and carry other files, so it is evidence only.
        ref_commit = None
        title = f"{bench.name} reference patch for {instance}"
        url = page
        kind = "benchmark-instance"
    if task_commit is None:
        return None
    meta = _repo_meta(repo)
    workload, oracle = _commands(bench, row, source)
    return Task(
        id=f"{bench.name}-{instance.lower().replace('__', '-')}",
        repository=repo,
        stars=meta.get("stargazerCount"),
        pushed_at=meta.get("pushedAt"),
        task_commit=task_commit,
        task_commit_basis=basis,
        reference_change=Change(
            kind=kind,
            url=url,
            title=title,
            commit=ref_commit,
            merged_at=None,
            files=files,
        ),
        revert=None,
        family="published-benchmark",
        change_kind="benchmark-instance",
        target_class="python",
        workload=workload,
        oracle=oracle,
        evidence=[
            Link(
                url=page,
                note=f"datasets-server page holding {instance}; the "
                "reference change is base_commit plus the row's "
                + ("gt_diff" if bench.name == "gso" else "patch")
                + (
                    f" (head_commit {row['head_commit']} is the pull "
                    "request's branch tip, not the reference tree)"
                    if row.get("head_commit")
                    else ""
                ),
            ),
            Link(
                url=f"https://github.com/{AUDIT_REPO}/blob/{AUDIT_SHA}/"
                f"{AUDIT_SUMMARY}",
                note="cross-machine audit (arXiv 2607.01211) per-task row",
            ),
        ],
        audit=_audit_note(audit),
        status="candidate",
        status_reason=(
            "not built or run here; the benchmark's own environments are "
            "linux/amd64 images, so a native macOS arm64 environment "
            "still has to be built"
        ),
        runs=[],
        split="development-candidate",
        mined_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        miner=miner,
    )


def stage_benchmarks() -> None:
    """Write manifests for audit-stable, plausibly local instances."""
    miner = "uv run python eval/mine/mine.py benchmarks"
    audit = audit_summary()
    for bench in BENCHMARKS:
        per_repo: dict[str, int] = {}
        written = 0
        # Rows are trimmed as they stream past: SWE-fficiency's pages
        # total about 2.8 GB, mostly workload scripts and patches.
        rows = sorted(
            (
                (offset, _trim(row))
                for offset, row in iter_rows(bench)
                if str(row["repo"]) in bench.repos
            ),
            key=lambda r: str(r[1]["instance_id"]),
        )
        for offset, row in rows:
            repo = str(row["repo"])
            found = audit.get((bench.audit_name, str(row["instance_id"])))
            if repo not in bench.repos or found is None:
                continue
            if found["final_status"] != AUDIT_VALID:
                continue
            if per_repo.get(repo, 0) == MAX_PER_REPO:
                continue
            task = _task(bench, offset, row, found, miner)
            if task is None:
                continue
            write_task(task)
            per_repo[repo] = per_repo.get(repo, 0) + 1
            written += 1
        sys.stdout.write(f"{bench.name}: {written} manifests {per_repo}\n")
