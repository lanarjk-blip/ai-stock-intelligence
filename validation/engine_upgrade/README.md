# Engine training experiment — 26 October 2026

Status: **NO PROMOTION**. Production Time Map, model and APK are unchanged.
This is an offline experiment on already inspected archives, not an independent
holdout, trading win-rate, live forecast or guaranteed accuracy improvement.

## Executed work

The fixed protocol was saved before training. One bounded two-ticker recovery
used longer eight-second timeouts. Long-history requests still timed out on both
Yahoo hosts; five-day recovery yielded 34 rows each. PTSP/MPXL still have only
17/16 eligible dates after combining archives, below the fixed 35-day screen.
All errors and source timestamps are preserved in recovery.json.

Training used 1,516 eligible rows from BBCA, ANTM, BBRI and TLKM (40 dates each),
with nine shared lag-only features. No target candle price or volume enters its
features. First 20 dates warm up; subsequent 12 dates are daily walk-forward
validation; final eight dates are 14,15,16,19,20,21,22,23 October 2026. The final
weights train through 13 October, before 304 retrospective test outcomes.

One configuration each was tried: balanced regularized logistic regression,
shallow XGBoost, a 12-unit neural network, and their equal-weight ensemble.
Chronological validation Brier alone selected the ensemble. Test results were
not used to switch to another model. No LSTM/Transformer training was justified
by this small and incomplete dataset. No synchronized IHSG/sector features were
invented, and there was no paid provider activation.

| Method | Direction correct | Balanced accuracy | Brier (lower better) |
|---|---:|---:|---:|
| Same-sample historical clock-slot baseline | 35.53% | 36.00% | 0.685309 |
| Global ticker-frequency baseline | 29.61% | 33.92% | 0.670985 |
| Balanced logistic | 41.45% | 41.58% | 0.661444 |
| XGBoost | 36.84% | 36.97% | 0.665158 |
| Neural network | 38.49% | 39.60% | 0.650854 |
| Validation-selected ensemble | 41.45% | 41.93% | 0.653168 |

Ensemble UP precision46.73%, DOWN40.86%, ECE0.05329. Per-ticker directional
accuracy: ANTM39.47%, BBCA40.79%, BBRI35.53%, TLKM50.00% (76 outcomes each).
These are three-class direction metrics, not profitable-trade percentages.

Paired 8-day-block bootstrap95% Brier improvement: versus slot baseline
[0.01410,0.05752], versus global baseline[-0.01417,0.03966]. The latter crosses
zero. Short block history, previously seen data and missing ticker coverage
prevent an improvement/promotion claim. Fees, spread, slippage, executable
entries/exits and downside trading losses were not evaluated.

## Reproducibility and protection

Training took33.709seconds in this run; this is not an application speed gain.
JSON candidate weights were saved before final test scoring, with feature/class
identity, training end and SHA256 checksums. There are no pickle objects. The
portable replay reproduced all304 selected-candidate probability vectors with
maximum difference0.0, without retraining. Six new guard tests passed locally.
Results, warnings (zero), per-ticker metrics, validation folds, input hashes and
historical-only records are available in results.json and replay.json.

Commands for engineering reproduction:

```
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python validation/engine_upgrade/train.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m validation.engine_upgrade.frozen
python -m pytest -q tests/test_engine_upgrade_research.py
```

Optional research dependencies are pinned in requirements.txt. These scripts are
not imported by the app or worker. JSON candidate files do not enable production
signals. Tests explicitly reject promotion from retrospective archives, and
never automatically promote even when prospective flags are supplied.

Next action: obtain complete verifiable PTSP/MPXL intraday history and a feed
appropriate for minute monitoring. Freeze candidate and decision thresholds
before collecting independent prospective multi-ticker outcomes. Record actual
prediction time, feature cutoff and candle time before observing outcomes; then
compare calibration, direction, coverage and costs against both baselines.
There is currently no automatic live candidate collector or promoted model.
The paid feed trial is still awaiting provider confirmation; no credentials or
subscription were invented, purchased or placed in GitHub.
