#!/bin/sh
# Regenerate everything in out/. Light CPU: niced, numpy only.
# Run one at a time on battery; the sweeps take 15 to 40 minutes each.
cd "$(dirname "$0")" || exit 1
PY=${PY:-python}
NOISES="0.1 0.25 0.5 1.0 1.5 2.0"
# The margin set of the first, pre-registered run, kept as evidence.
nice -n 19 $PY rule_sweep.py 4000 20261022 scaled 2,3,4 $NOISES > out/rule-sweep-scaled-m234.txt
# The sweeps after the amendment (PREREGISTRATION.md).
nice -n 19 $PY rule_sweep.py 4000 20261022 scaled 2,3,4,5,6,8 $NOISES > out/rule-sweep-scaled.txt
nice -n 19 $PY mixed_pair.py 4000 20261022 scaled 2,3,4,5,6,8 $NOISES > out/mixed-pair-scaled.txt
nice -n 19 $PY heldout.py 1000 scaled 777 888 30 2,3,4,5,6,8 0.1 0.25 0.5 > out/heldout-scaled-a.txt
nice -n 19 $PY heldout.py 1000 scaled 777 888 30 2,3,4,5,6,8 1.0 1.5 2.0 > out/heldout-scaled-b.txt
nice -n 19 $PY exp_grid.py 4000 20261023 8 0.5,1.0,1.25 > out/exp-grid-a.txt
nice -n 19 $PY exp_grid.py 4000 20261023 8 1.5,1.75,2.0 > out/exp-grid-b.txt
nice -n 19 $PY sharp_warm.py 4000 20261023 8 0.1,0.5,1.0,2.0 > out/sharp-warm.txt
nice -n 19 $PY estimator_unknown.py 4000 20261022 2,3,4,5,6,8 > out/estimator-unknown.txt
nice -n 19 $PY pick_margin.py out/rule-sweep-scaled.txt > out/margin-pick.txt
nice -n 19 $PY limits_table.py out/rule-sweep-scaled.txt out/mixed-pair-scaled.txt > out/limits-table.txt
nice -n 19 $PY cost.py > out/cost.txt
nice -n 19 $PY rule_on_committed.py > out/rule-on-committed.txt
nice -n 19 $PY half_check_power.py 7316650219 > out/half-check-power.txt
nice -n 19 $PY node_ab_model.py 4000 5927014338 0.042 60 6 > out/ab-model.txt
nice -n 19 $PY margin_model.py 4000 6021774 0.042 60 6 > out/margin-model.txt
nice -n 19 $PY estimate_model.py 4000 5551230777 > out/estimate-model.txt
nice -n 19 $PY drop_stats.py > out/drop-stats.txt
# Round 6 (PREREGISTRATION.md): the robust estimator, clean and with outliers.
nice -n 19 $PY robust_sweep.py 2000 20261025 robust 0.1 0.5 > out/robust-sweep-a.txt
nice -n 19 $PY robust_sweep.py 2000 20261025 robust 0.25 1.0 > out/robust-sweep-b.txt
nice -n 19 $PY robust_verdict.py out/robust-sweep-a.txt out/robust-sweep-b.txt > out/robust-verdict.txt
nice -n 19 $PY exp_grid.py 2000 20261026 8 0.1,0.25,0.5,1.0 robust > out/exp-grid-robust.txt
nice -n 19 $PY estimator_unknown.py 4000 20261022 2,3,4,5,6,8 robust > out/estimator-unknown-robust.txt
nice -n 19 $PY outlier_probe.py 2000 20261024 > out/outlier-probe.txt
# Round 7 (PREREGISTRATION.md): no cap and a 15% cap; part (b) first.
nice -n 19 $PY estimator_unknown.py 4000 20261022 2,3,4,5,6,8 robust-nocap robust-cap15 > out/estimator-unknown-r7.txt
nice -n 19 $PY robust_sweep.py 2000 20261031 robust-nocap 0.1 0.5 > out/r7-nocap-sweep-a.txt
nice -n 19 $PY robust_sweep.py 2000 20261031 robust-nocap 0.25 1.0 > out/r7-nocap-sweep-b.txt
nice -n 19 $PY robust_verdict.py out/r7-nocap-sweep-a.txt out/r7-nocap-sweep-b.txt > out/r7-nocap-verdict.txt
# Round 8 (PREREGISTRATION.md): 24 and 36 processes per tree, part (b).
NODE_WARMUP_PROCESSES=24 nice -n 19 $PY estimator_unknown.py 4000 20261041 8 robust > out/estimator-unknown-r8-p24.txt
NODE_WARMUP_PROCESSES=36 nice -n 19 $PY estimator_unknown.py 4000 20261041 8 robust > out/estimator-unknown-r8-p36.txt
# Round 9 (PREREGISTRATION.md): unknown pairs may KEEP; WARM 640,000 and 160,000.
nice -n 19 $PY unknown_keep.py 2000 20261051 640000 0.1 0.5 1.0 1.5 2.0 > out/unknown-keep-w640000.txt
nice -n 19 $PY unknown_keep.py 2000 20261051 160000 0.1 0.5 1.0 1.5 2.0 > out/unknown-keep-w160000.txt
