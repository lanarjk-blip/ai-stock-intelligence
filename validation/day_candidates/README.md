# Full-day classifiers: rejected research candidates

The next experiment evaluated logistic regression, a shallow XGBoost, a small
one-hidden-layer neural classifier, and an equal ensemble. Each candidate was
also tested with three fixed mixtures with uniform to reduce overconfidence.
Model/mixture selection used validation Brier only. There is no live UI training.

Training precedes the complete validation/test day. Thirteen features describe
prior-session state, actually available opening/prefix data, target clock,
forecast horizon and Friday schedule. Target interval prices/volume never enter
its feature vector. Calibration/feature scaling is fitted on training only.

- Six validation dates: 7, 8, 9, 12, 13, 14 October 2026, using rolling prior days.
- Final fit: 4410 training rows, through 14 October.
- Final dates: 15, 16, 19, 20, 21, 22, 23, 26 October.
- Final common coverage: 1008 forecasts over 456 unique outcomes, six tickers:
  BBCA, BBRI, ANTM, TLKM, AMRT, ACES. MPXL/PTSP lack 35-day evaluation coverage.
- These archives were already examined in earlier work. Chronological separation
  does not turn them into untouched or independent prospective evidence.

| Method | Final directional accuracy | Brier |
| --- | ---: | ---: |
| XGBoost, selected on validation | 32.94% | 0.67974 |
| Logistic (full mixture weight) | 33.13% | 0.72081 |
| Small neural classifier (full mixture weight) | 28.08% | 0.67421 |
| Equal ensemble (full mixture weight) | 31.75% | 0.68113 |
| Previous conditional analogue | 35.81% | 0.68294 |
| R90 baseline | 37.30% | 0.67743 |

Uniform Brier is 0.66667. The validation-selected classifier failed final baseline
comparison. No alternative is picked after inspecting final test scores. All
weights remain quarantined, production_enabled=false, and the existing trading
promotion gate remains false. No accuracy/profit improvement is claimed.
Day-block 95% Brier-gain interval vs R90 spans zero; versus uniform it is negative.
No transaction-cost or real-execution advantage has been established.

The first attempted scoring run found 36 additional Friday 13:30-issue targets
that the previous experiment had omitted under its ingestion gate. Before final
metrics were computed, scoring was aligned by forecast identity to the immutable
1008-row previous replay. The protocol records this coverage correction. The
training/validation definitions and selected XGBoost weights did not change:
component SHA256 remains 66fa044e1b45f06321820f0a4174e961b07c05772aa8f03f1613d4f57f61ee74.

frozen.json retains the selected settings, training end, original weight freeze
time, component/protocol/feature-engine checksums and nonproduction flags.
frozen.py replays all 1008 probability vectors without retraining. Source-engine
checksum metadata was added after scoring; the weights/timestamp are unchanged.
results.json contains all configurations, calibration, per-ticker results,
source hashes and warnings. replay.json preserves features and issued/cutoff
identities for every evaluated prediction. Do not count repeated issue forecasts
as independent trades.

Reproduce (repository root):

    python -m pip install -r validation/day_candidates/requirements-research.txt
    PYTHONPATH=. python validation/day_candidates/experiment.py
    python -m pytest -q tests/test_day_candidate_research.py

## Operational change shipped separately from these rejected classifiers

The existing conditional-analogue shadow ledger now settles pending outcomes
from saved verified bars in normal worker cycles and after intraday collection.
It works while the APK is closed, adds no downloads, and processes up to 20
pending tickers with an oldest-checked cursor to avoid starvation. Cached/unknown
target bars are excluded even if a newer quote exists. Original forecasts and
completed outcomes remain immutable. Its cursor table is protected by retention.
The input-availability caption now displays WIB explicitly.
