"""Apply the round-6 pass rule of PREREGISTRATION.md to sweep output.

Reads the ``SUMMARY`` and ``UNKNOWN`` lines of ``robust_sweep.py``
files. The estimator passes if every worst CP95 upper bound (sharp and
exponential, false KEEP and false REJECT, every noise, with and
without outliers) is at most 2.5%. Prints the table and the failing
cells.

Usage: python robust_verdict.py file [file ...]
"""

import sys
from pathlib import Path

from _out import emit

LIMIT = 2.5


def main() -> None:
    """Print the table of worst bounds and the verdict."""
    worst: dict[tuple[float, str], dict[str, float]] = {}
    unknown: dict[tuple[float, str], float] = {}
    for name in sys.argv[1:]:
        for line in Path(name).read_text().splitlines():
            p = line.split()
            if p[:1] == ["SUMMARY"]:
                # SUMMARY noise N outliers LABEL family kind all X%
                i = p.index("all")
                label = " ".join(p[4 : i - 2])
                key = (float(p[2]), label)
                col = f"{p[i - 2]} {p[i - 1]}"
                worst.setdefault(key, {})[col] = float(p[i + 1].rstrip("%"))
            elif p[:1] == ["UNKNOWN"]:
                i = p.index("pair")
                label = " ".join(p[4 : i - 1])
                unknown[(float(p[2]), label)] = float(p[-1].rstrip("%"))
    cols = sorted({c for v in worst.values() for c in v})
    emit("worst CP95 upper bound (%) | " + " | ".join(cols))
    fails = []
    for key in sorted(worst):
        cells = []
        for c in cols:
            v = worst[key].get(c, float("nan"))
            cells.append(f"{v:6.2f}")
            if v > LIMIT:
                fails.append((key, c, v))
        emit(
            f"noise {key[0]:<4g} {key[1]:<14} | "
            + " | ".join(cells)
            + f" | pair unknown {unknown.get(key, float('nan')):.2f}%"
        )
    if fails:
        emit(f"FAIL: {len(fails)} cells over {LIMIT}%")
        for key, c, v in fails:
            emit(f"  noise {key[0]:g} {key[1]} {c}: {v:.2f}%")
    else:
        emit(f"PASS: every worst bound is at most {LIMIT}%")


if __name__ == "__main__":
    main()
