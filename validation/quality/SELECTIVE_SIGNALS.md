# Selective-signal diagnostic — 26 October 2026

Question: can abstaining on low-confidence predictions make the existing conditional candidate dependable?

Reuse the candidate selected on chronological validation (logistic), frozen before the archived final eight test dates, 14–23 October 2026. The 152 outcomes cover BBCA and ANTM. The archives and thresholds have already been inspected; this is descriptive research, not an untouched promotion test. Probe directional signals with maximum class probability >=0.60; exclude sideways predictions. Class mapping: DOWN=0, DATAR=1, UP=2.

Result: 4/152 signals (2.63% coverage), 1/4 correct (25%). UP: 1/2 correct; DOWN: 0/2. ANTM: 0/3; BBCA: 1/1. These tiny counts do not establish precision or statistical significance. Confidence is not measured correctness. No threshold tuning or model promotion is justified by these results.

Production remains unchanged: NO EDGE; historical probabilities remain labelled historical. This work does not improve the reported production accuracy or latency. Genuine improvement still requires richer timestamped intraday evidence across more tickers, synchronized market/sector features, calibration evaluated chronologically, and a future untouched test before promoting a candidate. A delayed feed cannot be made realtime by faster UI refresh.

Reproduce: `python validation/quality/selective_signals.py`. Input: versioned conditional_replay.json and conditional_results.json. Output: selective_results.json. No training, production imports, future-tape substitution or APK changes.
