# Time Map evidence quality, 25 October 2026

Baseline source: 03c5a735c91843e7dd0a70e6b7b7649555653e2c.

Verified defects: statistical evidence included regular-session start times even when the 30-minute horizon crossed the session end; zero/missing volume could count as flat-price evidence; an imbalanced ticker could receive ABOVE UNIFORM without beating its own historical class frequencies.

Changes: require aligned, positive-volume, full regular-session bars; exclude 15:30 from regular 30-minute statistics (the provider bar can include the closing auction); compare each held-out outcome against a smoothed global class-frequency baseline frozen before that day. Duplicate observations cannot inflate counts. Show evaluation days, balanced accuracy and calibration error. Better descriptive scores remain UNCONFIRMED EDGE and never authorize a live forecast.

IDX schedule reference: https://www.idx.co.id/id/produk/mekanisme-dan-jam-perdagangan (regular session ends15:49:59; preclosing starts15:50).

Archived inputs: ../acceptance/inputs/BBCA.csv and ANTM.csv,26Aug–23Oct2026. comparison.json records before/after diagnostics. Eligible samples change from453 to410; held-out outcomes from365 to330 per ticker, across35 test days. This is a data-validity correction, NOT a like-for-like accuracy improvement or an untouched model-selection holdout. BBCA accuracy38.36→36.97%, ANTM38.08→36.06%. Both still lose to the historical-frequency Brier baseline; NO EDGE remains. No model promoted.

One local timing sample: BBCA62.38→68.01ms; ANTM46.45→90.18ms. Not a controlled speed benchmark; no speed improvement claimed.

Targeted tests:14 passed in0.63s. Local full suite initially blocked during collection by missing streamlit/fastapi dependencies; GitHub CI supplies full requirements and is the merge gate. Tests cover session boundaries, no-trade/unknown-volume exclusion, class imbalance, future/same-day leakage and duplicate sample counts.

No APK changes. Production activation must be observed separately from CI success. Physical Android and future market-session acceptance remain pending.
