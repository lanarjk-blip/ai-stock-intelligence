# Compact Time Map trading monitor

The default Time Map now shows the entire session schedule in six columns, a
current candle card, and the matching Dynamic Opportunity snapshot. This is a
monitoring view, not a promoted forecasting model. Future slots remain NO EDGE.
Historical percentages are never presented as live probabilities.

- app.py retains its existing 90-second fragment and resume guard. Dynamic rows
  are reread on each refresh rather than captured from the outer detail page.
- trading_monitor.py shares the existing ticker-scoped ingestion cache and
  checkpoint engine. There is no universe scan, training or paid provider added.
- Previous-day/session candles, provider fallback, unavailable volume and
  unverified calendars cannot enable a current direction. Candles whose start
  timestamp is more than five minutes old are unsuitable for minute monitoring;
  this is a conservative UI gate, not an estimate of feed delay or an SLA.
- A falling current candle keeps WAIT even when historical UP or an upstream
  opportunity looks bullish. Even aligned signals cannot become BUY: no validated
  live forecast has been demonstrated.
- Dynamic context requires exact ticker, an aware nonfuture timestamp, same day
  and session, <=5 minutes age, and generation at or after the latest candle.
  Reused inputs are not independent confirmations.
- Completed outcomes require today's date, an explicit completed interval time,
  and the original checkpoint clock. Future rows never acquire realized results.
- Full checkpoint history and original chronological validation remain available
  in the collapsed details section. No archived predictions are deleted.

Validation on 26 October 2026: full local regression 334 passed, 0 failed in
65.26 seconds; one existing pandas concatenation FutureWarning. Includes 20 new
monitor cases, ticker/day/session boundaries, provider failure, zero volume,
future bars, PTSP reversal and a real Streamlit AppTest render with a six-column
full-schedule table and collapsed details. Fixtures are synthetic and are not
live-market evidence. CI and production verification must be checked separately.

No claim of accuracy, trading win-rate or speed improvement. Existing research
has not established a trading edge. Paid feed trial remains unapproved. Live
market and physical Android verification are not supplied by these tests.
