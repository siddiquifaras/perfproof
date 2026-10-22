"""Worst CP95 upper bound per family, noise and margin, from sweeps.

Reads the ``SUMMARY`` lines of ``rule_sweep.py`` and ``mixed_pair.py``
output (every comparison, "all") and prints one table per kind of
error. The sharp and exponential families are the claimed coverage; the
others are stated limits.

Usage: python limits_table.py file [file ...]
"""

import sys
from pathlib import Path

from _out import emit


def main() -> None:
    """Print the false KEEP and false REJECT tables."""
    worst: dict[tuple[str, str, float, float], float] = {}
    for name in sys.argv[1:]:
        for line in Path(name).read_text().splitlines():
            p = line.split()
            if p[:1] != ["SUMMARY"] or p[3] == "margin":
                continue
            key = (p[3], p[4], float(p[2]), float(p[6]))
            worst[key] = max(worst.get(key, 0.0), float(p[8].rstrip("%")))
    fams = sorted({k[0] for k in worst})
    noises = sorted({k[2] for k in worst})
    margins = sorted({k[3] for k in worst})
    for kind in ("KEEP", "REJECT"):
        emit(f"worst CP95 upper bound (%), false {kind}")
        emit("family | noise | " + " | ".join(f"m={g:g}" for g in margins))
        for fam in fams:
            for noise in noises:
                cells = " | ".join(
                    f"{worst[(fam, kind, noise, g)]:6.2f}" for g in margins
                )
                emit(f"{fam:<11} | {noise:4.2f} | {cells}")
        emit()


if __name__ == "__main__":
    main()
