"""Apply the margin rule of PREREGISTRATION.md to a sweep output.

The rule: the smallest margin whose CP95 upper bound is at most 2.5% on
every row of the sharp pairs and the exponential tails, at every noise
0.1, 0.25, 0.5, 1.0, 1.5 and 2.0, over all comparisons. Prints the worst
bound per noise and margin, then the pick, or the closest margin when
none qualifies.

Usage: python pick_margin.py out/rule-sweep-scaled.txt
"""

import sys
from pathlib import Path

from _out import emit

NOISES = (0.1, 0.25, 0.5, 1.0, 1.5, 2.0)
CLAIMED = ("sharp", "exponential")
LIMIT = 2.5


def main() -> None:
    """Read the SUMMARY lines, print the table and the pick."""
    worst: dict[tuple[float, float], float] = {}
    for line in Path(sys.argv[1]).read_text().splitlines():
        parts = line.split()
        if parts[:1] != ["SUMMARY"] or parts[3] not in CLAIMED:
            continue
        key = (float(parts[2]), float(parts[6]))
        bound = float(parts[8].rstrip("%"))
        worst[key] = max(worst.get(key, 0.0), bound)
    margins = sorted({g for _, g in worst})
    if len(margins) < 2:
        emit("one candidate margin in the file: nothing to choose from")
        return
    missing = [(n, g) for n in NOISES for g in margins if (n, g) not in worst]
    if missing:
        emit(f"missing (noise, margin) cells: {missing}; no pick")
        return
    emit("worst CP95 upper bound (%), sharp pairs and exponential tails")
    emit("noise | " + " | ".join(f"margin {g:g}" for g in margins))
    for noise in NOISES:
        cells = " | ".join(f"{worst[(noise, g)]:8.2f}" for g in margins)
        emit(f"{noise:5.2f} | {cells}")
    over = {g: max(worst[(n, g)] for n in NOISES) for g in margins}
    ok = [g for g in margins if over[g] <= LIMIT]
    if ok:
        emit(f"margin picked by the rule: {min(ok):g}")
    else:
        closest = min(margins, key=lambda g: over[g])
        emit(
            f"no margin meets {LIMIT}%; closest {closest:g} with "
            f"{over[closest]:.2f}%"
        )


if __name__ == "__main__":
    main()
