# Full-day plan lifecycle

The conditional forecast model and its validation gate are unchanged. This update improves preparation, preservation and inspection of the 09:00-to-close plan; it does not establish higher predictive accuracy.

- Default view: first recorded forecast for each interval. Latest-revision view remains available. Original issue time, feature timestamp, source and revision count are shown separately from the realized return.
- Coverage distinguishes all regular intervals, intervals recorded at any time, and forecasts actually recorded before 09:00 WIB. Missing past intervals are never backfilled.
- Only the requested ticker, trading date and model version are eligible for display. Caller record order cannot change the original forecast.
- Pre-open worker preparation rotates over recently viewed tickers (last seven days), at most 20 per normal worker cycle. Missing-history tickers cannot permanently starve tickers beyond the first batch. This is not a claim that all exchange tickers have enough history or are precomputed at opening.
- A complete pre-open plan for the same date/model is reused. A new date or model requires a new plan. Cache-labelled/unknown source rows are excluded from preparation.
- The preparation cursor is stored in SQLite and protected from housekeeping; it is included by the existing full-database checkpoint path. Actual remote disaster recovery has not been tested by this change.
- Default numeric display remains gated. Explicit experimental viewing does not promote the model or create a BUY signal. The recorded model is still NO EDGE.

Validation: `python -m pytest -q` -> 487 passed, one existing pandas concatenation FutureWarning (14.47 seconds locally). Seven added behavior tests are in `tests/test_full_day_plan.py`, including real Streamlit rendering, reopen persistence, cross-ticker/date/model rejection, no retroactive 09:00 forecast, 22-ticker queue fairness, next-day re-preparation and cached-source rejection.

Measured optimization: seven offline repetitions for one already-complete pre-open ticker using the synthetic history fixture in `tests/test_day_forecast.py`: median 103.345 ms before (`c47cce3:timemap_forecast_ui.py`) and 1.852 ms after. The old path recomputed ten forecasts; the new path checked/recorded zero because the complete plan already existed. This measures avoided work only, excluding network and UI; it does not measure live feed latency or full-app performance.
