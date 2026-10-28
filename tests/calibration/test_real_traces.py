"""Unit tests of the real-trace loader and replicate builder."""

import gzip
import json
import math
from pathlib import Path

import numpy as np
import pytest
import real_traces as rt


def _write(path: Path, pairs: int) -> None:
    rng = np.random.default_rng(1)
    lines: list[str] = []
    for p in range(-1, pairs):
        b_first = (p // 2 % 2 == 0) == (p % 2 == 0)
        for pos in (0, 1):
            arm = "B" if (pos == 0) == b_first else "A"
            seconds = 1.0 + 0.01 * rng.standard_normal()
            lines.append(
                json.dumps(
                    {
                        "pair": p,
                        "warmup": p < 0,
                        "pos": pos,
                        "arm": arm,
                        "wall_s": seconds,
                        "reported_s": seconds * 0.5 if arm == "A" else None,
                        "load1": 1.0,
                    }
                )
            )
    path.write_text("\n".join(lines) + "\n")


def test_loader_drops_warmup_and_prefers_reported_time(
    tmp_path: Path,
) -> None:
    path = tmp_path / "linux-x.jsonl"
    _write(path, 40)
    trace = rt.load_trace(path)
    assert trace.pairs == 40
    assert trace.name == "linux-x"
    # arm A reports half its wall time, B has no reported time, so d is
    # about log(0.5).
    assert abs(trace.d.mean() - math.log(0.5)) < 0.01
    assert np.all(trace.x == 0)


def test_blocks_keep_order_pairs_and_replicates_have_exact_speedup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "linux-y.jsonl"
    _write(path, 60)
    trace = rt.load_trace(path)
    rng = np.random.default_rng(2)
    idx = rt.replicate_indexes(trace, 6, 50, 30, rng)
    assert idx.shape == (50, 30)
    assert np.all(idx[:, 0::2] % 2 == 0)
    assert np.all(idx[:, 1::2] == idx[:, 0::2] + 1)
    log_times, first, x = rt.replicates(trace, 6, 1.05, 2000, 30, rng)
    assert log_times.shape == (2000, 30, 2)
    assert first.shape == x.shape == (2000, 30)
    d = log_times[..., 0] - log_times[..., 1]
    assert abs(d.mean() - math.log(1.05)) < 0.01
    flags = first.reshape(2000, 15, 2)
    assert np.all(flags[:, :, 0] != flags[:, :, 1])


def test_odd_or_oversized_blocks_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "linux-z.jsonl"
    _write(path, 20)
    trace = rt.load_trace(path)
    rng = np.random.default_rng(3)
    with pytest.raises(ValueError, match="even"):
        rt.replicate_indexes(trace, 5, 2, 10, rng)
    with pytest.raises(ValueError, match="exceeds"):
        rt.replicate_indexes(trace, 40, 2, 10, rng)


def test_committed_traces_load_and_include_picolm() -> None:
    traces = {t.name: t for t in rt.load_all()}
    assert "linux-picolm" in traces
    assert traces["linux-picolm"].pairs == 1000
    assert not any(rt.EXCLUDED_SUFFIX in name for name in traces)
    assert all(t.pairs >= 2 for t in traces.values())


def test_missing_traces_fail_with_a_clear_message(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no A/A traces in"):
        rt.load_all(tmp_path)
    with pytest.raises(FileNotFoundError, match="no A/A traces in"):
        rt.load_all(tmp_path / "absent")


def test_loader_reads_gzip(tmp_path: Path) -> None:
    plain = tmp_path / "linux-g.jsonl"
    _write(plain, 20)
    packed = tmp_path / "linux-h.jsonl.gz"
    packed.write_bytes(gzip.compress(plain.read_bytes()))
    a, b = rt.load_trace(plain), rt.load_trace(packed)
    assert b.name == "linux-h"
    assert np.array_equal(a.d, b.d)
