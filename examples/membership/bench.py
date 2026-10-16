"""Benchmark `orders.held_orders` on a fixed synthetic order book.

Run as ``python bench.py [--output PATH]`` from this directory. It
times one screening of every order with `time.perf_counter` and prints
``PERFPROOF {"value": <seconds>}`` on stdout, so the same command
serves a ``reported`` metric (the call alone) and a ``wall`` metric
(the whole process). With ``--output`` it also writes the held order
IDs, one per line, outside the timed region, for output comparison.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from orders import Order, held_orders

ORDERS = 40_000
"""Number of orders screened."""
BLOCKED = 2_000
"""Number of distinct blocked customers."""
REPEATS = 30
"""Blocklist entries that repeat an ID already on it."""
BLOCKED_SHARE = 0.05
"""Share of orders placed by blocked customers."""
CUSTOMER_IDS = 2**24
"""Customer IDs are drawn from 0 to ``CUSTOMER_IDS - 1``."""
SEED = 20261016
"""Seed of the generator that draws the order book."""


def make_book() -> tuple[list[Order], list[int]]:
    """Draw the benchmark orders and blocklist.

    Returns
    -------
    orders : list of Order
        ``ORDERS`` orders, about ``BLOCKED_SHARE`` of them from blocked
        customers, the same on every run.
    blocklist : list of int
        ``BLOCKED`` distinct customer IDs plus ``REPEATS`` repeats, in
        shuffled order.
    """
    rng = np.random.default_rng(SEED)
    distinct = rng.choice(CUSTOMER_IDS, BLOCKED, replace=False)
    blocklist = np.concatenate([distinct, rng.choice(distinct, REPEATS)])
    rng.shuffle(blocklist)
    customers = rng.integers(0, CUSTOMER_IDS, ORDERS)
    from_blocked = rng.random(ORDERS) < BLOCKED_SHARE
    customers[from_blocked] = rng.choice(distinct, int(from_blocked.sum()))
    amounts = rng.integers(100, 100_000, ORDERS)
    orders = [
        Order(order_id=i, customer_id=c, amount_cents=a)
        for i, (c, a) in enumerate(
            zip(customers.tolist(), amounts.tolist(), strict=True)
        )
    ]
    return orders, blocklist.tolist()


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark once and report its time.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; ``sys.argv[1:]`` when omitted.

    Returns
    -------
    int
        Exit status, 0 on success.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write held order IDs")
    args = parser.parse_args(argv)
    orders, blocklist = make_book()
    start = time.perf_counter()
    held = held_orders(orders, blocklist)
    elapsed = time.perf_counter() - start
    if args.output is not None:
        output: Path = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("".join(f"{o.order_id}\n" for o in held))
    sys.stdout.write(f"PERFPROOF {json.dumps({'value': elapsed})}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
