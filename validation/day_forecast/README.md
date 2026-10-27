# Full-day conditional Time Map: shadow research only

The earlier production map displayed realized movement plus same-clock historical
frequencies. It did not forecast the rest of the day. This candidate supplies a
separate, conditional projection for each still-future regular 30-minute window.
Previous-day state, available opening price and confirmed completed intraday
prefix condition historical analogue weights. Fixed settings are in protocol.json.
There is no target interval close/volume used as an input. No neural training,
new provider, universe scan, model selection or test-set tuning is added.

## Retrospective comparison (not a promotion holdout)

| Metric | Candidate | R90 same-slot baseline |
| --- | ---: | ---: |
| Directional accuracy | 35.81% | 37.30% |
| Balanced accuracy | 37.14% | 38.63% |
| UP precision | 42.24% | 43.52% |
| DOWN precision | 39.27% | 40.18% |
| Multiclass Brier (lower is better) | 0.68294 | 0.67743 |
| ECE | 0.08840 | 0.07698 |

Uniform Brier: 0.66667. The candidate did NOT improve the comparator. Day-block
95% Brier gain vs R90: [-0.01050, -0.00133]. It is NOT enabled as a trading model.
The hard promotion gate stays false. Numerical projections are available only
through an explicit checkbox labelled shadow research; default production
probability cells remain WAIT until validation succeeds. No candidate becomes BUY.

1008 forecast instances concern 456 unique ticker/interval outcomes, not 1008
independent trades. They cover BBCA, BBRI, ANTM, TLKM, AMRT and ACES, on eight dates
15, 16, 19, 20, 21, 22, 23 and 26 October 2026. Issue times: 08:50, 09:30, 13:30 WIB.
Each held-out complete date uses strictly earlier dates for labels. The issue
anchors reuse some outcomes; day-block intervals address clustering by date.
MPXL (17 days) and PTSP (18 days) do not meet the 35-day evaluation coverage rule.
Inputs are previously inspected archives, ideal bar availability, and exclude
trading costs/real execution. No claim of independent prospective superiority.

## Operational behavior

- All supported tickers use the same path; numbers require sufficient own history.
- A pre-market projection can cover the entire day from previous-session data.
- A currently running interval is not filled with a new retrospective prediction.
- A delayed bar is only considered completed once a later provider observation
  reaches its end, not merely because the wall clock has advanced.
- Live-phase missing-day/session, fallback, unknown source, untraded/zero-volume
  evidence, holidays/unverified calendars, insufficient history remain WAIT.
- Opening and completed-prefix changes can change future probabilities. An
  unchanged input hash preserves the original prediction generation time.
- Each prediction stores ticker/date/slot/model source checksum/input hash,
  generated time, input-availability time, historical cutoff and source.
- SQLite uses append-only INSERT OR IGNORE identities. Finished outcomes never
  rewrite forecasts. Accuracy uses the first-issued forecast per interval,
  not whichever later update happened to win. This is paper evaluation only.
- Journal/outcome/watch tables are protected from housekeeping. Existing full
  SQLite checkpoint backup covers them; round-trip tests preserve the journal.
  External cloud restore and physical Android still require separate observation.
- The worker prepares at most 20 recently viewed tickers (last seven days) per
  existing pre-open cycle, from saved bars only. No extra downloads, daily manual
  command, new scheduler, or full-universe scan. Other tickers prepare on demand.
  The screen does not invent a forecast claimed to have existed before first view.
- The old R90 statistical map and realized table remain in collapsed details.

Reproduce from repository root:

    PYTHONPATH=. python validation/day_forecast/evaluate.py
    python -m pytest -q tests/test_day_forecast.py tests/test_trading_monitor.py

results.json includes source checksums, model source checksum, calibration bins,
per-ticker and issue-anchor metrics. replay.json preserves every scored forecast.
The offline timing sample measured a median 62.57 ms for one complete plan on
266 input rows (five repeats). This is not provider or production latency, and
there is no claim that the app became faster.
