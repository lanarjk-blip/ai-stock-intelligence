# Provider recovery and broader evidence — 25 October 2026

Baseline:04e5fc9de86e6ee4ebc49c4c14090036de501e10.

Verified defects and corrections:

- A successful primary HTTP response could carry yesterday's candles and stop failover. During an active regular session, stale candles now trigger the already-bounded secondary-host attempt; the newest valid response is retained. Both hosts belong to Yahoo; this is not an independent feed guarantee.
- Existing cache prevented short-history recovery after a failed long-history request. Five-day recovery now remains available with cache present, except after a429 rate-limit error. Short history remains insufficient for statistical validation; no padding/fabrication.
- An older successful provider response could replace newer same-key cached data and the unequal timestamp could be marked new. Timestamp regression is now rejected; fallback keeps the original fetch time and DATA STALE status. New-candle flags require strictly newer current-session usable data. First-seen previous-day history is never marked new market information.

Targeted behavioral regression:44 passed in1.76s. Compile and diff checks passed. Full GitHub regression is required before merge. No accuracy or latency gain is claimed; conditional fallback may add requests only on failure/staleness. No APK/model replacement.

## Additional real provider attempt

Eight preselected tickers,59-day request,6s read timeout,two workers. MPXL and BMRI failed both long/short requests with timeouts. BBRI475 bars and TLKM474 bars were returned on the long request. GOTO,BRIS,PTBA,JPFA each recovered34 bars from the short request and have no evaluable out-of-sample history. Raw successful snapshots, SHA256 hashes, original provider errors and timestamps are archived here. This is one environment's snapshot, not a production SLA or proof of exchange-wide availability.

| Ticker | Eligible bars | Walk-forward outcomes | Direction accuracy | Balanced accuracy | Brier | Ticker baseline Brier | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| BBRI |392|312|36.54%|36.70%|.6916|.6742|NO EDGE|
| TLKM |392|312|42.95%|42.97%|.6598|.6760|UNCONFIRMED EDGE; HISTORICAL ONLY|

Each has33 evaluation days. This expands descriptive evidence beyond BBCA/ANTM but is not a new-model promotion test. TLKM's lower average Brier alone is not statistical proof of a tradable advantage. No live forecast was enabled. Sunday testing cannot establish Monday/Tuesday live rollover or physical Android behavior.

Reproduce collection (network dependent): `AI_ENGINE_WORKER=1 python validation/reliability/collect_history.py`. Archived inputs can be evaluated offline using timemap_statistics.observations and walk_forward with checked_at from history_manifest.json.
