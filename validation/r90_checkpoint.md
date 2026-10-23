# R90 Time Map delivery checkpoint

Scope: ticker-scoped intraday ingestion, dated Time Map, Friday schedule, completed-bar evidence, chronological historical validation, and bounded UI refresh. No order execution or strategy promises.

Implemented:
- Direct Yahoo chart adapter validates response symbol, UTC timestamps and OHLC; query2 fallback after empty/error query1; 5-day recovery if 59-day history fails.
- Cache bounded to 64 ticker/date/range keys, TTL 90 seconds, copied results, concurrent same-key refresh coalescing. Failed refresh retains original bar timestamps.
- Historical open-to-close 30-minute probabilities use previous days only, minimum eight observations and fixed Laplace smoothing. Expanding-window held-out accuracy/Brier is shown separately. No heuristic confidence advertised as calibrated probability.
- Current-day candles appear only after their 30-minute window ends. Missing intervals are not filled with preceding bars. Friday 13:30 remains BREAK, 14:00 Session II. The 15:30 provider candle is not treated as available before 16:00.
- Explicit ticker attrs override unrelated widget context; rendered tables are not augmented twice. Day/date, freshness and availability time lead the mobile table.
- Time Map fragment updates every 90 seconds without rerunning the full app; initial computation is reused.

Evidence before deployment (2026-10-23):
- Full regression: 256 passed in 4.44 seconds. After fragment addition: 50 targeted passed in 1.14 seconds.
- Streamlit AppTest of the actual fragment with installed direct/checkpoint wrappers: one 28-row table, explicit TEST ticker, Friday13:30 BREAK; no UI exception. Fixture is synthetic and is not market validation.
- Real BBCA download: 498 bars, latest 2026-10-23 09:00 UTC; cold 5163.40ms, cache hit0.15ms, calculation56.30ms.
- Real ANTM download: 499 bars, latest 2026-10-23 09:00 UTC; cold2090.35ms, cache hit0.15ms, calculation67.06ms.
- BBCA held-out n367, accuracy38.42%, Brier0.68203; ANTM n367, accuracy38.69%, Brier0.67066. Both worse than uniform Brier0.66667. No predictive-accuracy improvement established.
- MPXL long-history and short-history requests timed out from this execution environment; no live data or successful validation claimed for that ticker.
- Android wrapper uses the unchanged production URL, JavaScript and DOM storage enabled. No native code changes. No device/emulator test or new APK claimed.

Next action: publish tested branch, verify GitHub CI, merge authorized PR, verify Time Map R90 on production for multiple symbols. Update deployment evidence after completion. Do not report deployment until visible.
