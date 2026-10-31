from app.work import cheap, checksum


def test_checksum() -> None:
    assert checksum(400_000) > 0


def test_cheap() -> None:
    assert cheap() == 1
