"""Work the tests exercise."""


def checksum(n: int) -> int:
    """Return a checksum that costs time proportional to ``n``."""
    total = 0
    for i in range(n):
        total += i * i % 7
    return total


def cheap() -> int:
    """Return at once."""
    return 1
