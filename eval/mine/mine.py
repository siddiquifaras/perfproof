"""Mine evaluation task candidates from GitHub.

Stages, each reading the previous one's output from ``cache/``:

``repos``
    Check every seed repository's stars, last push and archive state
    with `gh repo view`; keep the popular, maintained ones.
``search``
    `gh search prs` in each kept repository for merged performance
    pull requests, reverts and regression fixes.
``view``
    `gh pr view` and `gh api` for the hits that pass the text rules in
    `classify`, and for what each revert reverted.
``llmc``
    Commits to karpathy/llm.c's ``dev/cuda`` that add a kernel version.
``manifests``
    Write one manifest per selected candidate to ``eval/tasks/``.
``benchmarks``
    Manifests for GSO, SWE-Perf and SWE-fficiency instances, flagged
    with the cross-machine audit (see `benchmarks`).
``counts``
    Print the task counts per family and class as Markdown.

Run from the repository root, for example
``uv run python eval/mine/mine.py search``. Every network call goes
through `ghcache`, so reruns are free and reproducible.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import classify
from benchmarks import stage_benchmarks
from ghcache import GhError, gh_json
from task import (
    Change,
    ChangeKind,
    Command,
    Family,
    Link,
    TargetClass,
    Task,
    load_tasks,
    task_path,
    write_task,
)

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
SEEDS = HERE / "seeds.json"

MIN_STARS = 2000
ACTIVE_DAYS = 365

IMPROVEMENT_QUERIES = (
    "perf OR performance OR faster OR speedup OR optimize in:title",
    "speed OR optimization OR optimized OR throughput OR latency in:title",
)
REVERT_QUERY = "revert in:title"
REGRESSION_QUERY = "regression in:title"
SEARCH_LIMIT = "60"
REVERT_LIMIT = "100"

VIEW_FIELDS = (
    "number,title,body,url,state,mergedAt,mergeCommit,files,labels,baseRefName"
)
MAX_VIEWS_PER_REPO = 6
MAX_REVERT_VIEWS = 6
MAX_CULPRIT_VIEWS = 4
MAX_HIDDEN_PER_REPO = 1
VIEW_FILES_CAP = 100
API_FILES_CAP = 3000
MAX_KNOWN_PER_REPO = 3
MAX_LANDED_PER_REPO = 1
MAX_REVERTS_PER_REPO = 3
MAX_REGRESSIONS_PER_REPO = 2
HIDDEN_MAX_SOURCE_FILES = 3

LLMC = "karpathy/llm.c"
LLMC_DIR = "dev/cuda"
ADDED_CASE = re.compile(r"^\+\s*case\s+\d+\s*:", re.MULTILINE)
# A version is a dispatch-switch label that calls the launcher with the
# same number: "case 4: attention_forward4(...)". Kernel function names
# are not used, since helpers like softmax_forward_kernel5 carry
# numbers of their own.
DISPATCH = re.compile(
    r"\bcase\s+(\d+)\s*:\s*\{?\s*(?:return\s+)?([A-Za-z_]\w*?)(\d+)\s*\("
)
NVCC_LINE = re.compile(r"^\s*(nvcc\s.+?)\s*$", re.MULTILINE)

PY_LANGUAGES = {"Python", "Jupyter Notebook"}


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {}


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def _read(name: str) -> Any:
    return json.loads((CACHE / name).read_text())


def _write(name: str, data: object) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / name).write_text(json.dumps(data, indent=1))


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def stage_repos() -> None:
    """Check seed repositories and keep popular, maintained ones.

    Writes ``cache/repos.json``: every seed with its API fields and
    whether it passed, so rejected seeds stay visible.
    """
    seeds = json.loads(SEEDS.read_text())["repos"]
    cutoff = datetime.now(UTC) - timedelta(days=ACTIVE_DAYS)
    rows: list[dict[str, Any]] = []
    for klass, repos in seeds.items():
        for repo in repos:
            argv = [
                "repo",
                "view",
                repo,
                "--json",
                "nameWithOwner,stargazerCount,pushedAt,isArchived,"
                "primaryLanguage,url",
            ]
            try:
                info = gh_json(argv)
            except GhError as exc:
                rows.append({"seed": repo, "class": klass, "error": str(exc)})
                continue
            pushed = datetime.fromisoformat(info["pushedAt"])
            reasons: list[str] = []
            if info["stargazerCount"] < MIN_STARS:
                reasons.append(f"stars {info['stargazerCount']} < {MIN_STARS}")
            if pushed < cutoff:
                reasons.append(f"last push {info['pushedAt']}")
            if info["isArchived"]:
                reasons.append("archived")
            language = _dict(info.get("primaryLanguage"))
            rows.append(
                {
                    "seed": repo,
                    "class": klass,
                    "repo": info["nameWithOwner"],
                    "url": info["url"],
                    "stars": info["stargazerCount"],
                    "pushed_at": info["pushedAt"],
                    "language": language.get("name"),
                    "kept": not reasons,
                    "rejected_because": reasons,
                }
            )
    _write("repos.json", rows)
    kept = sum(1 for r in rows if r.get("kept"))
    _out(f"{len(rows)} seeds, {kept} kept")
    for row in rows:
        if not row.get("kept"):
            why = row.get("rejected_because") or row.get("error")
            _out(f"  rejected {row['seed']}: {why}")


def _kept_repos() -> list[dict[str, Any]]:
    return [r for r in _read("repos.json") if r.get("kept")]


def _search(repo: str, query: str, limit: str) -> list[dict[str, Any]]:
    argv = [
        "search",
        "prs",
        query,
        "--repo",
        repo,
        "--merged",
        "--limit",
        limit,
        "--json",
        "number,title,url,body,closedAt,labels",
    ]
    try:
        return cast(list[dict[str, Any]], gh_json(argv, search=True))
    except GhError as exc:
        _out(f"  search failed {repo} {query!r}: {exc}")
        return []


def stage_search() -> None:
    """Search each kept repository for candidate pull requests.

    Writes ``cache/hits.json`` keyed by repository, with improvement,
    revert and regression hits after the title filter.
    """
    hits: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in _kept_repos():
        repo = row["repo"]
        improvements: dict[int, dict[str, Any]] = {}
        for query in IMPROVEMENT_QUERIES:
            for pr in _search(repo, query, SEARCH_LIMIT):
                if classify.is_perf_title(pr["title"]):
                    improvements[pr["number"]] = pr
        reverts = [
            pr
            for pr in _search(repo, REVERT_QUERY, REVERT_LIMIT)
            if pr["title"].lower().startswith("revert")
            and classify.is_perf_title(pr["title"])
        ]
        regressions = [
            pr
            for pr in _search(repo, REGRESSION_QUERY, SEARCH_LIMIT)
            if classify.is_perf_title(pr["title"])
        ]
        hits[repo] = {
            "improvements": sorted(
                improvements.values(), key=lambda p: -p["number"]
            ),
            "reverts": reverts,
            "regressions": regressions,
        }
        _out(
            f"{repo}: {len(improvements)} perf, {len(reverts)} perf "
            f"reverts, {len(regressions)} perf regressions"
        )
    _write("hits.json", hits)


def view_pr(repo: str, number: int | str) -> dict[str, Any] | None:
    """Fetch one pull request's details.

    Parameters
    ----------
    repo : str
        ``owner/name``.
    number : int or str
        Pull request number.

    Returns
    -------
    dict or None
        The `gh pr view` JSON, or None if the call failed.
    """
    argv = ["pr", "view", str(number), "--repo", repo, "--json", VIEW_FIELDS]
    try:
        return cast(dict[str, Any], gh_json(argv))
    except GhError as exc:
        _out(f"  view failed {repo}#{number}: {exc}")
        return None


def commit_info(repo: str, sha: str) -> dict[str, Any] | None:
    """Fetch a commit's parents, date and message.

    Parameters
    ----------
    repo : str
        ``owner/name``.
    sha : str
        Commit id, full or abbreviated.

    Returns
    -------
    dict or None
        Keys ``sha``, ``parents``, ``date``, ``message``, ``html_url``;
        None if the call failed.
    """
    argv = [
        "api",
        f"repos/{repo}/commits/{sha}",
        "--jq",
        "{sha: .sha, parents: [.parents[].sha], html_url: .html_url,"
        " date: .commit.committer.date, message: .commit.message}",
    ]
    try:
        return cast(dict[str, Any], gh_json(argv))
    except GhError as exc:
        _out(f"  commit failed {repo}@{sha}: {exc}")
        return None


def pr_for_commit(repo: str, sha: str) -> int | None:
    """Find the merged pull request that introduced a commit.

    Parameters
    ----------
    repo : str
        ``owner/name``.
    sha : str
        Commit id.

    Returns
    -------
    int or None
        The pull request number, or None if GitHub links none.
    """
    argv = [
        "api",
        f"repos/{repo}/commits/{sha}/pulls",
        "--jq",
        "[.[] | select(.merged_at != null) | .number]",
    ]
    try:
        numbers = cast(list[int], gh_json(argv))
    except GhError:
        return None
    return numbers[0] if numbers else None


def _with_parent(repo: str, pr: dict[str, Any]) -> dict[str, Any] | None:
    sha = _dict(pr.get("mergeCommit")).get("oid")
    if not pr.get("mergedAt") or not sha:
        return None
    info = commit_info(repo, sha)
    if info is None or not info["parents"]:
        return None
    return {"pr": pr, "commit": info}


def _rank(pr: dict[str, Any]) -> tuple[int, int, int]:
    ev = classify.evidence(pr.get("body"), [])
    has_line = classify.workload_line(pr.get("body")) is not None
    return (-int(ev.has_numbers), -int(has_line), -pr["number"])


def stage_view() -> None:
    """Fetch details for the hits worth a manifest.

    Writes ``cache/viewed.json`` keyed by repository with the viewed
    improvements, reverts (each with the change it reverted) and
    regression culprits.
    """
    hits: dict[str, dict[str, list[dict[str, Any]]]] = _read("hits.json")
    viewed: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for repo, groups in hits.items():
        improvements: list[dict[str, Any]] = []
        prefiltered = [
            pr
            for pr in groups["improvements"]
            if classify.evidence(pr.get("body"), []).has_numbers
            or classify.evidence(pr.get("body"), []).bench_words
        ]
        for pr in sorted(prefiltered, key=_rank)[:MAX_VIEWS_PER_REPO]:
            full = view_pr(repo, pr["number"])
            item = _with_parent(repo, full) if full else None
            if item is not None:
                improvements.append(item)
        reverts: list[dict[str, Any]] = []
        for pr in groups["reverts"][:MAX_REVERT_VIEWS]:
            revert = view_pr(repo, pr["number"])
            if revert is None or not revert.get("mergedAt"):
                continue
            ref = classify.reverted_ref(repo, revert["title"], revert["body"])
            number: int | None = None
            if ref is not None and ref[0] == "pr":
                number = int(ref[1])
            elif ref is not None:
                number = pr_for_commit(repo, ref[1])
            original = view_pr(repo, number) if number else None
            item = _with_parent(repo, original) if original else None
            if item is not None:
                item["revert"] = revert
                reverts.append(item)
        regressions: list[dict[str, Any]] = []
        named = [
            (pr, found)
            for pr in groups["regressions"]
            if (found := classify.culprit_pr(pr.get("body"))) is not None
        ]
        for pr, found in named[:MAX_CULPRIT_VIEWS]:
            culprit = view_pr(repo, found[0])
            item = _with_parent(repo, culprit) if culprit else None
            if item is not None:
                item["fix"] = pr
                item["culprit_text"] = found[1]
                regressions.append(item)
        viewed[repo] = {
            "improvements": improvements,
            "reverts": reverts,
            "regressions": regressions,
        }
        _out(
            f"{repo}: {len(improvements)} improvements, {len(reverts)} "
            f"reverts, {len(regressions)} culprits"
        )
        _write("viewed.json", viewed)


def _llmc_file_commits(path: str) -> list[dict[str, Any]]:
    argv = [
        "api",
        f"repos/{LLMC}/commits?path={path}&per_page=100",
        "--jq",
        "[.[] | {sha: .sha, parents: [.parents[].sha], html_url: .html_url,"
        " date: .commit.committer.date, message: .commit.message}]",
    ]
    return cast(list[dict[str, Any]], gh_json(argv))


def _llmc_patch(sha: str, path: str) -> str:
    argv = [
        "api",
        f"repos/{LLMC}/commits/{sha}",
        "--jq",
        f'[.files[] | select(.filename == "{path}") | .patch // ""]',
    ]
    patches = cast(list[str], gh_json(argv))
    return patches[0] if patches else ""


def _llmc_content(path: str, ref: str) -> str:
    argv = [
        "api",
        f"repos/{LLMC}/contents/{path}?ref={ref}",
        "--jq",
        "{content: .content}",
    ]
    record = cast(dict[str, str], gh_json(argv))
    return base64.b64decode(record["content"]).decode(errors="replace")


def dispatch_versions(source: str) -> dict[str, set[int]]:
    """Return the versions a ``dev/cuda`` file's dispatch switch runs.

    Parameters
    ----------
    source : str
        Contents of one llm.c ``dev/cuda/*.cu`` file.

    Returns
    -------
    dict of str to set of int
        Launcher base name (``attention_forward``) to the ``N`` of every
        ``case N:`` whose body calls ``<base><N>(``.
    """
    found: dict[str, set[int]] = {}
    for case, base, num in DISPATCH.findall(source):
        if case == num:
            found.setdefault(base, set()).add(int(num))
    return found


def _llmc_files(sha: str) -> list[str]:
    argv = [
        "api",
        f"repos/{LLMC}/commits/{sha}",
        "--jq",
        "[.files[].filename]",
    ]
    return cast(list[str], gh_json(argv))


def stage_llmc() -> None:
    """Find llm.c ``dev/cuda`` commits that add a kernel version.

    Versions are the file's dispatch-switch labels, ``case N:`` calling
    the launcher ``<name><N>(``. A non-merge commit qualifies when the
    file after it dispatches a version ``N`` above every version of the
    same launcher in the parent, and the parent dispatches at least
    one: the parent holds the earlier versions and the commit adds a
    new attempt. The earliest qualifying commit per file is kept.
    Writes ``cache/llmc.json``.
    """
    argv = [
        "api",
        f"repos/{LLMC}/contents/{LLMC_DIR}",
        "--jq",
        "[.[] | {name: .name, path: .path, type: .type}]",
    ]
    entries = cast(list[dict[str, str]], gh_json(argv))
    found: list[dict[str, Any]] = []
    for entry in entries:
        if entry["type"] != "file" or not entry["name"].endswith(".cu"):
            continue
        commits = _llmc_file_commits(entry["path"])
        for commit in sorted(commits, key=lambda c: c["date"]):
            if len(commit["parents"]) != 1:
                continue
            patch = _llmc_patch(commit["sha"], entry["path"])
            if ADDED_CASE.search(patch) is None:
                continue
            try:
                parent_src = _llmc_content(entry["path"], commit["parents"][0])
            except GhError:
                continue
            before = dispatch_versions(parent_src)
            after = dispatch_versions(
                _llmc_content(entry["path"], commit["sha"])
            )
            new = sorted(
                (name, num)
                for name, nums in after.items()
                if before.get(name)
                for num in nums
                if num > max(before[name])
            )
            if not new:
                continue
            found.append(
                {
                    "path": entry["path"],
                    "commit": commit,
                    "new_kernels": [f"{n}{k}" for n, k in new],
                    "new_versions": [k for _, k in new],
                    "parent_kernels": {
                        n: sorted(v) for n, v in sorted(before.items())
                    },
                }
            )
            break
        count = sum(f["path"] == entry["path"] for f in found)
        _out(f"{entry['path']}: {count}")
    _write("llmc.json", found)


def _repo_meta() -> dict[str, dict[str, Any]]:
    return {r["repo"]: r for r in _kept_repos()}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def pr_files(pr: dict[str, Any]) -> list[str]:
    """Return every path a pull request changed.

    `gh pr view --json files` stops at 100 files, so a pull request
    with 100 or more is read again, page by page, from
    ``gh api repos/<r>/pulls/<n>/files``, which GitHub serves up to
    3000 files.
    """
    files = cast(list[dict[str, Any]], pr.get("files") or [])
    paths = [str(f["path"]) for f in files]
    match = GH_PR.search(str(pr.get("url", "")))
    if len(paths) < VIEW_FILES_CAP or match is None:
        return paths
    argv = [
        "api",
        "--paginate",
        "--slurp",
        f"repos/{match.group(1)}/pulls/{match.group(2)}/files?per_page=100",
    ]
    try:
        pages = cast(list[list[dict[str, Any]]], gh_json(argv))
    except GhError:
        return paths
    return [str(f["filename"]) for page in pages for f in page]


def files_truncated(pr: dict[str, Any]) -> bool:
    """Whether a pull request's file list may be incomplete.

    Parameters
    ----------
    pr : dict
        `gh pr view` JSON.

    Returns
    -------
    bool
        True when GitHub's 3000-file listing limit was reached, or the
        100-file `gh pr view` list could not be extended.
    """
    count = len(pr_files(pr))
    listed = len(cast(list[Any], pr.get("files") or []))
    return count >= API_FILES_CAP or (
        count == listed and listed >= VIEW_FILES_CAP
    )


def _change(pr: dict[str, Any]) -> Change:
    return Change(
        kind="pull-request",
        url=pr["url"],
        title=pr["title"],
        commit=_dict(pr.get("mergeCommit")).get("oid"),
        merged_at=pr.get("mergedAt"),
        files=pr_files(pr),
        files_truncated=files_truncated(pr),
    )


def _evidence_note(ev: classify.Evidence) -> str:
    parts: list[str] = []
    if ev.speedups:
        parts.append("speedup claims: " + "; ".join(ev.speedups))
    if ev.timings:
        parts.append("timings: " + "; ".join(ev.timings))
    if ev.bench_files:
        parts.append("benchmark files changed: " + ", ".join(ev.bench_files))
    if not parts:
        parts.append("no numbers or benchmark files found by the miner")
    return "description, as matched by the miner: " + " | ".join(parts)


def _oracle(language: str | None, files: list[str], url: str) -> Command:
    tests = [
        f for f in classify.test_files(files) if classify.PYTEST_FILE.search(f)
    ]
    if language in PY_LANGUAGES and tests:
        return Command(
            command="python -m pytest " + " ".join(tests),
            source=f"test files changed by {url} (gh pr view files); "
            "targeted tests only, not the full suite",
            verified=False,
        )
    return Command(command=None, source="unknown", verified=False)


def _workload(body: str | None, url: str) -> Command:
    line = classify.workload_line(body)
    if line is None:
        return Command(command=None, source="unknown", verified=False)
    return Command(
        command=line,
        source=f"quoted in the description of {url}",
        verified=False,
    )


@dataclass(frozen=True)
class _Spec:
    family: Family
    kind: ChangeKind
    item: dict[str, Any]
    suffix: str = ""


NOT_SOURCE = re.compile(
    r"(^|/)(\.github|\.circleci|\.buildkite|\.ci|ci|docker|\.devcontainer"
    r"|docs?|scripts|examples?|cookbooks?)/"
    r"|(^|/)(Dockerfile[^/]*|Makefile|Jenkinsfile|tox\.ini|noxfile\.py"
    r"|setup\.cfg|pyproject\.toml|[^/]*\.ya?ml|[^/]*\.sh|[^/]*\.lock"
    r"|package-lock\.json|[^/]*\.mdx?|[^/]*\.json)$",
    re.IGNORECASE,
)


def source_files(files: list[str]) -> list[str]:
    """Return the changed files that are code the change is about.

    Tests, benchmarks, docs (``docs/`` trees, ``.md``, ``.mdx``),
    changelogs, CI configuration, build or packaging files, lockfiles,
    ``scripts/``, examples and cookbooks, and JSON data are left out.
    """
    tests = set(classify.test_files(files))
    return [
        f
        for f in files
        if f not in tests
        and not classify.BENCH_PATH.search(f)
        and not NOT_SOURCE.search(f)
        and not f.lower().endswith((".md", ".rst", ".txt"))
        and "changelog" not in f.lower()
        and "whatsnew" not in f.lower()
        and "news" not in f.lower()
    ]


FRONTEND = re.compile(
    r"(^|/)(frontend|web|ui)/|\.(tsx?|jsx?|css|scss|vue|svelte|html)$",
    re.IGNORECASE,
)


def in_class(files: list[str], klass: str) -> bool:
    """Whether a change's source files fit its repository's class.

    A change whose source files are all browser frontend code (a
    ``frontend/`` tree, TypeScript, CSS and the like) is outside the
    python, native, serving and GPU classes, whatever its repository.

    Parameters
    ----------
    files : list of str
        Paths the change touched.
    klass : str
        The repository's target class from the seed list.

    Returns
    -------
    bool
        False for a frontend-only change outside web and agentic.
    """
    src = source_files(files)
    if klass in ("web", "agentic") or not src:
        return True
    return not all(FRONTEND.search(f) for f in src)


def _select(
    groups: dict[str, list[dict[str, Any]]], klass: str
) -> list[_Spec]:
    specs: list[_Spec] = []
    # A pull request that was later reverted, or named as a
    # regression's cause, is never also used as an improvement.
    used: set[int] = {
        it["pr"]["number"] for it in groups["reverts"] + groups["regressions"]
    }
    # Titles are checked again against the current rule, and an
    # improvement must change some source file: one touching only CI,
    # tests or docs is not a performance change to the code.
    with_ev = [
        it
        for it in groups["improvements"]
        if it["pr"]["number"] not in used
        and classify.is_perf_title(it["pr"]["title"])
        and not classify.is_non_runtime_title(it["pr"]["title"])
        and source_files(pr_files(it["pr"]))
        and in_class(pr_files(it["pr"]), klass)
        and classify.evidence(it["pr"].get("body"), pr_files(it["pr"])).any
    ]
    hidden = known = landed = 0
    for it in with_ev:
        pr = it["pr"]
        line = classify.workload_line(pr.get("body"))
        small = 0 < len(source_files(pr_files(pr))) <= HIDDEN_MAX_SOURCE_FILES
        numbers = classify.evidence(pr.get("body"), []).has_numbers
        if line and small and numbers and hidden < MAX_HIDDEN_PER_REPO:
            specs.append(_Spec("hidden-fix", "improvement", it))
            hidden += 1
        elif known < MAX_KNOWN_PER_REPO:
            specs.append(_Spec("known-change", "improvement", it))
            known += 1
        elif numbers and landed < MAX_LANDED_PER_REPO:
            specs.append(
                _Spec("nothing-to-keep", "after-landed", it, "-landed")
            )
            landed += 1
        else:
            continue
        used.add(pr["number"])
    reverts = 0
    taken: set[int] = set()
    titles: set[str] = set()
    for it in groups["reverts"]:
        pr, revert = it["pr"], it["revert"]
        if pr["number"] in taken or reverts == MAX_REVERTS_PER_REPO:
            continue
        if not classify.is_perf_title(revert["title"]):
            continue
        if classify.is_non_runtime_title(pr["title"]):
            continue
        if not source_files(pr_files(pr)) or not in_class(pr_files(pr), klass):
            continue
        # A reverted change that is itself a revert, or the same change
        # landed and reverted twice, adds no new task.
        if pr["title"].lower().lstrip().startswith(("revert", "temp revert")):
            continue
        if pr["title"] in titles:
            continue
        titles.add(pr["title"])
        reason, _ = classify.revert_reason(
            classify.revert_text(revert["title"], revert.get("body"))
        )
        family: Family = (
            "nothing-to-keep"
            if reason == "reverted-no-gain"
            else "known-change"
        )
        specs.append(_Spec(family, cast(ChangeKind, reason), it))
        taken.add(pr["number"])
        reverts += 1
    regressions = 0
    for it in groups["regressions"]:
        pr = it["pr"]
        if pr["number"] in taken or regressions == MAX_REGRESSIONS_PER_REPO:
            continue
        found = classify.culprit_pr(it["fix"].get("body"))
        if found is None or int(found[0]) != pr["number"]:
            continue
        specs.append(_Spec("known-change", "regression", it))
        taken.add(pr["number"])
        regressions += 1
    return specs


def _compare_files(repo: str, base: str, head: str) -> set[str] | None:
    argv = [
        "api",
        f"repos/{repo}/compare/{base}...{head}",
        "--jq",
        "[.files[].filename]",
    ]
    try:
        return set(cast(list[str], gh_json(argv)))
    except GhError:
        return None


def _pr_headlines(repo: str, number: int) -> list[str] | None:
    argv = [
        "pr",
        "view",
        str(number),
        "--repo",
        repo,
        "--json",
        "commits",
        "--jq",
        "[.commits[].messageHeadline]",
    ]
    try:
        return cast(list[str], gh_json(argv))
    except GhError:
        return None


def same_headline(message: str, shown: str) -> bool:
    """Whether a commit message has the headline GitHub shows for it.

    GitHub shortens long headlines in ``messageHeadline`` and ends them
    with an ellipsis, so a shortened one matches as a prefix.

    Parameters
    ----------
    message : str
        Full commit message.
    shown : str
        ``messageHeadline`` from `gh pr view --json commits`.

    Returns
    -------
    bool
        True if the message's first line is the headline.
    """
    first = message.splitlines()[0] if message else ""
    if shown.endswith("\u2026"):
        return first.startswith(shown[:-1].rstrip())
    return first == shown


def _rebase_base(
    repo: str, commit: dict[str, Any], heads: list[str]
) -> str | None:
    """Return the base if the merge replayed the PR's commits.

    Walks first parents from the merge commit, matching each against
    the pull request's headlines from last to first, and stops at the
    first mismatch, so a squash merge costs no more than one step.
    """
    current = commit
    for i, shown in enumerate(reversed(heads)):
        if not same_headline(str(current["message"]), shown):
            return None
        if not current["parents"]:
            return None
        parent = str(current["parents"][0])
        if i == len(heads) - 1:
            return parent
        nxt = commit_info(repo, parent)
        if nxt is None:
            return None
        current = nxt
    return None


def pr_base(
    repo: str, pr: dict[str, Any], commit: dict[str, Any]
) -> tuple[str, str] | None:
    """Find the commit a merged pull request was applied to.

    For a merge commit (two parents) or a squash, the base is the first
    parent. A rebase merge replays the pull request's ``k`` commits, so
    the base is ``k`` first parents back. A merge with one parent is
    taken as a rebase when the ``k`` commits ending at it carry the pull
    request's commit headlines in order, and as a squash otherwise. The
    base is then accepted only if the diff from it to the merge commit
    touches exactly the pull request's files.

    Parameters
    ----------
    repo : str
        ``owner/name``.
    pr : dict
        `gh pr view` JSON, with ``number`` and ``files``.
    commit : dict
        `commit_info` of the merge commit.

    Returns
    -------
    tuple of (str, str) or None
        The base commit and how it was found, or None when the pull
        request's commits can't be read, the history is shorter than
        expected, or the diff check fails.
    """
    merge = commit["sha"]
    url = commit["html_url"]
    heads = _pr_headlines(repo, pr["number"])
    if heads is None or not commit["parents"]:
        return None
    base = commit["parents"][0]
    how = f"first parent of {url}"
    if len(commit["parents"]) == 1 and len(heads) > 1:
        rebase = _rebase_base(repo, commit, heads)
        if rebase is not None:
            base = rebase
            how = (
                f"{len(heads)} first parents back from {url}: a rebase "
                "merge whose commits carry the pull request's commit "
                "headlines"
            )
        else:
            how = f"first parent of {url} (squash merge)"
    if _compare_files(repo, base, merge) != set(pr_files(pr)):
        return None
    return (
        base,
        f"{how}; gh api compare {base[:12]}...{merge[:12]} touches "
        "exactly the pull request's files",
    )


def _pr_task(
    repo: str, meta: dict[str, Any], spec: _Spec, miner: str
) -> Task | None:
    pr: dict[str, Any] = spec.item["pr"]
    commit: dict[str, Any] = spec.item["commit"]
    files = pr_files(pr)
    ev = classify.evidence(pr.get("body"), files)
    links = [Link(url=pr["url"], note=_evidence_note(ev))]
    if spec.kind == "after-landed":
        task_commit = commit["sha"]
        basis = (
            "merge commit of the reference change (code after the "
            f"optimization landed), from {commit['html_url']}"
        )
    else:
        found = pr_base(repo, pr, commit)
        if found is None:
            _out(f"  dropped {pr['url']}: no base commit matches its files")
            return None
        task_commit, basis = found
    revert_change: Change | None = None
    if "revert" in spec.item:
        revert = spec.item["revert"]
        revert_change = _change(revert)
        _, snippet = classify.revert_reason(
            classify.revert_text(revert["title"], revert.get("body"))
        )
        links.append(
            Link(
                url=revert["url"],
                note=f"revert; miner classified it as {spec.kind}"
                + (f" from: {snippet!r}" if snippet else " (no reason text)"),
            )
        )
    if "fix" in spec.item:
        links.append(
            Link(
                url=spec.item["fix"]["url"],
                note="regression fix naming this change as the cause: "
                f"{spec.item['culprit_text']!r}",
            )
        )
    target: TargetClass = meta["class"]
    return Task(
        id=f"{_slug(repo)}-pr{pr['number']}{spec.suffix}",
        repository=repo,
        stars=meta["stars"],
        pushed_at=meta["pushed_at"],
        task_commit=task_commit,
        task_commit_basis=basis,
        reference_change=_change(pr),
        revert=revert_change,
        family=spec.family,
        change_kind=spec.kind,
        target_class=target,
        workload=_workload(pr.get("body"), pr["url"]),
        oracle=_oracle(meta.get("language"), files, pr["url"]),
        evidence=links,
        audit=None,
        status="candidate",
        status_reason="not built or run here",
        runs=[],
        split="development-candidate",
        mined_at=_now(),
        miner=miner,
    )


def _llmc_tasks(miner: str) -> Iterable[Task]:
    # llm.c fails the activity threshold (no push in the last year)
    # but the plan names it for GPU hidden-fix tasks; manifests say so.
    rows = [r for r in _read("repos.json") if r.get("repo") == LLMC]
    meta = rows[0] if rows else None
    inactive = "; ".join(meta["rejected_because"]) if meta else "unknown"
    for item in _read("llmc.json"):
        commit = item["commit"]
        path = item["path"]
        content = _llmc_content(path, commit["sha"])
        nvcc = NVCC_LINE.search(content)
        binary = Path(path).stem
        versions = item["new_versions"]
        workload = Command(
            command=(
                f"cd {LLMC_DIR} && {nvcc.group(1)} && ./{binary} "
                f"{versions[-1]}"
                if nvcc
                else None
            ),
            source=(
                f"compile line in the header of {path} at "
                f"{commit['sha'][:12]}; the kernel number is the version "
                "this commit adds, so a fixed baseline-vs-candidate "
                "command still has to be set up"
            ),
            verified=False,
        )
        has_check = "validate_result" in content
        oracle = Command(
            command=workload.command if has_check else None,
            source=(
                f"{path} compares the GPU result against its CPU reference "
                "with validate_result when run"
                if has_check
                else "unknown: no validate_result call found in the file"
            ),
            verified=False,
        )
        first_line = commit["message"].splitlines()[0]
        yield Task(
            id=f"karpathy-llm-c-{_slug(binary)}-{commit['sha'][:10]}",
            repository=LLMC,
            stars=meta["stars"] if meta else None,
            pushed_at=meta["pushed_at"] if meta else None,
            task_commit=commit["parents"][0],
            task_commit_basis=(
                f"first parent of {commit['html_url']} (gh api commits)"
            ),
            reference_change=Change(
                kind="commit",
                url=commit["html_url"],
                title=first_line,
                commit=commit["sha"],
                merged_at=commit["date"],
                files=_llmc_files(commit["sha"]),
            ),
            revert=None,
            family="hidden-fix",
            change_kind="kernel-version",
            target_class="gpu",
            workload=workload,
            oracle=oracle,
            evidence=[
                Link(
                    url=commit["html_url"],
                    note=f"{path} dispatches "
                    + ", ".join(item["new_kernels"])
                    + " after this commit (case N: calling <name><N>); "
                    "its parent dispatches "
                    + "; ".join(
                        f"{name} {nums}"
                        for name, nums in item["parent_kernels"].items()
                    )
                    + " (matched by the miner)",
                )
            ],
            audit=None,
            status="candidate",
            status_reason=(
                "not built or run here: needs an NVIDIA GPU. Repository "
                f"kept despite the activity threshold ({inactive}) because "
                "the plan names its dev/cuda kernels for GPU tasks"
            ),
            runs=[],
            split="development-candidate",
            mined_at=_now(),
            miner=miner,
        )


GH_PR = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")


def benchmark_pr(task: Task) -> tuple[str, int] | None:
    """Return the pull request a published-benchmark task comes from.

    Parameters
    ----------
    task : Task
        Any task.

    Returns
    -------
    tuple of (str, int) or None
        Lower-cased ``owner/name`` and pull request number for
        SWE-Perf and SWE-fficiency instances, whose ids end in the pull
        request number; for GSO, the number in the reference commit's
        ``(#N)`` title suffix. None for other tasks or when no number
        is found.
    """
    if task.family != "published-benchmark":
        return None
    repo = task.repository.lower()
    if task.id.startswith(("swe-perf-", "swe-fficiency-")):
        return (repo, int(task.id.rsplit("-", 1)[1]))
    match = re.search(r"\(#(\d+)\)", task.reference_change.title)
    return (repo, int(match.group(1))) if match else None


def stage_manifests() -> None:
    """Write manifests for the selected candidates.

    Existing manifests with runs recorded are left alone, so a rerun
    of the miner never discards local build evidence.
    """
    miner = "uv run python eval/mine/mine.py manifests"
    existing = {t.id: t for t in load_tasks()}
    viewed: dict[str, dict[str, list[dict[str, Any]]]] = _read("viewed.json")
    meta = _repo_meta()
    written = 0
    tasks: list[Task] = []
    # Pull requests that are also published-benchmark instances stay
    # there only, so one change can never land in both splits.
    benchmark_prs = {
        k for t in existing.values() if (k := benchmark_pr(t)) is not None
    }
    for repo, groups in viewed.items():
        for spec in _select(groups, meta[repo]["class"]):
            key = (repo.lower(), spec.item["pr"]["number"])
            if key in benchmark_prs:
                _out(f"  skipped {spec.item['pr']['url']}: a benchmark task")
                continue
            task = _pr_task(repo, meta[repo], spec, miner)
            if task is not None:
                tasks.append(task)
    if (CACHE / "llmc.json").exists():
        tasks.extend(_llmc_tasks(miner))
    for task in tasks:
        old = existing.get(task.id)
        if old is not None and old.runs:
            continue
        if old is not None and task_path(old) != task_path(task):
            task_path(old).unlink()
        write_task(task)
        written += 1
    # Manifests this stage wrote before that its rules no longer select
    # are removed, unless they carry runs from this machine.
    current = {t.id for t in tasks}
    removed = 0
    for old in existing.values():
        if old.miner == miner and old.id not in current and not old.runs:
            task_path(old).unlink()
            removed += 1
    _out(f"{written} manifests written, {removed} no longer selected")


def stage_counts() -> None:
    """Print task counts per family and class as Markdown tables."""
    tasks = load_tasks()
    classes = ["python", "native", "web", "serving", "gpu", "agentic", "java"]
    families = sorted({t.family for t in tasks})
    by = Counter((t.family, t.target_class) for t in tasks)
    _out("| Family | " + " | ".join(classes) + " | Total |")
    _out("|---|" + "---:|" * (len(classes) + 1))
    for fam in families:
        cells = [str(by[(fam, c)]) for c in classes]
        total = sum(by[(fam, c)] for c in classes)
        _out(f"| {fam} | " + " | ".join(cells) + f" | {total} |")
    col = [str(sum(by[(f, c)] for f in families)) for c in classes]
    _out("| **Total** | " + " | ".join(col) + f" | {len(tasks)} |")
    _out("")
    kinds = Counter(t.change_kind for t in tasks)
    _out("| Change kind | Tasks |")
    _out("|---|---:|")
    for kind, n in sorted(kinds.items()):
        _out(f"| {kind} | {n} |")
    _out("")
    statuses = Counter(t.status for t in tasks)
    _out("| Status | Tasks |")
    _out("|---|---:|")
    for status, n in sorted(statuses.items()):
        _out(f"| {status} | {n} |")


STAGES = {
    "repos": stage_repos,
    "search": stage_search,
    "view": stage_view,
    "llmc": stage_llmc,
    "manifests": stage_manifests,
    "benchmarks": stage_benchmarks,
    "counts": stage_counts,
}


def main(argv: list[str] | None = None) -> int:
    """Run one miner stage.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Process exit status.
    """
    parser = argparse.ArgumentParser(
        description="Mine evaluation task candidates from GitHub."
    )
    parser.add_argument("stage", choices=sorted(STAGES))
    args = parser.parse_args(argv)
    STAGES[args.stage]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
