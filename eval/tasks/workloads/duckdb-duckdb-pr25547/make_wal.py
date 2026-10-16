"""Write the SQL that leaves a DuckDB database with a long WAL.

Writes ``eval_wal.sql`` in the current directory: checkpoints turned
off, one table, then 20,000 one-row transactions. Run against a fresh
``eval.db`` it leaves every transaction in the WAL, so opening the
database replays them, the path the reference change shortens
(BeginTransaction and Commit per replayed transaction), as in the
reference pull request's own benchmark.
"""

from pathlib import Path

lines = [
    "PRAGMA disable_checkpoint_on_shutdown;",
    "SET wal_autocheckpoint = '1TB';",
    "CREATE TABLE t(a INTEGER, b VARCHAR);",
]
# The values are loop integers written into a fixed test script, not
# input from anywhere.
lines += [
    f"BEGIN; INSERT INTO t VALUES ({i}, 'row_{i}'); COMMIT;"  # noqa: S608
    for i in range(20000)
]
Path("eval_wal.sql").write_text("\n".join(lines) + "\n")
