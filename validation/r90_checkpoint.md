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

## Deployment verified, 24 October 2026 WIB

- PR71 merged to main: a02545e3508eb9e962e0344f7e0fd3367ad4a66c. Main tree matches the tested implementation.
- Final PR validation: 257 tests passed in 2.30s; production contracts, compile, live IHSG, JST end-to-end smoke, and opportunity analysis all passed. Post-merge validate-main also succeeded.
- Production URL: https://ai-stock-intelligence-zijcl2pi2kjweeappbkjiy.streamlit.app/
- Browser observed Time Map R90 with MPXL on 24 October at05:44 WIB and again at11:34 WIB. Source query1.finance.yahoo.com, latest bar23 October16:00 WIB, schedule reference26 October, historical cutoff23 October.
- Local MPXL network timeouts did not reproduce on the production server. Production fetch was90.93ms at11:34 WIB, historical held-out n345, accuracy32.8%, Brier0.685. These are observed metrics, not an accuracy improvement claim.
- In the same browser session, BBCA at11:33 WIB showed its own ticker, source and held-out n361, accuracy38.0%, Brier0.686; switching back to MPXL changed the ticker and evaluation. BBCA server fetch110.84ms. Both baselines remain NO EDGE VS UNIFORM.
- Replay of real Friday BBCA/ANTM candles at an explicitly simulated Friday16:30 clock yielded nine regular-session candles per ticker, excluding lunch. The14:00 candle became available at14:30; closes5050 and2830 respectively. Replay is not a live-market observation.
- Native Android wrapper configuration was reviewed; it opens the same unchanged HTTPS web app with JavaScript/DOM storage. No new APK built and no physical Android/emulator test performed.

- Automatic fragment refresh observed without a Refresh click: MPXL inspection time advanced from11:34:15 to11:35:46 WIB; latest market bar stayed23 October16:00 WIB. Server fetch109.08ms on the refresh. Screenshot: r90-production.jpg.

Remaining limits: the market is closed for the weekend, so active-market auto-refresh/data delivery cannot yet be observed during a trading session. No exhaustive live fetch of all957 tickers was performed. No calibrated predictive edge or guaranteed price direction has been established. Historical baseline remains separate from current-day realized evidence.

Next verification when the market reopens: at09:30 WIB, check that the09:00 candle is completed, the09:30 candle remains FORMING, source timestamps advance, and changing ticker preserves isolation. No code changes are currently pending.
