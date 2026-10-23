"""Deterministic CPU-bound Python target for the golden set.

Builds a fixed order book from an integer LCG, screens it against a
blocklist held in a list, sorts the held orders and prints a checksum.
Derived in spirit from the T2.11 membership target, sized to about
0.1 s of work.
"""

import sys

N = 60000
BLOCKED = 150


def main() -> None:
    """Run the workload once and print its checksum."""
    state = 12345
    customers: list[int] = []
    for _ in range(N):
        state = (state * 1103515245 + 12345) & 0xFFFFFFFF
        customers.append(state % 100000)
    blocklist = [(i * 7919) % 100000 for i in range(BLOCKED)]
    held = [c for c in customers if c in blocklist]
    held.sort()
    total = 0
    for c in held:
        total = (total * 31 + c) & 0xFFFFFFFF
    sys.stdout.write(f"checksum {total} {len(held)}\n")


if __name__ == "__main__":
    main()
