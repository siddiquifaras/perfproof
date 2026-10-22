"""The claimed families and the audit shapes, by noise and margin.

Claimed coverage: sharp-drop pairs and exponential tails. Stated limits:
the audit shapes (second plateau, drift, hump, late tree with a hump).
Margin 2, 3 and 4 share each pair's estimates of N. Every row is a
production ``run_looks`` over 60 rounds.

The last lines per noise are ``SUMMARY`` lines, which ``pick_margin.py``
reads: the worst CP95 upper bound per family, kind and margin, over all
comparisons ("all") and over those where N was found ("known").

Usage: python rule_sweep.py R seed mode margins noise [noise ...]
"""

import sys

import model as m
import numpy as np
from _out import emit


def claimed(
    pl: m.Pools, outlier: tuple[float, float] | None = None
) -> list[tuple[str, str, m.Arm, m.Arm]]:
    """Return (family, tag, tree A, tree B) for the claimed pairs.

    ``outlier`` adds heavy-tailed batches to every arm (see ``m.Arm``).
    """
    out = []
    for fb, fc in ((1, 1), (3, 1), (1, 3), (2, 1), (1, 2), (3, 3)):
        out.append(
            (
                "sharp",
                f"sharp ({fb},{fc})",
                m.Arm(pl, f=float(fb), outlier=outlier),
                m.Arm(pl, f=float(fc), outlier=outlier),
            )
        )
    out.extend(
        (
            "exponential",
            f"exp a={a} tau={tau:.0f} vs tau=300",
            m.Arm(pl, exp=(a, tau), outlier=outlier),
            m.Arm(pl, exp=(a, 300.0), outlier=outlier),
        )
        for a in (0.5, 0.75)
        for tau in (2000.0, 5000.0, 10000.0, 20000.0, 40000.0)
    )
    return out


def limits(pl: m.Pools) -> list[tuple[str, str, m.Arm, m.Arm]]:
    """Return (family, tag, tree A, tree B) for the audit shapes."""
    shapes = (
        (
            "plateau",
            "second plateau +2% to 80,000",
            1.0,
            m.plateau(0.02, 80000),
        ),
        (
            "plateau",
            "second plateau +4% to 80,000",
            1.0,
            m.plateau(0.04, 80000),
        ),
        (
            "plateau",
            "second plateau +6% to 80,000",
            1.0,
            m.plateau(0.06, 80000),
        ),
        ("drift", "drift +2% over 300,000", 1.0, m.drift(0.02, 300000)),
        ("drift", "drift +4% over 300,000", 1.0, m.drift(0.04, 300000)),
        ("hump", "hump +10% 20,000-60,000", 1.0, m.hump(0.10, 20000, 60000)),
        ("hump", "hump +10% 50,000-80,000", 1.0, m.hump(0.10, 50000, 80000)),
        (
            "hump",
            "late f=3 + hump 50,000-70,000",
            3.0,
            m.hump(0.10, 50000, 70000),
        ),
        (
            "hump",
            "late f=3 + hump 100,000-130,000",
            3.0,
            m.hump(0.10, 100000, 130000),
        ),
    )
    return [
        (
            fam,
            tag,
            m.Arm(pl, f=f, shapes=[(tag, shape)]),
            m.Arm(pl),
        )
        for fam, tag, f, shape in shapes
    ]


def main() -> None:
    """Run every family at each noise; print rows and summaries."""
    runs, seed, mode = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    margins = tuple(float(x) for x in sys.argv[4].split(","))
    noises = [float(x) for x in sys.argv[5:]]
    emit(
        f"seed {seed}, R = {runs} per row, tolerance {mode}, "
        f"margins {sys.argv[4]}"
    )
    for noise in noises:
        rng = np.random.default_rng([seed, round(100 * noise)])
        pl = m.Pools(noise)
        worst: dict[tuple[str, str, float], tuple[float, float]] = {}
        emit(f"== noise {noise:g}")
        for fam, tag, a, b in claimed(pl) + limits(pl):
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
