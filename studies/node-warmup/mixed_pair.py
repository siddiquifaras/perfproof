"""Mixed pairs and narrow humps: stated limits of the rule.

Mixed pair: a hump on the earlier-settling tree A, inside the window
that the later tree B sets. Narrow hump: a short hump on a tree that
settles like the committed one, placed in the neighbourhood of the
measured window; a window of 20 batches holding the whole hump sees
area / 20 of excess.

Usage: python mixed_pair.py R seed mode margins noise [noise ...]
"""

import sys

import model as m
import numpy as np
from _out import emit

MIXED = (
    (2.0, 0.10, 40000, 55000),
    (2.0, 0.20, 44000, 50000),
    (3.0, 0.10, 60000, 75000),
    (3.0, 0.20, 64000, 70000),
    (3.0, 0.10, 55000, 65000),
)


def cases(pl: m.Pools) -> list[tuple[str, str, m.Arm, m.Arm]]:
    """Return (family, tag, tree A, tree B) for both kinds."""
    out = []
    for fb, h, k0, k1 in MIXED:
        tag = f"mixed hump +{100 * h:.0f}% [{k0},{k1}) B f={fb:g}"
        out.append(
            (
                "mixed",
                tag,
                m.Arm(pl, shapes=[(tag, m.hump(h, k0, k1))]),
                m.Arm(pl, f=fb),
            )
        )
    for start in (14000, 20000, 26000, 40000, 46000, 52000):
        for width in (2000, 4000):
            for area in (40, 60, 80):
                h = area / 100.0 / (width / m.BATCH)
                tag = (
                    f"narrow hump +{100 * h:.1f}% [{start},+{width}) "
                    f"area {area}"
                )
                out.append(
                    (
                        "narrow",
                        tag,
                        m.Arm(
                            pl,
                            shapes=[(tag, m.hump(h, start, start + width))],
                        ),
                        m.Arm(pl),
                    )
                )
    return out


def main() -> None:
    """Run the cases at each noise; print rows and summaries."""
    runs, seed, mode = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    margins = tuple(float(x) for x in sys.argv[4].split(","))
    noises = [float(x) for x in sys.argv[5:]]
    emit(
        f"seed {seed}, R = {runs} per row, tolerance {mode}, "
        f"margins {sys.argv[4]}"
    )
    for noise in noises:
        rng = np.random.default_rng([seed, round(100 * noise)])
        worst: dict[tuple[str, str, float], tuple[float, float]] = {}
        emit(f"== noise {noise:g}")
        for fam, tag, a, b in cases(m.Pools(noise)):
            rows, known, med = m.scenario(rng, tag, a, b, runs, margins, mode)
            emit(
                f"-- {fam}: {tag} | A: {a.label()} | B: {b.label()} | "
                f"N known in {100 * known:.1f}% of pairs, WARM median "
                f"{med:.0f} at margin {margins[0]:g}"
            )
            for row in rows:
                emit(row.line())
                key = (fam, row.kind, row.margin)
                old = worst.get(key, (0.0, 0.0))
                worst[key] = (max(old[0], row.ub), max(old[1], row.ub_known))
        for (fam, kind, margin), (ub, ubk) in sorted(worst.items()):
            emit(
                f"SUMMARY noise {noise:g} {fam} {kind} margin {margin:g} "
                f"all {100 * ub:.2f}% known {100 * ubk:.2f}%"
            )


if __name__ == "__main__":
    main()
