# stats

The decision logic. If this package is wrong, every verdict perfproof
gives is wrong, so it holds the highest bar in the repo.

`docs/method.md` is the spec. Code follows it; when they disagree, stop
and report rather than picking one.

## Rules

- Pure functions. NumPy arrays in, NumPy arrays or frozen dataclasses
  out. No I/O, no clock, no logging, no imports from other perfproof
  packages except `perfproof.schema`.
- Randomness only through an `np.random.Generator` passed in by the
  caller. Never seed or use the global RNG.
- Work on log ratios. Ratios are multiplicative; logs make them additive
  and keep the arithmetic stable.
- Every function takes a leading batch axis where it makes sense, so the
  calibration suite can push thousands of replications through one call.
- Validate inputs at the public boundary: shape, finiteness, minimum n.
  Raise `ValueError` with the offending value.

## Vectorization

- Bootstrap: draw indices once as `rng.integers(0, n, size=(B, n))`,
  gather, reduce along `axis=-1`. Chunk `B` so the gathered array stays
  under a fixed byte budget.
- Walsh averages for Hodges-Lehmann: take the upper triangle of
  `np.add.outer(d, d) / 2`. That is O(n^2) memory, fine for the n we
  run (hundreds at most); say so in the docstring.
- Quantiles over a batch: one `np.quantile(..., axis=-1)` call, never a
  loop over rows.

## Proof required

A new or changed procedure lands with:

1. Unit tests on hand-checkable inputs, including every row of the
   decision table in `docs/method.md`.
2. Property tests (Hypothesis) for invariants: swapping baseline and
   candidate inverts the interval, scaling both arms by one factor leaves
   it unchanged, and for a lower-is-better metric, scaling only the
   candidate by `c` scales the interval by `1 / c`.
3. Calibration under the prespecified acceptance rule in
   `docs/method.md`, with seed, `R`, outcome counts and Clopper-Pearson
   bounds written into `docs/method.md`. The rule is never adjusted to
   fit a result.
