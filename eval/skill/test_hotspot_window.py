"""The hotspot check on real profiles of the hidden-fix tasks.

``fixtures/hiddenfix-probe`` holds, per task, the first 15 rows of a
real profile's two lists (see ``VERSIONS.txt`` there). Each test prints
them the way the skill tells an agent to (JSON objects or ``function
file line self total`` lines), wraps them in a transcript and scores it
with the task's real manifest.
"""

import json
from pathlib import Path
from typing import Any, Literal

import pytest

import scoring
import taskset
import transcript
from taskset import Hotspot, HotspotName, Task

HERE = Path(__file__).resolve().parent
PROBE = HERE / "fixtures" / "hiddenfix-probe"


def _task(file: str, task_id: str) -> Task:
    tasks = taskset.load(HERE / file)
    return next(t for t in tasks.tasks if t.id == task_id)


def _fixture(name: str) -> dict[str, list[dict[str, Any]]]:
    return json.loads((PROBE / f"{name}.json").read_text())


def _lines(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{r['function']} {r['file']} {r['line']} "
        f"{r['self_fraction']} {r['total_fraction']}"
        for r in rows
    )


def _objects(rows: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(r) for r in rows)


def _printed(
    data: dict[str, list[dict[str, Any]]],
    lists: str,
    form: str,
    labelled: bool = True,
) -> str:
    """Print the chosen lists, each under its list's name by default."""
    keys = {
        "self": ["hotspots"],
        "total": ["frames_by_total"],
        "both": ["hotspots", "frames_by_total"],
    }[lists]
    render = _lines if form == "lines" else _objects
    if not labelled:
        return "\n".join(render(data[k]) for k in keys)
    return "\n".join(f"{k}:\n{render(data[k])}" for k in keys)


def _events(printed: str, said: str) -> list[transcript.Turn]:
    use = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "perfproof profile w --json | py"},
                }
            ]
        },
    }
    out = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": printed,
                    "is_error": False,
                }
            ]
        },
    }
    say = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": said}]},
    }
    return [transcript.Turn([use, out, say], None)]


def _check(
    task: Task,
    fixture: str,
    lists: str,
    form: str,
    said: str,
    labelled: bool = True,
) -> tuple[bool | None, str]:
    printed = _printed(_fixture(fixture), lists, form, labelled)
    return scoring.hotspot_check(task, _events(printed, said))


# (task file, task id, fixture, the agent's words, lists that put the
# function in a window of five)
PASSING = [
    # ServeHTTP: rank 1 by total; not in the first five by self.
    (
        "tasks-go.json",
        "go-echo-hidden-fix",
        "echo-p",
        "`Echo.ServeHTTP` is where the request time goes.",
        ["total", "both"],
    ),
    # to_chars is rank 4 by total and rank 1 by self.
    (
        "tasks-native.json",
        "native-simdjson-hidden-fix",
        "sj-m",
        "`internal::to_chars` dominates the profile.",
        ["self", "total", "both"],
    ),
    # _view_wrapper is rank 1 by total and rank 3 by self. The
    # manifest's own user_passes_test is in neither list.
    (
        "tasks-real.json",
        "real-django-hidden-fix",
        "django-m200",
        "The per-request cost is `_view_wrapper`.",
        ["self", "total", "both"],
    ),
    # is_dask_collection has no self time; rank 2 by total.
    (
        "tasks-real.json",
        "real-xarray-hidden-fix",
        "xarray-wl",
        "`is_dask_collection` is called over and over.",
        ["total", "both"],
    ),
    # print_entry is rank 5 by self, rank 17 by total (thread entry
    # frames are above it).
    (
        "tasks-real.json",
        "real-fd-hidden-fix",
        "fd-m",
        "`output::print_entry` is the cost.",
        ["self", "both"],
    ),
]


@pytest.mark.parametrize("form", ["json", "lines"])
@pytest.mark.parametrize(
    ("file", "task_id", "fixture", "said", "lists"), PASSING
)
def test_the_known_function_is_found_in_the_list_that_ranks_it(
    file: str,
    task_id: str,
    fixture: str,
    said: str,
    lists: list[str],
    form: str,
) -> None:
    task = _task(file, task_id)
    for printed in ("self", "total", "both"):
        found, why = _check(task, fixture, printed, form, said)
        assert found is (printed in lists), (printed, why)


@pytest.mark.parametrize(
    ("file", "task_id", "fixture", "said", "lists"), PASSING
)
def test_an_unrelated_hotspot_named_as_the_cost_still_fails(
    file: str,
    task_id: str,
    fixture: str,
    said: str,
    lists: list[str],
) -> None:
    task = _task(file, task_id)
    found, why = _check(
        task, fixture, "both", "json", "`some_other_function` is the cost."
    )
    assert found is False
    assert "never named" in why


@pytest.mark.parametrize(
    ("file", "task_id", "fixture", "said", "lists"), PASSING
)
def test_the_function_absent_from_the_profile_fails_even_if_named(
    file: str,
    task_id: str,
    fixture: str,
    said: str,
    lists: list[str],
) -> None:
    task = _task(file, task_id)
    other = "xarray-wl" if fixture != "xarray-wl" else "echo-p"
    found, why = _check(task, other, "both", "json", said)
    assert found is False
    assert "is not in the profile's top" in why


def test_a_function_that_only_shares_the_name_is_not_the_hotspot() -> None:
    """The manifest names the type; another type's method is not it."""
    task = _task("tasks-go.json", "go-echo-hidden-fix")
    data = _fixture("echo-p")
    for key in ("hotspots", "frames_by_total"):
        for row in data[key]:
            row["function"] = row["function"].replace(
                "(*Echo).ServeHTTP", "(*Router).ServeHTTP"
            )
    printed = _lines(data["frames_by_total"])
    found, why = scoring.hotspot_check(
        task, _events(printed, "`ServeHTTP` is the cost.")
    )
    assert found is False
    assert "is not in the profile's top" in why


def test_accepted_functions_are_checked_against_their_file() -> None:
    task = _task("tasks-real.json", "real-django-hidden-fix")
    data = _fixture("django-m200")
    for row in data["frames_by_total"]:
        if row["function"] == "_view_wrapper":
            row["file"] = "django/contrib/admin/decorators.py"
    printed = _objects(data["frames_by_total"])
    found, _ = scoring.hotspot_check(
        task, _events(printed, "`_view_wrapper` is the cost.")
    )
    assert found is False


def test_print_entry_counts_only_where_the_manifest_accepts_it() -> None:
    task = _task("tasks-real.json", "real-fd-hidden-fix")
    assert _check(
        task, "fd-m", "both", "json", "`output::print_entry` is it."
    )[0]
    hotspot = task.expect.hotspot
    assert hotspot is not None
    assert [a.function for a in hotspot.accept] == ["fd::output::print_entry"]
    strict = task.model_copy(
        update={
            "expect": task.expect.model_copy(
                update={"hotspot": hotspot.model_copy(update={"accept": []})}
            )
        }
    )
    found, why = _check(
        strict, "fd-m", "both", "json", "`output::print_entry` is it."
    )
    assert found is False
    assert "output::print_entry_colorized is not in the" in why


def test_the_django_manifest_accepts_the_per_request_wrapper() -> None:
    task = _task("tasks-real.json", "real-django-hidden-fix")
    hotspot = task.expect.hotspot
    assert hotspot is not None
    assert hotspot.function == "user_passes_test"
    assert [(a.function, a.file) for a in hotspot.accept] == [
        ("_view_wrapper", "django/contrib/auth/decorators.py")
    ]
    assert all(a.why for a in hotspot.accept)


def test_an_accepted_function_must_say_why() -> None:
    with pytest.raises(ValueError, match="needs a why"):
        Hotspot(
            file="a.py",
            function="f",
            accept=[HotspotName(file="a.py", function="g")],
        )


def test_the_primary_function_stays_first_among_the_accepted() -> None:
    hotspot = Hotspot(
        file="a.py",
        function="f",
        accept=[HotspotName(file="b.py", function="g", why="inlined")],
    )
    assert [(a.function, a.file) for a in hotspot.accepted()] == [
        ("f", "a.py"),
        ("g", "b.py"),
    ]


def test_go_names_with_pointer_receivers_are_read_from_lines() -> None:
    frames = transcript.ranked_frames(
        "github.com/labstack/echo/v4.(*Echo).ServeHTTP /src/echo.go 663"
        " 0.01 0.9\n"
        "github.com/labstack/echo/v4.(*Router).Find /src/router.go 451"
        " 0.125 0.3\n"
    )
    assert [f.name for f in frames] == [
        "github.com/labstack/echo/v4.(*Echo).ServeHTTP",
        "github.com/labstack/echo/v4.(*Router).Find",
    ]
    assert frames[0].file == "/src/echo.go"
    assert frames[0].total == 0.9


def test_cpp_names_with_spaces_and_missing_files_are_read_from_lines() -> None:
    frames = transcript.ranked_frames(
        "simdjson::internal::to_chars(char*, char const*, double)"
        " src/to_chars.cpp 1169 0.209 0.382\n"
        "fd`fd::output::print_entry::ha19644765a9c40d9 None None 0.022"
        " 0.3\n"
    )
    assert [(f.name, f.file) for f in frames] == [
        (
            "simdjson::internal::to_chars(char*, char const*, double)",
            "src/to_chars.cpp",
        ),
        ("fd`fd::output::print_entry::ha19644765a9c40d9", None),
    ]


def test_interpreter_frames_with_a_space_in_the_file_are_skipped() -> None:
    frames = transcript.ranked_frames(
        "_call_with_frames_removed <frozen importlib._bootstrap> 228"
        " 0.0 0.9\n"
        "rolling_sum rolling.py 51 0.5 0.7\n"
    )
    assert [f.name for f in frames] == ["rolling_sum"]


def _frame(
    name: str, total: float, source: Literal["self", "total"], rank: int
) -> transcript.RankedFrame:
    return transcript.RankedFrame(name, "a.py", total, source, rank)


def test_the_window_is_the_first_n_of_each_printed_list() -> None:
    frames = [
        _frame("entry", 0.99, "total", 1),
        _frame("by_total", 0.8, "total", 2),
        _frame("middle", 0.5, "self", 1),
        _frame("by_self", 0.2, "self", 2),
        _frame("deep", 0.1, "self", 3),
        _frame("deeper", 0.05, "total", 3),
    ]
    window = transcript.window_frames(frames, 2)
    assert [f.name for f in window] == [
        "entry",
        "by_total",
        "middle",
        "by_self",
    ]


def test_a_total_list_is_never_ranked_by_self() -> None:
    """Frames 6th and 8th by total stay out of a window of five.

    Printed as the skill says, only ``frames_by_total`` (real xarray
    profile): the old window also took the five largest self shares
    among those rows and let ``_cmpkey`` and ``_parse_letter_version``
    in.
    """
    rows = _fixture("xarray-wl")["frames_by_total"]
    names = [r["function"] for r in rows if not r["file"].startswith("<")]
    assert {"_cmpkey", "_parse_letter_version"} <= set(names[5:])
    for labelled in (True, False):
        for form in ("json", "lines"):
            frames = transcript.ranked_frames(
                _printed(_fixture("xarray-wl"), "total", form, labelled)
            )
            window = [f.name for f in transcript.window_frames(frames, 5)]
            assert "is_dask_collection" in window
            assert "_cmpkey" not in window
            assert "_parse_letter_version" not in window
            assert window == names[:5]


def test_each_printed_list_gives_its_own_first_five_and_no_more() -> None:
    data = _fixture("django-m200")
    frames = transcript.ranked_frames(_printed(data, "both", "json"))
    window = {f.name for f in transcript.window_frames(frames, 5)}

    def first_five(key: str) -> set[str]:
        keep = [r for r in data[key] if not r["file"].startswith("<")]
        return {r["function"] for r in keep[:5]}

    assert window == first_five("hotspots") | first_five("frames_by_total")


def test_lists_printed_without_their_names_rank_by_total_only() -> None:
    """With no section name the output is one list ranked by total.

    print_entry is rank 5 by self and 17 by total: found only when the
    output says which list the rows came from.
    """
    task = _task("tasks-real.json", "real-fd-hidden-fix")
    said = "`output::print_entry` is the cost."
    for form in ("json", "lines"):
        found, _ = _check(task, "fd-m", "both", form, said, labelled=False)
        assert found is False
        found, why = _check(task, "fd-m", "both", form, said, labelled=True)
        assert found is True, why


def test_a_frame_without_a_file_needs_an_owner_in_the_manifest() -> None:
    task = _task("tasks-real.json", "real-fd-hidden-fix")
    hotspot = task.expect.hotspot
    assert hotspot is not None
    bare = Hotspot(file="src/output.rs", function="print_entry", top_n=5)
    said = "`print_entry` is the cost."
    printed = _printed(_fixture("fd-m"), "self", "json")
    other = task.model_copy(
        update={"expect": task.expect.model_copy(update={"hotspot": bare})}
    )
    found, _ = scoring.hotspot_check(other, _events(printed, said))
    assert found is False


def test_an_owner_elsewhere_is_not_the_manifests_function() -> None:
    task = _task("tasks-real.json", "real-fd-hidden-fix")
    data = _fixture("fd-m")
    for row in data["hotspots"]:
        row["function"] = row["function"].replace(
            "fd::output::", "other::output::"
        )
    printed = _printed(data, "self", "json")
    found, _ = scoring.hotspot_check(
        task, _events(printed, "`output::print_entry` is the cost.")
    )
    assert found is False


def test_the_native_manifest_names_its_owner() -> None:
    task = _task("tasks-native.json", "native-simdjson-hidden-fix")
    hotspot = task.expect.hotspot
    assert hotspot is not None
    assert hotspot.function == "simdjson::internal::to_chars"
    for wrong in ("std::__1", "fast_float"):
        data = _fixture("sj-m")
        for key in ("hotspots", "frames_by_total"):
            for row in data[key]:
                row["function"] = row["function"].replace(
                    "simdjson::internal::to_chars",
                    f"{wrong}::to_chars",
                )
        found, _ = scoring.hotspot_check(
            task,
            _events(
                _printed(data, "both", "json"), "`internal::to_chars` it is."
            ),
        )
        assert found is False


def test_the_bare_name_does_not_name_a_qualified_manifest_function() -> None:
    task = _task("tasks-go.json", "go-echo-hidden-fix")
    for said, named in [
        ("`ServeHTTP` is slow.", False),
        ("The `Router.ServeHTTP` path is slow.", False),
        ("`Echo.ServeHTTP` is slow.", True),
        ("`(*Echo).ServeHTTP` is slow.", True),
        ("echo.Echo.ServeHTTP is where the time goes.", True),
    ]:
        found, why = _check(task, "echo-p", "total", "json", said)
        assert found is named, (said, why)


def test_an_accepted_name_names_only_the_function_that_matched() -> None:
    """django: the profile has _view_wrapper, not user_passes_test."""
    task = _task("tasks-real.json", "real-django-hidden-fix")
    found, why = _check(
        task,
        "django-m200",
        "total",
        "json",
        "`user_passes_test` is the cost.",
    )
    assert found is False
    assert "never named" in why
    found, why = _check(
        task, "django-m200", "total", "json", "`_view_wrapper` is the cost."
    )
    assert found is True, why


def test_rank_one_is_by_total_share_only() -> None:
    """A top-1 task is not passed by a function first only by self."""
    task = _task("tasks.json", "find-rolling-light")
    printed = "main bench.py 1 0.0 0.99\nrolling_sum rolling.py 51 0.8 0.9\n"
    found, why = scoring.hotspot_check(
        task, _events(printed, "`rolling_sum` is the cost.")
    )
    assert found is False
    assert why == "profile ranks main first"


def _kept(rows: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    """The rows the reader keeps: no interpreter frames, no repeats."""
    pairs = [
        (r["function"], r["file"])
        for r in rows
        if not (r["file"] or "").startswith("<")
        and not r["function"].startswith("<")
    ]
    return list(dict.fromkeys(pairs))


@pytest.mark.parametrize(
    "fixture", ["echo-p", "sj-m", "django-m200", "xarray-wl", "fd-m"]
)
def test_the_cli_text_output_is_read_as_two_lists(fixture: str) -> None:
    """The CLI's own tables, under its two titles, one frame per row."""
    data = _fixture(fixture)
    text = json.loads((PROBE / f"{fixture}.json").read_text())["cli_text"]
    frames = transcript.ranked_frames(text)
    for source, key in (("self", "hotspots"), ("total", "frames_by_total")):
        got = sorted(
            (f for f in frames if f.source == source), key=lambda f: f.rank
        )
        want = _kept(data[key])
        assert [(f.name, f.file) for f in got] == want
        assert [f.rank for f in got] == list(range(1, len(want) + 1))


def test_cli_text_rows_give_file_and_total() -> None:
    text = (
        "Top frames by self time (share of the profiler's samples):\n"
        "   self  total  location  function\n"
        "   5.4%  74.9%  django/contrib/auth/decorators.py:54  "
        "_view_wrapper\n"
        "  33.3%  33.3%  -  libsystem_kernel.dylib`read  (library)\n"
        "   1.0%         x.py  nototal\n"
    )
    frames = transcript.ranked_frames(text)
    assert [(f.name, f.file, f.source) for f in frames] == [
        ("_view_wrapper", "django/contrib/auth/decorators.py", "self"),
        ("libsystem_kernel.dylib`read", None, "self"),
        ("nototal", "x.py", "self"),
    ]
    assert [f.total for f in frames] == pytest.approx([0.749, 0.333, 0.01])


def test_print_entry_passes_from_the_cli_text_with_nothing_else() -> None:
    """self rank 5, total rank 17: found from the CLI's own tables."""
    task = _task("tasks-real.json", "real-fd-hidden-fix")
    text = json.loads((PROBE / "fd-m.json").read_text())["cli_text"]
    found, why = scoring.hotspot_check(
        task, _events(text, "`output::print_entry` is the cost.")
    )
    assert found is True, why


@pytest.mark.parametrize("dump", ["inline", "per_line"])
def test_a_list_of_key_names_is_not_a_section_heading(dump: str) -> None:
    """``list(r)`` names both lists; it must not label rows below."""
    keys = ["argv", "basis", "folded", "frames_by_total", "hotspots", "name"]
    head = str(keys) if dump == "inline" else "\n".join(keys)
    rows = _lines(_fixture("fd-m")["hotspots"])
    frames = transcript.ranked_frames(head + "\n" + rows)
    assert frames
    assert {f.source for f in frames} == {None}


@pytest.mark.parametrize(
    "heading",
    [
        "hotspots",
        "hotspots:\nfunction file line self total",
        "hotspots\n--------\nfunction file line self total",
        "hotspots:\nfunction file line self total\n(top 9)",
        "| hotspots |\n|---|\nfunction file line self total",
    ],
)
def test_a_heading_with_other_lines_before_its_rows_still_labels(
    heading: str,
) -> None:
    """A taller heading does not unlabel the list."""
    rows = [f"f{i} a.py 1 0.{9 - i} 0.2" for i in range(8)]
    rows.append("tgt a.py 1 0.01 0.95")
    frames = transcript.ranked_frames(heading + "\n" + "\n".join(rows))
    assert {f.source for f in frames} == {"self"}
    window = transcript.window_frames(frames, 5)
    assert "tgt" not in {f.name for f in window}
    assert next(f for f in frames if f.name == "tgt").rank == 9


@pytest.mark.parametrize(
    "head",
    [
        "hotspots, frames_by_total",
        "hotspots frames_by_total",
        "['argv', 'hotspots']",
        "dict_keys(['hotspots', 'name'])",
        "argv\nhotspots\nname",
    ],
)
def test_lists_of_names_do_not_label_the_rows_after_them(head: str) -> None:
    rows = "f0 a.py 1 0.5 0.6\nf1 a.py 1 0.4 0.5"
    frames = transcript.ranked_frames(head + "\n" + rows)
    assert {f.source for f in frames} == {None}


def test_cli_rows_with_percentages_over_100_are_not_frames() -> None:
    text = "   5000%  x.py  foo\n   50.0%  60.0%  y.py:3  bar\n"
    assert [f.name for f in transcript.ranked_frames(text)] == ["bar"]


def _list_with_the_word_in_row_six(form: str) -> str:
    """A total list: row six mentions ``hotspots``, target is ninth."""
    names = [f"f{i}" for i in range(8)]
    names.insert(8, "tgt")
    out = ["frames_by_total:"]
    for i, name in enumerate(names):
        file, func = "a.py", name
        if i == 5:
            if form == "path":
                file = "src/hotspots/x.py"
            elif form == "function":
                func = "Profiler.hotspots"
            else:
                file = "pkg/hotspots/core.py"
        total = round(0.9 - i * 0.05, 2)
        if form == "json":
            out.append(
                json.dumps(
                    {
                        "function": func,
                        "file": file,
                        "line": 1,
                        "self_fraction": 0.0,
                        "total_fraction": total,
                    }
                )
            )
        else:
            out.append(f"{func} {file} 1 0.0 {total}")
    return "\n".join(out)


@pytest.mark.parametrize("form", ["path", "function", "json"])
def test_the_word_inside_a_row_is_not_a_heading(form: str) -> None:
    """A row naming ``hotspots`` must not restart the ranks."""
    frames = transcript.ranked_frames(_list_with_the_word_in_row_six(form))
    assert {f.source for f in frames} == {"total"}
    assert next(f for f in frames if f.name == "tgt").rank == 9
    window = transcript.window_frames(frames, 5)
    assert "tgt" not in {f.name for f in window}


@pytest.mark.parametrize("form", ["path", "function", "json"])
def test_a_function_in_a_hotspots_path_does_not_pass_the_check(
    form: str,
) -> None:
    task = _task("tasks-real.json", "real-xarray-hidden-fix")
    printed = (
        _list_with_the_word_in_row_six(form)
        .replace("tgt a.py", "is_dask_collection xarray/core/pycompat.py")
        .replace(
            '"function": "tgt", "file": "a.py"',
            '"function": "is_dask_collection", '
            '"file": "xarray/core/pycompat.py"',
        )
    )
    found, why = scoring.hotspot_check(
        task, _events(printed, "`is_dask_collection` is the cost.")
    )
    assert found is False, why
    assert "is not in the profile's top" in why


def test_a_key_or_a_lone_name_is_still_a_heading() -> None:
    rows = "f0 a.py 1 0.5 0.6"
    for head in (
        '{"hotspots": [',
        "`hotspots`",
        "result.hotspots[]:",
        "|hotspots|",
    ):
        frames = transcript.ranked_frames(head + "\n" + rows)
        assert {f.source for f in frames} == {"self"}, head
