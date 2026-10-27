# Probability calibration diagnostic — not promoted

Settings and the calibration weight were frozen in commit
`578c6776edd00fde68f05dd5b256dff144f5a252` before fetching the new evaluation-date bars.
Validation used October 7, 8, 9, 12, 13 and 14, 2026: 795 forecast instances.
Every target used only earlier dates for historical labels. Validation chose
alpha=0.5, mixing the unchanged conditional model equally with uniform.
The archived validation data had already been inspected in earlier experiments.

Evaluation date: October 27, 2026, issue anchors 08:50, 09:30 and 13:30 WIB.
Six tickers supplied usable data: ANTM, TLKM, AMRT, ACES, MPXL and PTSP.
BBCA and BBRI timed out on both bounded attempts from this environment.
The acquisition log preserves failures; missing data was not replaced by older-day data.
128 forecast instances concern **58 unique ticker/interval outcomes**, not 128
independent trades. This is retrospective replay with ideal candle availability,
not forecasts actually issued live before these outcomes. Only one test date is
available and no transaction costs are evaluated.

| Method | Directional accuracy | Balanced accuracy | Brier (lower better) | ECE |
| --- | ---: | ---: | ---: | ---: |
| Frozen calibrated analogue | 38.28% | 41.51% | 0.65587 | 0.03840 |
| Original analogue | 38.28% | 41.51% | 0.66332 | 0.06781 |
| Same-ticker/time R90 baseline | 40.63% | 42.80% | 0.64744 | 0.17781 |

Uniform Brier is 0.66667. Calibration improved Brier/ECE relative to the original
on this single day, but did not change the most likely class and did not beat R90.
The fixed filter (probability >=0.45, margin >=0.10) retained only six calibrated
forecasts, with 33.33% accuracy: no useful trading edge demonstrated.
All original and calibrated probabilities, source hashes and per-ticker metrics
are retained. No tuning or alternate candidate selection followed test scoring.
**The candidate is not imported by production; promotion remains disabled.**

Reproduce evaluation without fetching or retraining:

    PYTHONPATH=. python validation/probability_calibration/experiment.py evaluate
    python -m pytest tests/test_probability_calibration_research.py

Do not rerun `freeze` over the retained artifact: its original freeze timestamp
is part of the evidence. A new experiment needs a new protocol/artifact directory.

## Production improvement shipped alongside this rejected candidate

`timemap_evidence.py` reads only the selected ticker/current model's latest
60-day ledger. It counts one first-issued prediction per interval; later winning
revisions cannot replace it. Unknown/cached sources, future evidence, stale date
labels, invalid probabilities, unconfirmed outcomes and mismatched outcome labels
are excluded. Not-yet-known outcomes stay pending. Missing results are not 0%
accuracy, and uniform probability ties are not interpreted as DOWN predictions.

The collapsed details show actual recorded results, sample/day counts and a
bounded deterioration check. The last five settled dates are compared with
previous dates only after at least ten dates and 30 outcomes in each segment.
A Brier increase >=0.05 is a review warning, not a significance claim or an
automatic model switch. Fewer than 200 outcomes or 20 dates is labelled
insufficient evidence; reaching those counts never enables BUY or promotion.
No model training, new data downloads or universe scan occurs for these metrics.
