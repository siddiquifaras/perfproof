# Node warm-up rule: choices fixed before the runs

Written and committed before `rule_sweep.py`, `mixed_pair.py` and
`heldout.py` were run.

The rule under test (no cover gate, no "unknown" path from noisy
windows):

1. Each tree is recorded: 12 fresh processes, batches of 500
   iterations, R = 320 batches (retries at 640 and 1,280 when the tail
   windows disagree).
2. `W(s)` is the mean over processes of the mean of the 20 batches
   starting at batch `s`. The reference is the mean of `W` at batches
   R - 20, R - 40, R - 60 ("the tail windows"). The tail windows
   *agree* when the largest is at most `(1 + tol)` times the smallest.
3. `N` is the smallest `s` whose windows `W(s)`, `W(s + 20)` and
   `W(s + 40)` are each within `tol` of the reference (|W / ref - 1| at
   most `tol`).
4. `WARM` = margin x 500 x the larger `N` of the two trees. If the tail
   windows of either tree still disagree after the 1,280-batch
   recording, `WARM` = 640,000 (the longest recording) and the skill
   reports "measured, can't keep: warm-up unknown". That is the only
   unknown path. (Superseded in round 9: `WARM` 160,000 and the
   verdict stands.)

Choices, in this order.

**A. Tolerance of the estimator.** Candidate 1: fixed `tol` = 2%.
Candidate 2 (noise-scaled): `tol` = max(2%, 3 x se), where `se` is the
relative standard error of a tail window mean, estimated from the
12 processes of the same recording (each process's three tail windows
divided by that process's own mean, spread across processes, over
sqrt(12)). Use candidate 1 if, on the sharp pair (1, 1), the unknown
rate is at most 5% at every noise 0.1, 0.25, 0.5 and 1.0 (1.0 is the
committed curve). Otherwise use candidate 2, and report its unknown
rate at 1.0 whatever it is.

**B. Margin.** Margins 2, 3 and 4. The smallest margin whose CP95 upper
bound (binomial, 95%) is at most 2.5% on every row of the sharp pairs
and the exponential tails, at every noise 0.1, 0.25, 0.5, 1.0, 1.5 and
2.0, counting every KEEP or REJECT the simulation produces (unknown
pairs included, with `WARM` 640,000). Rows: false KEEP at a true ratio
of 1.00 and 1.03, false REJECT at 1.06. If none qualifies, no margin is
chosen and the closest is reported.

Claimed coverage: sharp-drop pairs (f of 1, 2, 3 for either tree) and
exponential tails (a of 0.5, 0.75; tau of 2,000 to 40,000). Everything
else (second plateau, drift, hump, narrow hump, mixed pairs, held-out
random scenarios) is reported as a stated limit with its numbers and
is not used to choose.

## Amendment, made after the first run (margins 2, 3 and 4)

Choice A selected the noise-scaled tolerance (the fixed 2% tolerance
leaves 8.3% of pairs unknown at noise 0.5 and 71% at 1.0;
`out/estimator-unknown.txt`).

Choice B, as written, picked no margin: the worst CP95 upper bound at
noise 1.0 was 21.1%, 12.1% and 6.4% for margins 2, 3 and 4, and at
noise 2.0 29.9%, 18.9% and 14.4%, all on the exponential tails with a
small tau (`out/rule-sweep-scaled-m234.txt`, `pick_margin.py`). The
cause is in the estimator: a 20-batch window dilutes a fast tail, and a
tolerance of 3 se (6% at noise 1.0) finds `N` as soon as the diluted
window is inside it.

The amendment, decided after seeing that and so not pre-registered:
the same rule (smallest margin with every sharp and exponential row at
or below 2.5%, at every noise from 0.1 to 2.0) over the wider candidate
set 2, 3, 4, 5, 6, 8. The estimator, the families, the noise levels and
the 2.5% bar are unchanged. Nothing else in the rule was changed after
seeing the results.

## Round 6, 2026-10-22: a robust estimator (fixed before the runs)

The reason: the round-6 audit showed that heavy-tailed batches break
the estimator of the previous sections. One batch 30 times the usual in
each process, inside the tail windows, gives `tol` of 51% and `N` of 0,
so `WARM` is 0. Random outliers at 1% to 2% of batches, 30 times the
usual, put `N` at one batch or less in 42% to 56% of recordings. End to
end at margin 8, outliers in 0.5% to 2% of batches gave 5.9% to 9.3%
false KEEP on an exponential tail and about 3.6% on the sharp pair
(3, 1). A loaded host can produce such batches, and a busy machine is
meant to be handled, not excluded.

The change, to the estimator only ("robust" in `model.py`):

1. A process's value for a 20-batch window is the median of its 20
   batch values, not the mean. `W(s)` is the mean over the 12 processes
   of those medians. (Executions, which report a mean, are unchanged.)
2. `se` is estimated robustly: each process's three tail window values
   divided by its own mean, deviations of those 36 ratios from their
   median over the processes in the same window, the median of the
   absolute deviations (MAD), times 1.4826, over sqrt(12).
3. `tol = min(max(0.02, 3 se), 0.04)`: the 2% floor, the factor 3, and a
   maximum of twice the floor. The maximum is provisional.

Unchanged: the tail windows, the agreement test, `N` from three
windows, the retries, unknown only when the 1,280-batch tail disagrees,
and **margin 8**. No margin is re-chosen.

The pass rule. The robust estimator replaces the current one if, at
margin 8, noise 0.1, 0.25, 0.5 and 1.0, the CP95 upper bound is at most
2.5% on every row (false KEEP at a true ratio of 1.00 and 1.03, false
REJECT at 1.06) of: the six sharp pairs and the ten exponential tails
of the sweep's grid (a of 0.5 and 0.75, tau of 2,000, 5,000, 10,000,
20,000 and 40,000, each against tau 300), **with and without**
outliers. Outliers multiply each batch by m with probability q,
independently, in both the recordings (not the first batch) and the
executions of both trees, for q of 0.5%, 1% and 2% and m of 10 and 30
(the audit's `heavy_e2e6.py` scheme). The fine grid of `exp_grid.py`
without outliers at noise up to 1.0 must also have no cell over 2.5%.
The unknown rate and the cost are reported, not part of the rule.
If a row fails, the rows that fail are reported and nothing is tuned
further.

### Round 6 result, 2026-10-22 (added after the runs)

The runs used 2,000 comparisons per row (the sweeps of round 5 used
4,000), seeds 20261025 (`robust_sweep.py`) and 20261026
(`exp_grid.py`), margin 8. The pass rule was met: the worst CP95 upper
bound over every sharp and exponential row, with and without outliers,
at noise 0.1, 0.25, 0.5 and 1.0, was 1.80%
(`out/robust-verdict.txt`); the fine grid had no cell over 2.5% at any
of those noises (worst 1.80%, `out/exp-grid-robust.txt`). Nothing was
tuned. Not part of the rule and reported: the pair unknown rate of the
sharp pair (1, 1) rose to 5.13% at noise 0.5, 51.25% at 1.0, 84.97% at
1.5 and 94.97% at 2.0 (the scaled estimator had 0.52% at 1.0), the
effect of the cap (`out/estimator-unknown-robust.txt`). The `lowa`
cells (a of 0.3 and 0.4) of `exp_grid.py` are outside the rule; one is
over 2.5% at noise 0.5 (4.63%).

## Round 7, 2026-10-22: remove the cap (fixed before the runs)

The reason: round 6 passed its error rule, but the 4% cap made the pair
unknown in 51.25% of pairs at noise 1.0, 84.97% at 1.5 and 94.97% at
2.0 (`out/estimator-unknown-robust.txt`), which withholds the KEEP on a
busy machine. Window medians and a MAD-based `se` already resist
outliers, so the cap may be redundant.

The change, to the tolerance only: `tol = max(0.02, 3 se)` with the
round-6 window medians and MAD-based `se`, with no upper limit
("robust-nocap" in `model.py`); and, as the alternative, the same with
`tol` capped at 15% ("robust-cap15"). The tail windows, the agreement
test, `N`, the retries, the unknown rule and **margin 8** are
unchanged. Nothing else is varied.

The pass rule; a variant must meet both parts:
- (a) Errors, as in round 6: at margin 8, noise 0.1, 0.25, 0.5 and 1.0,
  the CP95 upper bound is at most 2.5% on every row (false KEEP at a
  true ratio of 1.00 and 1.03, false REJECT at 1.06) of the six sharp
  pairs and the ten exponential tails of the sweep's grid, clean and
  with outliers (probability q of 0.5%, 1% and 2%, multiplier m of 10
  and 30, in the recordings and the executions of both trees).
- (b) The KEEP-blocking cost: the unknown rate of the sharp pair (1, 1)
  is at most 5% at every noise 0.1, 0.25, 0.5, 1.0 and 1.5
  (`estimator_unknown.py`, 4,000 pairs, seed 20261022 as in round 6).
  The fine grid at noise up to 1.0, without outliers, must also have
  no cell over 2.5%.

The choice: among the variants that pass both parts, the one with no
cap, because it is simpler; else the 15% cap. If neither passes, the
rows that fail are reported and nothing is tuned further (no other cap
is tried). The runs use 2,000 comparisons per row and new seeds
(20261031 for the sweeps, 20261032 for the fine grid). If a variant
passes, it replaces the 4% cap in the docs and the 4% result is kept as
a rejected alternative.

### Round 7 result, 2026-10-22 (added after the runs)

Neither variant passed, so nothing replaces the 4% cap and nothing was
tuned. Part (b), sharp pair (1, 1), 4,000 pairs, seed 20261022
(`out/estimator-unknown-r7.txt`): pair unknown, no cap, 0.00 / 0.68 /
4.65 / **5.80** / **5.75**% at noise 0.1 / 0.25 / 0.5 / 1.0 / 1.5; 15% cap, 0.00 / 0.68 / 4.65 / **5.80** /
**5.63**%. Both are over 5% at noise 1.0 and 1.5. Part (a) was run for
the no-cap variant only (`out/r7-nocap-verdict.txt`, seed 20261031,
2,000 per row): it fails at noise 1.0 on the exponential tails, 16.22%
false KEEP and 8.75% false REJECT clean, and 7.43%, 4.69% and 3.26%
false KEEP with outliers; at noise 0.1 to 0.5 every row is at most
1.91%. The 15% variant's part (a) was not run, because it already
failed part (b).

## Round 8, 2026-10-22: more processes per tree (fixed before the runs)

The reason: rounds 6 and 7 showed that at noise 1.0 the trade between
false KEEP and unknown is information-limited. The 4% cap gives 51.25%
unknown at noise 1.0; removing it (or capping at 15%) gives 5.8%
unknown but a false KEEP of 16.22% on the exponential tails, and fails
at noise 1.0 and 1.5 (`PREREGISTRATION.md`, rounds 6 and 7). More
information per recording should shrink `se` and the window noise, so
the 4% cap binds less often.

The change: the round-6 estimator (window medians, MAD-based `se`,
`tol` capped at 4%) and **margin 8**, unchanged, with the number of
processes recorded per tree raised from 12 to 24, and, as the
alternative, to 36. Only the processes per recording change (the `se`
divides by the square root of the count; the median over processes
averages more). Executions, the tail windows, the agreement test, `N`,
the retries and the unknown rule are unchanged. The model reads the
count from `NODE_WARMUP_PROCESSES` (default 12).

The pass rule, the two parts of round 7:
- (a) Errors: at margin 8, noise 0.1, 0.25, 0.5 and 1.0, the CP95 upper
  bound is at most 2.5% on every row (false KEEP at a true ratio of
  1.00 and 1.03, false REJECT at 1.06) of the six sharp pairs and the
  ten exponential tails of the sweep, clean and with outliers
  (q of 0.5%, 1% and 2%, m of 10 and 30, in recordings and executions
  of both trees).
- (b) Unknown: the sharp pair (1, 1) unknown rate is at most 5% at every
  noise 0.1, 0.25, 0.5, 1.0 and 1.5 (`estimator_unknown.py`, 4,000
  pairs).

The choice: the fewest processes that pass both parts (24 before 36).
Part (b) is run first for 24 and 36 (seed 20261041), then part (a) for
the cheapest variant that passes (b) (`robust_sweep.py`, 2,000
comparisons per row, seed 20261042). If neither passes both, the rows
that fail are reported and nothing is tuned further (no other count,
cap or margin is tried). The cost of choosing is reported for each:
processes times recording time per tree (3.4 s for 320 batches, 6.7 s
for 640, 13.2 s for 1,280, per process, `out/cost.txt`), with the
model's mean over retries from `estimator_unknown.py`. If a variant
passes, it replaces the 12-process rule in `docs/method.md` and
`skills/perfproof/node.md` with the cost stated, and the 12-process
result (rounds 6 and 7) is recorded as the rejected variant.

### Round 8 result, 2026-10-22 (added after the runs)

Neither variant passed part (b), so part (a) was not run, nothing
replaces the 12-process rule and nothing was tuned. Sharp pair (1, 1),
4,000 pairs, seed 20261041, margin 8, 4% cap, pair unknown at noise
0.1 / 0.25 / 0.5 / 1.0 / 1.5 (and 2.0, not part of the rule):
- 24 processes: 0.00 / 0.00 / 2.52 / **18.53** / **61.22**% (84.62%),
  `out/estimator-unknown-r8-p24.txt`;
- 36 processes: 0.00 / 0.00 / 1.68 / **6.57** / **39.70**% (70.45%),
  `out/estimator-unknown-r8-p36.txt`;
- (12 processes, round 6: 0.00 / 0.68 / 5.13 / 51.25 / 84.97%).
Cost of choosing both trees, the model's mean over retries, at noise
0.1 / 0.25 / 0.5 / 1.0 / 1.5: 24 processes 164 / 174 / 271 / 446 / 729 s,
36 processes 246 / 249 / 382 / 496 / 881 s (12 processes: 82 / 114 /
156 / 334 / 449 s).

### Round 9, 2026-10-22 (owner direction: a busy machine never blocks a KEEP)

Rule change, nothing else: a pair with an unknown tree uses `WARM`
160,000 (640,000 was run too) and the comparison proceeds, KEEP
included. One check (`unknown_keep.py`, seed 20261051, 2,000
comparisons per row, sharp and exponential grid, margin 8, robust
estimator, noise 0.1, 0.5, 1.0, 1.5, 2.0, clean and outliers q 2% x10
and x30). Bar: all pairs combined, worst-row CP95 at most 2.5% at noise
at most 1.0; if it failed, report and stop, no tuning. 160,000 was
used if it met the bar, else 640,000. Result: both met it (worst row
1.74% at 160,000 and 1.68% at 640,000), so 160,000;
`out/unknown-keep-w160000.txt`, `out/unknown-keep-w640000.txt`.
