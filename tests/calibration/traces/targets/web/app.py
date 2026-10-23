# pyright: basic, reportMissingImports=false
"""A small FastAPI app with three endpoints, the web trace target."""

import hashlib
import random
import sqlite3

from fastapi import FastAPI, Request

app = FastAPI()
_db = sqlite3.connect(":memory:", check_same_thread=False)


def _seed() -> None:
    rng = random.Random(20261201)  # noqa: S311
    _db.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer INTEGER,"
        " amount REAL)"
    )
    _db.execute("CREATE INDEX orders_customer ON orders (customer)")
    _db.executemany(
        "INSERT INTO orders (customer, amount) VALUES (?, ?)",
        [(rng.randrange(500), rng.random() * 100) for _ in range(20000)],
    )
    _db.commit()


_seed()


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the server is up."""
    return {"status": "ok"}


@app.post("/echo")
async def echo(request: Request) -> dict[str, object]:
    """Return the posted JSON with its key count."""
    body = await request.json()
    return {"keys": len(body), "body": body}


@app.get("/cpu/{n}")
def cpu(n: int) -> dict[str, str]:
    """Hash chain of ``n`` rounds, a CPU-bound handler."""
    h = b"seed"
    for _ in range(n):
        h = hashlib.sha256(h).digest()
    return {"digest": h.hex()}


@app.get("/db/{customer}")
async def db(customer: int) -> dict[str, object]:
    """Order count and total for one customer, from SQLite."""
    row = _db.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM orders"
        " WHERE customer = ?",
        (customer % 500,),
    ).fetchone()
    return {"orders": row[0], "total": round(row[1], 2)}
