"""Held-out random scenarios: stated limits of the rule.

Each scenario combines violations drawn from the ranges below (written
down before any scenario ran; the audit that proposed them did not use
this repository's shape parameters):

* arm A drop scale f in 1, 1.5, 2, 3; drop-position spread x1 or x2;
* arm A shapes, one or two of: plateau +p until k1 (p in 1% to 8%, k1 in
  30,000 to 150,000); drift +d to 0 over kd (d in 1% to 8%, kd in
  100,000 to 600,000); hump +h on [k0, k0 + w) (h in 5% to 30%, k0 in
  10,000 to 100,000, w in 2,000 to 40,000);
* arm B f in 1, 1.5, 2; with probability 0.3 a plateau or drift of at
  most 2%;
* process spread in 0.02, 0.042, 0.08.

Usage: python heldout.py R mode sseed rseed n margins noise [...]
"""

import sys

import model as m
import numpy as np
from _out import emit


def draw_shape(g: np.random.Generator) -> tuple[str, m.Shape]:
    """Draw one labelled extra shape for arm A."""
    kind = g.choice(["plateau", "drift", "hump"])
    if kind == "plateau":
        p, k1 = g.uniform(0.01, 0.08), g.uniform(30000, 150000)
        return f"plat+{100 * p:.1f}%<{k1 / 1000:.0f}k", m.plateau(p, k1)
    if kind == "drift":
        d, kd = g.uniform(0.01, 0.08), g.uniform(100000, 600000)
        return f"drift+{100 * d:.1f}%/{kd / 1000:.0f}k", m.drift(d, kd)
    h, k0, w = (
        g.uniform(0.05, 0.30),
        g.uniform(10000, 100000),
        g.uniform(2000, 40000),
    )
    label = f"hump+{100 * h:.0f}%[{k0 / 1000:.0f}k,+{w / 1000:.0f}k)"
    return label, m.hump(h, k0, k0 + w)


def main() -> None:
    """Draw scenarios, run each at every margin, count bad cells."""
    runs, mode = int(sys.argv[1]), sys.argv[2]
    sseed, rseed, count = (int(x) for x in sys.argv[3:6])
    margins = tuple(float(x) for x in sys.argv[6].split(","))
    noises = [float(x) for x in sys.argv[7:]]
    emit(
        f"scenario seed {sseed}, run seed {rseed}, R = {runs}, tolerance "
        f"{mode}, {count} scenarios per noise"
    )
    for noise in noises:
        g = np.random.default_rng([sseed, round(100 * noise)])
        rng = np.random.default_rng([rseed, round(100 * noise)])
        pl = m.Pools(noise)
        cells = dict.fromkeys(margins, 0)
        keep_cells = dict.fromkeys(margins, 0)
        total = 0
        emit(f"== noise {noise:g}")
        for i in range(count):
            jsd = float(g.choice([0.02, 0.042, 0.08]))
            a = m.Arm(
                pl,
                f=float(g.choice([1.0, 1.5, 2.0, 3.0])),
                sd=float(g.choice([1.0, 2.0])),
                jsd=jsd,
            )
            for _ in range(int(g.choice([1, 2]))):
                a.shapes.append(draw_shape(g))
            b = m.Arm(pl, f=float(g.choice([1.0, 1.5, 2.0])), jsd=jsd)
            if g.random() < 0.3:
                if g.random() < 0.5:
                    p = g.uniform(0.005, 0.02)
                    b.shapes.append(
                        (f"plat+{100 * p:.1f}%<60k", m.plateau(p, 60000))
                    )
                else:
                    d = g.uniform(0.005, 0.02)
                    b.shapes.append(
                        (f"drift+{100 * d:.1f}%/200k", m.drift(d, 200000))
                    )
            tag = f"H{i + 1:02d}"
            rows, known, _ = m.scenario(rng, tag, a, b, runs, margins, mode)
            emit(
                f"-- {tag} | A: {a.label()} | B: {b.label()} | jsd {jsd:g}"
                f" | N known in {100 * known:.1f}% of pairs"
            )
            for row in rows:
                emit(row.line())
                if row.ub > m.LIMIT:
                    cells[row.margin] += 1
                    if row.kind == "KEEP":
                        keep_cells[row.margin] += 1
            total += len(rows) // len(margins)
        for mg in margins:
            emit(
                f"SUMMARY noise {noise:g} margin {mg:g}: cells {total}, "
                f"CP95 above 2.5% in {cells[mg]} ({keep_cells[mg]} of them "
                f"false KEEP)"
            )


if __name__ == "__main__":
    main()
