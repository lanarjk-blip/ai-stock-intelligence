# Conditional Time Map research — 25 October 2026

Decision: **NO MODEL PROMOTION**. Production stays unchanged, including NO EDGE.

810 eligible feature rows from archived BBCA/ANTM26Aug–23Oct. First20 dates (26Aug–22Sep) warm up training. Validation23Sep–13Oct uses daily expanding training strictly before each validation day. Final8 dates14–23Oct have152 outcomes; candidate selection and weights freeze before14Oct. These archives were inspected in earlier diagnostics, so this is NOT a new untouched promotion holdout. Model selection does not consume test scores.

Fixed candidates: standardized multinomial logistic regression(C=.1); Random Forest(100 trees,depth4,leaf20); histogram gradient boosting(60 iterations,7 leaves,leaf30,L2=5,learning rate.05,no random early-stopping split); equal-weight ensemble. No tuning after the final test. Separate per-ticker slot/global frequency comparators use the same training window and evaluated rows.

Features: completed previous-bar return/range, trailing3-bar momentum, trailing5-bar volatility/volume ratio, time since prior completion, clock, Friday indicator and ticker identity. Target candle OHLCV is excluded from features. Current/future label mutation and timestamp tests pass. Positive-volume/full-session outcomes define the evaluation cohort; this does not establish ability to predict availability of a future trade or handle suspended/newly listed stocks. IHSG/sector synchronized intraday history is absent and is not fabricated.

| Candidate | Test directional accuracy | Balanced accuracy | Multiclass Brier |
|---|---:|---:|---:|
| Logistic (selected on validation) |35.53%|36.82%|.7165|
| Random Forest |36.18%|36.40%|.6662|
| Histogram boosting |33.55%|32.23%|.6980|
| Ensemble |31.58%|31.88%|.6835|
| Slot baseline |33.55%|33.64%|.6728|
| Ticker global baseline |33.55%|34.92%|.6704|

Logistic's paired day-block bootstrap95% interval for Brier improvement over the global baseline is[-.1158,.0029], spanning zero and mostly negative. Random Forest is NOT promoted by switching winners after observing test scores. Precision, calibration bins, per-ticker metrics, training cutoffs and input SHA256 hashes are in conditional_results.json. Every test probability and actual outcome is retained in conditional_replay.json, explicitly marked historical_replay=true and live_forecast=false.

Neural sequence models/Transformers are not justified by810 rows across only two symbols. No fresh unseen dataset, diverse liquidity cohort, execution-cost evaluation or proof of market-wide edge is available. This offline research adds no production runtime cost and claims no speed/accuracy improvement.

Reproduce with production requirements plus requirements-research.txt:

`OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 AI_ENGINE_WORKER=1 python validation/quality/conditional_models.py`

The diagnostic ran in3.368s locally (single observation, not a benchmark). Targeted leakage/sample tests:7 passed in0.59s. Full GitHub regression must pass before merge.

Next evidence needed: timestamped prospective intraday histories across more liquid/illiquid/new/suspended tickers, synchronized IHSG/sector features where available, then an untouched chronological evaluation with a model-selection policy frozen beforehand. Do not retune these same final8 days until they become an apparent win.
