# R91 production evidence

PR #74 merged as ae15de23aea5032732b4cc500960927cfa6123e3 after all three CI jobs succeeded. Main validation: 278 tests passed, 0 failed, 4.76 seconds.

Production browser observed 2026-10-25 04:11:02 WIB: MPXL, WEEKEND, latest bar 2026-10-23 16:00 WIB, source query1.finance.yahoo.com, HISTORICAL ONLY, live forecast NO EDGE / unavailable. Statistical calculation time shown independently. Screenshot attached. This verifies deployment of the new UI and that Friday bars do not masquerade as Sunday live data.

Automated tests cover date/month/year rollover, provider failure, unchanged/new candle, session isolation, 957-symbol identity with simulated data, and official 2027 holiday parsing. Resume/pageshow JavaScript event behavior passed a simulated DOM check. Physical Android lifecycle and actual Monday/Tuesday bar arrival remain unverified.

R90 estimator retained: BBCA 38.36% directional accuracy (balanced 34.93%), ANTM 38.08% (balanced 36.10%), 365 chronological outcomes each. Brier .6831/.6702 versus uniform .6667: no edge demonstrated. No new live forecasting model or accuracy improvement claimed. Durable forecast ledger/drift monitoring and broad model comparison remain unfinished.

2027 bundle contains 20 official KSEI/IDX dates, parsed from the official PDF. Daily background calendar discovery is implemented; index requests timed out during local verification, so future automatic source discovery is not certified end to end. Unknown years disable live sessions. No new APK was built; backend changes are served through the existing WebView URL. Processing benchmark: BBCA statistical build median 51.15 ms over ten local runs; no matched before/after speed improvement claim.
