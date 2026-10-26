# Expanded Time Map accuracy experiment — 26 October 2026

## Outcome

NO PROMOTION. This is reproducible retrospective research, not an improvement to production accuracy or latency. No new live probabilities, BUY signals, model, Android package or UI changes were deployed.

Twelve fixed tickers were requested for 59 days, two workers and three-second read timeout. All long requests timed out; five-day recovery returned 34 raw bars per ticker, spanning 22–26 October. After lag features and completed prior-day sample rules, each had 13 feature rows across two days. These strict research timeouts do not establish production availability or an SLA.

Bounded seven-day requests for PTSP, MPXL, BBCA and ANTM recovered 136, 162, 232 and 233 raw bars respectively. Attempts and failures are retained. Merge dated archives in order, keeping latest fetched duplicate timestamps; input hashes record every source. PTSP has125 eligible feature rows/17 days and MPXL147/16 days: both excluded from training/testing for insufficient coverage. BBCA, ANTM, BBRI and TLKM each have379 feature rows/40 dates. No gap padding or invented outcomes.

## Fixed chronological experiment

Protocol was written before new collection. Shared lag-only features; remove old BBCA identity flag. First20 dates warmup; subsequent dates before final8 are rolling daily validation. Choose minimum validation Brier among logistic, random forest, histogram boosting and their equal ensemble. Freeze final model weights before test; training ends13 October. Test dates14–23 October,304 outcomes/four tickers. Archives were already inspected in earlier work: this is not an untouched promotion holdout. No synchronized IHSG/sector input or fees/slippage simulation.

Selected random forest: directional accuracy34.87%, balanced34.97%, UP precision42.47%, DOWN precision34.59%, Brier0.670379, calibration error0.05236. Global ticker-frequency baseline accuracy29.61%, balanced33.92%, Brier0.670985. Slot baseline accuracy35.53%, Brier0.685309. The selected model does not beat the slot baseline on accuracy. Paired day-block bootstrap95% Brier improvement versus global baseline: [-0.0102803,0.0115197], crossing zero. This does not establish an edge. Logistic's higher test accuracy39.80% is diagnostic only; choosing it after seeing test would contaminate selection. No production replacement.

## Verification and next action

Three existing leakage/selection research tests passed. Additional artifact checks confirm probability normalization, finite scores, hashes, prior feature timestamps and chronological final training boundary. Full production regression was not repeated: production files and dependencies are unchanged.

Reproduce collection: `AI_ENGINE_WORKER=1 python validation/expanded/run.py`; targeted recovery: `python validation/expanded/recover_history.py`; evaluate archived inputs: `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python validation/expanded/evaluate_recovered.py`. `--frozen` reuses validation selection only when all input hashes match and avoids repeated selection fitting; it does not make the archived test untouched.

Remaining blocker: sufficient contiguous timestamped PTSP/MPXL history and independent prospective multi-ticker evidence, plus a verified feed appropriate for minute-level monitoring. Next action: obtain complete licensed/verifiable30-minute history for the missing ranges, freeze the candidate and calibration policy before collecting new prospective outcomes, then test the fixed model against baselines. Existing delayed Yahoo feed cannot be converted into broker realtime by changing refresh frequency. A new provider account/credential or paid feed was not invented or provisioned.
