"""Name matching for the hotspot check, on names profilers printed.

The strings that should match are names printed by the hidden-fix
probe's real profiles (``fixtures/hiddenfix-probe``) or the real
folded stacks beside them. The must-not-match strings are constructed
to share a suffix or a prefix with them.
"""

import pytest

from hotspot_names import ShortName, files_match, names_match, short_name

CPP = "simdjson::internal::to_chars(char*, char const*, double)"
CPP_MACHO = "eval_to_chars`" + CPP
GO_ECHO = "github.com/labstack/echo/v4.(*Echo).ServeHTTP"
GO_ROUTER = "github.com/labstack/echo/v4.(*Router).ServeHTTP"
RUST = "fd`fd::output::print_entry::ha19644765a9c40d9"
CPP_WANTED = "simdjson::internal::to_chars"
RUST_WANTED = "fd::output::print_entry"
RUST_COLORIZED = "fd`fd::output::print_entry_colorized::h0123456789abcdef"


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        (CPP, ShortName(("simdjson", "internal"), "to_chars")),
        (CPP_MACHO, ShortName(("simdjson", "internal"), "to_chars")),
        (GO_ECHO, ShortName(("v4", "Echo"), "ServeHTTP")),
        (
            "github.com/labstack/echo/v4.(*Router).Find",
            ShortName(("v4", "Router"), "Find"),
        ),
        ("net/url.escape", ShortName(("url",), "escape")),
        (RUST, ShortName(("fd", "output"), "print_entry")),
        (
            "eval_to_chars`simdjson::internal::string_builder"
            "<simdjson::internal::mini_formatter>::append"
            "(simdjson::dom::element)",
            ShortName(("simdjson", "internal", "string_builder"), "append"),
        ),
        (
            "NumpyIndexingAdapter.__getitem__",
            ShortName(("NumpyIndexingAdapter",), "__getitem__"),
        ),
        ("_view_wrapper", ShortName((), "_view_wrapper")),
        ("Echo.ServeHTTP", ShortName(("Echo",), "ServeHTTP")),
        ("String#blank?", ShortName(("String",), "blank?", True)),
        ("String.blank?", ShortName(("String",), "blank?", False)),
        ("Array#[]", ShortName(("Array",), "[]", True)),
        (
            "<fd::Foo as core::fmt::Display>::fmt",
            ShortName(("<Foo as Display>",), "fmt"),
        ),
        ("make.<locals>.inner", ShortName(("make",), "inner")),
        (
            "std::function<void ()>::operator()(int) const",
            ShortName(("std", "function"), "operator_40_41"),
        ),
        ("foo(int) [clone .cold]", ShortName((), "foo")),
        (
            "echo/v4.(*Echo).ServeHTTP-fm",
            ShortName(("v4", "Echo"), "ServeHTTP"),
        ),
    ],
)
def test_short_name_of_what_profilers_print(
    printed: str, expected: ShortName
) -> None:
    assert short_name(printed) == expected


@pytest.mark.parametrize(
    ("printed", "wanted"),
    [
        (CPP, "to_chars"),
        (CPP, "simdjson::internal::to_chars"),
        (CPP_MACHO, "simdjson::internal::to_chars"),
        (GO_ECHO, "Echo.ServeHTTP"),
        (GO_ECHO, "ServeHTTP"),
        (RUST, "print_entry"),
        (RUST, "fd::output::print_entry"),
        (RUST, "output::print_entry"),
        ("fd::output::print_entry::ha19644765a9c40d9", "print_entry"),
        ("<fd::Foo as core::fmt::Display>::fmt", "<Foo as Display>::fmt"),
        ("A::Foo::bar", "Foo::bar"),
        ("_view_wrapper", "_view_wrapper"),
        ("is_dask_collection", "is_dask_collection"),
        ("String#blank?", "String#blank?"),
        (
            "Markdig.Parsers.InlineProcessor.ProcessInlineLeaf",
            "Markdig.Parsers.InlineProcessor.ProcessInlineLeaf",
        ),
    ],
)
def test_the_manifests_short_name_matches_the_printed_one(
    printed: str, wanted: str
) -> None:
    assert names_match(printed, wanted)


@pytest.mark.parametrize(
    ("printed", "wanted"),
    [
        # A different function that shares a suffix or a prefix.
        ("simdjson::internal::from_chars(char const*, double&)", "to_chars"),
        ("simdjson::internal::to_chars_len(double)", "to_chars"),
        (
            "simdjson::internal::dtoa_impl::to_decimal(unsigned long long)",
            "to_chars",
        ),
        (
            "fd`fd::output::print_entry_colorized::h0123456789abcdef",
            "print_entry",
        ),
        (RUST, "print_entry_colorized"),
        (
            "fd`fd::output::print_entry_uncolorized::h0123456789abcdef",
            "print_entry",
        ),
        ("my_to_chars", "to_chars"),
        ("__view_wrapper", "_view_wrapper"),
        ("_view_wrapper_async", "_view_wrapper"),
        # An owner path elsewhere, or only partly the same.
        ("std::__1::to_chars(char*, char*, double)", CPP_WANTED),
        ("fast_float::to_chars(char*, char*, double)", CPP_WANTED),
        ("other::output::print_entry::h0123456789abcdef", RUST_WANTED),
        ("B::Foo::bar", "A::Foo::bar"),
        ("Foo::bar", "A::Foo::bar"),
        # Ruby: a class method is not the instance method.
        ("String.blank?", "String#blank?"),
        ("String#blank?", "String.blank?"),
        # A trait impl matches only that impl.
        ("<fd::Foo as core::fmt::Display>::fmt", "Foo::fmt"),
        ("<fd::Foo as core::fmt::Debug>::fmt", "<Foo as Display>::fmt"),
        # Another type's method of the same name, when the manifest
        # names the type.
        (GO_ROUTER, "Echo.ServeHTTP"),
        ("github.com/labstack/echo/v4.(*Group).ServeHTTP", "Echo.ServeHTTP"),
        ("Router.ServeHTTP", "Echo.ServeHTTP"),
        # No receiver printed: cannot be confirmed against one.
        ("ServeHTTP", "Echo.ServeHTTP"),
        ("Object#blank?", "String#blank?"),
        # Comes from the wrong side of the dot.
        ("ServeHTTP.Echo", "Echo.ServeHTTP"),
    ],
)
def test_a_function_that_only_shares_a_suffix_does_not_match(
    printed: str, wanted: str
) -> None:
    assert not names_match(printed, wanted)


def test_nothing_left_after_stripping_matches_nothing() -> None:
    assert short_name("") is None
    assert short_name("`") is None
    assert not names_match("", "to_chars")
    assert not names_match("to_chars", "")


@pytest.mark.parametrize(
    ("printed", "wanted", "agree"),
    [
        ("/src/echo.go", "echo.go", True),
        ("echo.go", "echo.go", True),
        ("src/to_chars.cpp", "src/to_chars.cpp", True),
        (
            "/work/repo/django/contrib/auth/decorators.py",
            "django/contrib/auth/decorators.py",
            True,
        ),
        ("decorators.py", "django/contrib/auth/decorators.py", True),
        (None, "src/output.rs", True),
        (
            "django/contrib/admin/decorators.py",
            "django/contrib/auth/decorators.py",
            False,
        ),
        ("/src/router.go", "echo.go", False),
        ("other/decorators.pyc", "django/contrib/auth/decorators.py", False),
        ("", "echo.go", False),
    ],
)
def test_files_agree_by_their_trailing_components(
    printed: str | None, wanted: str, agree: bool
) -> None:
    assert files_match(printed, wanted) is agree
