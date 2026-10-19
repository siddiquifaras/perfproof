"""The writer's blocks and its setting."""

import pytest

import writer
from writer import Writer


def test_every_character_is_written_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(writer, "LATENCY_SECONDS", 0.0)
    out = Writer(3)
    for i in range(7):
        out.add(str(i))
    out.flush()
    assert out.written == sum(len(str(i)) + 1 for i in range(7))


def test_environment_setting_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLUSH_EVERY", "5")
    calls: list[str] = []

    def record(block: str) -> int:
        calls.append(block)
        return 0

    monkeypatch.setattr(writer, "write_block", record)
    out = Writer.from_environment()
    for i in range(10):
        out.add(str(i))
    assert len(calls) == 2


def test_flush_every_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        Writer(0)
