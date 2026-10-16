import json
from pathlib import Path
from typing import Any

import pytest

from perfproof.bench.spec import (
    SpecConflictError,
    SpecRecordError,
    command_hash,
    load_spec,
    normalize_launch_path,
    save_spec,
    spec_command_hash,
    spec_record_path,
)
from perfproof.schema import BenchmarkSpec, LockedFile, LockManifest

SHA_A = "a" * 64
SHA_B = "b" * 64


def make_spec(**overrides: Any) -> BenchmarkSpec:
    fields: dict[str, Any] = {
        "name": "bench-tile",
        "command": ("python", "bench.py"),
        "metric": {
            "kind": "wall",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": {
            "command": ("python", "-m", "pytest"),
            "timeout_seconds": 60.0,
        },
        "warmup": 2,
        "budget": {"max_rounds": 40, "max_seconds": 600.0},
        "looks": {"n0": 10, "k": 10, "max_looks": 4},
        "timeout_seconds": 30.0,
        "harness": ("bench.py",),
        "oracle": ("tests",),
        "inputs": ("data",),
    }
    fields.update(overrides)
    return BenchmarkSpec.model_validate(fields)


def make_manifest(spec: BenchmarkSpec, sha: str = SHA_A) -> LockManifest:
    return LockManifest(
        spec_hash=spec.spec_hash,
        files=(
            LockedFile(
                path="bench.py", role="harness", sha256=sha, size_bytes=3
            ),
        ),
    )


def test_saved_record_round_trips_under_its_hash(tmp_path: Path) -> None:
    spec = make_spec()
    manifest = make_manifest(spec)

    path = save_spec(tmp_path, spec, manifest)

    assert path == (
        tmp_path
        / ".perfproof"
        / "benchmarks"
        / "bench-tile"
        / f"{spec.spec_hash}.json"
    )
    assert path == spec_record_path(tmp_path, spec.name, spec.spec_hash)
    assert load_spec(tmp_path, "bench-tile", spec.spec_hash) == (
        spec,
        manifest,
    )


def test_saving_the_same_record_again_leaves_the_file(tmp_path: Path) -> None:
    spec = make_spec()
    path = save_spec(tmp_path, spec, make_manifest(spec))
    before = path.read_bytes()
    stat = path.stat()

    assert save_spec(tmp_path, spec, make_manifest(spec)) == path

    assert path.read_bytes() == before
    assert path.stat().st_ino == stat.st_ino
    assert path.stat().st_mtime_ns == stat.st_mtime_ns


def test_changed_spec_field_is_a_new_spec_beside_the_old(
    tmp_path: Path,
) -> None:
    old = make_spec()
    new = make_spec(warmup=3)
    old_path = save_spec(tmp_path, old, make_manifest(old))
    old_bytes = old_path.read_bytes()

    new_path = save_spec(tmp_path, new, make_manifest(new))

    assert new.spec_hash != old.spec_hash
    assert new_path != old_path
    assert new_path.parent == old_path.parent
    assert old_path.read_bytes() == old_bytes
    assert load_spec(tmp_path, "bench-tile", old.spec_hash)[0] == old
    assert load_spec(tmp_path, "bench-tile", new.spec_hash)[0] == new


def test_a_frozen_record_is_never_overwritten(tmp_path: Path) -> None:
    spec = make_spec()
    path = save_spec(tmp_path, spec, make_manifest(spec, SHA_A))
    before = path.read_bytes()

    with pytest.raises(SpecConflictError, match="frozen"):
        save_spec(tmp_path, spec, make_manifest(spec, SHA_B))

    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_manifest_of_another_spec_is_refused(tmp_path: Path) -> None:
    spec = make_spec()
    other = make_spec(warmup=5)

    with pytest.raises(ValueError, match="belongs to spec"):
        save_spec(tmp_path, spec, make_manifest(other))

    assert not (tmp_path / ".perfproof").exists()


def test_missing_record_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_spec(tmp_path, "bench-tile", SHA_A)


def test_edited_record_is_refused_on_load(tmp_path: Path) -> None:
    spec = make_spec()
    path = save_spec(tmp_path, spec, make_manifest(spec))
    record = json.loads(path.read_text())
    record["spec"]["warmup"] = 9
    path.write_text(json.dumps(record))

    with pytest.raises(SpecRecordError, match="spec_hash"):
        load_spec(tmp_path, "bench-tile", spec.spec_hash)


def test_record_under_the_wrong_name_or_hash_is_refused(
    tmp_path: Path,
) -> None:
    spec = make_spec()
    other = make_spec(warmup=4)
    path = save_spec(tmp_path, spec, make_manifest(spec))
    wrong_hash = path.with_name(f"{other.spec_hash}.json")
    path.rename(wrong_hash)

    with pytest.raises(SpecRecordError, match="hash"):
        load_spec(tmp_path, "bench-tile", other.spec_hash)

    renamed = tmp_path / ".perfproof" / "benchmarks" / "other"
    renamed.mkdir()
    wrong_hash.rename(renamed / f"{spec.spec_hash}.json")
    with pytest.raises(SpecRecordError, match="name"):
        load_spec(tmp_path, "other", spec.spec_hash)


def test_record_whose_lock_names_another_spec_is_refused(
    tmp_path: Path,
) -> None:
    spec = make_spec()
    path = save_spec(tmp_path, spec, make_manifest(spec))
    record = json.loads(path.read_text())
    record["lock"]["spec_hash"] = SHA_B
    path.write_text(json.dumps(record))

    with pytest.raises(SpecRecordError, match="lock"):
        load_spec(tmp_path, "bench-tile", spec.spec_hash)


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "-rf"])
def test_unsafe_names_and_hashes_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError, match="not a"):
        spec_record_path(tmp_path, bad, SHA_A)
    with pytest.raises(ValueError, match="not a"):
        spec_record_path(tmp_path, "bench-tile", bad)


def test_command_hash_depends_on_every_launch_field() -> None:
    base = command_hash(("python", "b.py"), {"A": "1", "B": "2"}, ".", "py")

    assert base == command_hash(
        ["python", "b.py"], {"B": "2", "A": "1"}, ".", "py"
    )
    variants = {
        command_hash(("python", "c.py"), {"A": "1", "B": "2"}, ".", "py"),
        command_hash(("python", "b.py"), {"A": "1", "B": "3"}, ".", "py"),
        command_hash(("python", "b.py"), {"A": "1"}, ".", "py"),
        command_hash(("python", "b.py"), {"A": "1", "B": "2"}, "sub", "py"),
        command_hash(("python", "b.py"), {"A": "1", "B": "2"}, ".", "py3"),
    }
    assert base not in variants
    assert len(variants) == 5


def test_command_hash_does_not_merge_argv_elements() -> None:
    assert command_hash(("a b",), {}, ".", "x") != command_hash(
        ("a", "b"), {}, ".", "x"
    )


def test_spec_command_hash_covers_declared_launch_only() -> None:
    spec = make_spec(env={"OMP_NUM_THREADS": "1"}, cwd="sub")

    assert spec_command_hash(spec, ".venv/bin/python") == command_hash(
        ("python", "bench.py"),
        {"OMP_NUM_THREADS": "1"},
        "sub",
        ".venv/bin/python",
    )
    assert spec_command_hash(make_spec(warmup=7), "x") == spec_command_hash(
        make_spec(), "x"
    )


def test_launch_paths_inside_the_tree_are_tree_relative(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "tree"
    (tree / ".venv" / "bin").mkdir(parents=True)
    exe = tree / ".venv" / "bin" / "python"
    exe.touch()

    assert normalize_launch_path(exe, tree) == ".venv/bin/python"
    assert normalize_launch_path(tree, tree) == "."
    outside = tmp_path / "elsewhere" / "python"
    assert normalize_launch_path(outside, tree) == str(outside.resolve())


def test_relative_launch_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        normalize_launch_path(Path("bin/python"), tmp_path)
