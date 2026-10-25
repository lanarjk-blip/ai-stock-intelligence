# R91 final acceptance — 25 October 2026

Tested production/main commit: ae15de23aea5032732b4cc500960927cfa6123e3 (PR #74). No production code or APK changes in this acceptance run.

| Test | Result | Evidence / limit |
|---|---|---|
| Friday→Monday; Monday→Tuesday | PASS (simulation) | test_r91_acceptance.py checks actual candle timestamps, rejects previous-day frame, accepts new-day completed candle |
| Session I→II | PASS (simulation) | Fresh provider request on session change; old morning candle is DATA STALE and checkpoint is not fresh |
| Month/year/holiday rollover | PASS (simulation) | Oct30→Nov2; Dec30→Jan4; Dec23→Dec28; verified 2027 holiday Jan5 |
| Open at08:00/09:00/10:00/13:30/14:00/16:30 | PASS (server simulation) | Six clock-controlled scenarios; process cache cleared to simulate fresh session. NOT physical APK lifecycle proof |
| Empty/failing provider, unchanged/new bars, cross-ticker cache | PASS (simulation) | Acceptance plus existing R90/R91 tests |
| All 957 symbol identities | PASS (simulation) | Unique per-symbol provider fixture and bounded cache; not a live provider-coverage claim |
| Actual provider coverage | PENDING | 128 of957 completed before execution network permission cancellation. 82 DATA,44 FETCH_FAILED,2 NO_DATA;829 not attempted |
| Delayed/suspended/new-listing categories | PENDING | One successful result has history older than2026-10-23. Cannot infer suspension or delay from missing data; official category coverage not established |
| Whole regression | PASS | 290 passed,0 failed,4.72s locally; 43 targeted passed,0 failed,1.49s |
| Production UI and scheduled refresh | PASS | MPXL browser:09:18:41→09:20:11 WIB without clicking Refresh. Candle stays2026-10-23 16:00, HISTORICAL ONLY, NO EDGE. Fetch113.82→97.62ms. Search and AI Stock Ask render WAIT |
| Exact deployed SHA attestation | PENDING | GitHub main SHA verified; new R91 UI behavior observed. App does not expose running git SHA, so exact runtime revision is not independently attested |
| Native Android close/resume | PENDING | No physical device/emulator connected; server and browser checks cannot substitute |
| Actual Monday/Tuesday feed transition | PENDING | Market closed. Follow-up automation created for26/27Oct around10:00 WIB |
| Model superiority | FAIL | R90/R91 estimator unchanged; no consistent advantage over expanding global class-frequency baseline. Keep NO EDGE |

## Walk-forward metrics

Data period2026-08-26 through2026-10-23. BBCA498 raw bars;ANTM499.365 held-out chronological outcomes each. Fixed .2% return threshold and minimum8 earlier slot observations. Training uses only strictly earlier dates. These previously inspected data are diagnostics, not a new untouched promotion holdout. Script asserts R90 estimator accuracy/Brier parity.

| Ticker/model | Accuracy | Balanced | PrecisionUP | PrecisionDOWN | Brier | ECE |
|---|---:|---:|---:|---:|---:|---:|
| BBCA R90/R91 |38.36%|34.93%|21.88%|34.86%|.6831|.0760|
| BBCA expanding global |40.55%|33.33%|—|—|.6618|.0254|
| ANTM R90/R91 |38.08%|36.10%|45.30%|36.61%|.6702|.0705|
| ANTM expanding global |37.81%|33.33%|—|—|.6611|.0131|

Uniform Brier=.6667. Full per-model metrics/calibration bins in accuracy.json. Null precision means no predictions of that class, not zero performance. No model promotion, retraining, or accuracy gain claimed.

## Reproduce / resume

`AI_ENGINE_WORKER=1 python -m pytest -q`

`AI_ENGINE_WORKER=1 python validation/acceptance/evaluate.py` uses the archived numerical candle snapshots in inputs/; no live provider request is needed to reproduce the metrics.

Provider per-ticker errors and timestamps are in provider_results.json. Resume provider_probe.py from the saved checkpoint after network access is available; do not count unattempted symbols as failed/no data. Four concurrent requests; stop on429 or a whole batch of transport failures. Short2-second read timeout measures this probe's availability, not production SLA.

Next actions: complete remaining829 providers and verify category coverage, observe market-day transitions, verify physical Android lifecycle, expose/read runtime revision before claiming exact SHA attestation. No additional deployment was necessary for the passing simulation tests.
